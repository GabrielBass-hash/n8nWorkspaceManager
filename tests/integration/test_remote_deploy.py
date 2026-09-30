"""End-to-end remote deployment against a real SSH server (sshd in Docker).

This is the only end-to-end coverage of the whole remote flow:
``install_server`` (probe + generated listener + bare repo), ``publish``
(git push of ``dev:main``), the ``post-receive`` hook (compose up +
``deploy.py``) and the resulting ``last-deploy.json`` marker.

The listener is exercised against a throwaway ``sshd`` container built from
``ssh_server.Dockerfile`` (key-only login; host keys pinned in the named volume
``n8n-launcher-sshd-hostkeys`` so re-runs never trip a host-key change).
Phase A (listener install) runs on any Docker host: neither port 22 nor a
docker socket is required, so a local dev machine exercises it too (see
``ssh_fixture.ssh_server``). Phase B (the full publish) uses the host-networked
sandbox: ``deploy.py`` talks to ``http://127.0.0.1:<port>``, so the hook can only
reach the n8n it just published if it runs in the host network namespace. Host
mode also means sshd answers on the host's own port 22 — exactly what git needs,
since the scp-style server remote carries no port — and requires a docker socket
mounted in so the hook can drive the same daemon as the local n8n workspace.
Both legs therefore live in their own module: two host-networked sandboxes would
fight over port 22.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import DOCKER_COMMAND, SshServer, wait_for_n8n

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import (
    AppConfig,
    DbConfig,
    DbMode,
    GitConfig,
    ServerConfig,
    Workspace,
    WorkspaceState,
)
from n8n_launcher.docker.compose import render_remote_compose, write_compose
from n8n_launcher.git import git_head, git_init, git_remote_url, workspace_branch
from n8n_launcher.n8n.api import N8nApiClient
from n8n_launcher.n8n.owner import OwnerSetup
from n8n_launcher.platform.ports import suggest_port
from n8n_launcher.remote import bare_dir, resolve_base, server_remote_url
from n8n_launcher.remote.ssh import ssh_run
from n8n_launcher.workspaces.manager import WorkspaceManager

pytestmark = pytest.mark.integration


def _workspace(ws_id: str, workflows_dir: Path, port: int) -> Workspace:
    return Workspace(
        id=ws_id,
        name=f"Remote {ws_id}",
        workflows_dir=workflows_dir,
        port=port,
        db=DbConfig(DbMode.NONE),
        git=GitConfig(enabled=True),
    )


def _remote_workflows(server: ServerConfig, email: str, password: str) -> list[dict[str, object]]:
    """List the deployed n8n's workflows from inside the sandbox.

    The remote instance has no public port, so the session login runs where the
    generated script runs: the ``/rest/*`` endpoints authenticate with the
    ``n8n-auth`` cookie, never a bearer token.
    """
    script = (
        "import json, urllib.request\n"
        f"base = 'http://127.0.0.1:{server.n8n_port}'\n"
        "opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())\n"
        f"payload = json.dumps({{'emailOrLdapLoginId': {email!r}, 'password': {password!r}}}).encode()\n"
        "opener.open(urllib.request.Request(base + '/rest/login', data=payload, "
        "headers={'Content-Type': 'application/json'}, method='POST'), timeout=30).read()\n"
        "body = json.loads(opener.open(base + '/rest/workflows?limit=100', timeout=30).read())\n"
        "print(json.dumps(body.get('data', body) if isinstance(body, dict) else body))\n"
    )
    completed = ssh_run(server, "python3 - <<'PY'\n" + script + "PY", timeout=90.0)
    rows = json.loads(completed.stdout)
    return list(rows.get("results", rows)) if isinstance(rows, dict) else list(rows)


def test_install_server_writes_the_listener(ssh_server: SshServer, tmp_path: Path) -> None:
    ws_id = f"it{uuid4().hex[:6]}"
    workflows_dir = tmp_path / "workflows"
    server = ServerConfig(
        enabled=True,
        host="127.0.0.1",
        ssh_port=ssh_server.server.ssh_port,
        user="root",
        key_path=ssh_server.server.key_path,
        n8n_port=suggest_port(requested=5900),
    )
    ws = _workspace(ws_id, workflows_dir, suggest_port(requested=5600))
    ws.server = server
    git_init(workflows_dir, branch=workspace_branch(ws_id))
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "IntegrationPass123!", tmp_path, [ws]))
    manager = WorkspaceManager(store, ssh_server.docker)

    manager.install_server(ws, server)

    base = resolve_base(server, ws_id)
    bare = bare_dir(server, ws_id)
    assert (
        ssh_run(server, f"test -x {shlex.quote(bare)}/hooks/post-receive && echo ok").stdout.strip()
        == "ok"
    )
    assert (
        ssh_run(server, f"test -f {shlex.quote(base)}/deploy.py && echo ok").stdout.strip() == "ok"
    )
    assert (
        ssh_run(
            server, f"git --git-dir={shlex.quote(bare)} rev-parse --is-bare-repository"
        ).stdout.strip()
        == "true"
    )
    hook = ssh_run(server, f"cat {shlex.quote(bare)}/hooks/post-receive").stdout
    assert f"n8n-ws-{ws_id}" in hook
    assert git_remote_url(workflows_dir, "server") == server_remote_url(server, ws_id)


@pytest.mark.timeout(900)
def test_publish_deploys_to_the_server(ssh_server: SshServer, n8n_tunnel, tmp_path_factory) -> None:
    work = tmp_path_factory.mktemp("publish")
    store = ConfigStore(work / "config.db")
    manager = WorkspaceManager(store, ssh_server.docker)

    # --- Local n8n source workspace X (stays running: publish reads it) ---
    x_id = f"lx{uuid4().hex[:6]}"
    x_wf = work / "x-workflows"
    x_wf.mkdir(parents=True, exist_ok=True)
    x_port = suggest_port(requested=5600)
    x = _workspace(x_id, x_wf, x_port)
    compose_x = work / "compose-x.yml"
    write_compose(x, compose_x)
    base_url = f"http://127.0.0.1:{x_port}"

    # --- Remote workspace Y: same source, published to the sshd server ---
    y_id = f"ry{uuid4().hex[:6]}"
    y_wf = work / "y-workflows"
    y_wf.mkdir(parents=True, exist_ok=True)
    # The deployed stack bind-mounts its checkout, so the server side lives in
    # the directory the sandbox publishes from the host (see
    # SshServer.server_root): a Docker Desktop daemon refuses a bind source it
    # only knows inside the container.
    assert ssh_server.server_root is not None
    server = ServerConfig(
        enabled=True,
        host="127.0.0.1",
        ssh_port=ssh_server.server.ssh_port,
        user="root",
        key_path=ssh_server.server.key_path,
        base_dir=str(ssh_server.server_root / y_id),
        n8n_port=suggest_port(requested=5800),
    )
    y = _workspace(y_id, y_wf, x_port)  # Y.port mirrors X so the API factory reaches it
    y.server = server
    y.db = DbConfig(
        mode=DbMode.MANAGED,
        database_name="data",
        username="n8ndata",
        password="RemotePass123",
    )
    git_init(y_wf, branch=workspace_branch(y_id))
    # A local-only migration file rides along in the pushed tree. The real
    # integration assertion below is that deploy.py *never* runs it: the
    # ``data`` database must not exist after a successful publish, because
    # migrations stay on the launcher-managed local stack.
    migrations_dir = y_wf / "db" / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001-remote.sql").write_text(
        "CREATE TABLE remote_only (id int);\n", encoding="utf-8"
    )
    remote_compose = work / "compose-remote.yml"
    remote_compose.write_text(render_remote_compose(y), encoding="utf-8")

    try:
        ssh_server.docker.pull([f"n8nio/n8n:{x.n8n_version}"])
        ssh_server.docker.up(x, compose_x)
        wait_for_n8n(base_url)
        # The hook runs deploy.py inside the sandbox, which must see the n8n
        # Compose publishes on the host through its own loopback.
        n8n_tunnel(server.n8n_port)
        credentials = OwnerSetup(timeout=10.0).bootstrap(
            base_url, "owner@example.test", "IntegrationPass123!"
        )
        api = N8nApiClient(f"{base_url}/api/v1", credentials.api_key, timeout=10.0)
        # A webhook trigger makes the export *activatable*: n8n 2.x refuses to
        # activate a workflow without a webhook/schedule/polling trigger, so a
        # node-less workflow could not prove the deploy activates what it imports.
        api.create_workflow(
            {
                "name": "Publish smoke",
                "nodes": [
                    {
                        "parameters": {"path": "publish-smoke", "httpMethod": "GET"},
                        "id": "webhook",
                        "name": "Webhook",
                        "type": "n8n-nodes-base.webhook",
                        "typeVersion": 2,
                        "position": [0, 0],
                        "webhookId": "publish-smoke",
                    }
                ],
                "connections": {},
                "settings": {},
            }
        )
        assert len(api.list_workflows()) == 1

        # The store must know Y (publish persists server_last_*) with the API
        # key and state required by the export step.
        y.api_key = credentials.api_key
        y.state = WorkspaceState.RUNNING
        store.save(AppConfig("owner@example.test", "IntegrationPass123!", work, [y]))

        manager.install_server(y, server)
        manager.publish(y)

        deployed = store.load().workspaces[0]
        assert deployed.server_last_error is None
        marker = json.loads(deployed.server_last_deploy or "{}")
        assert marker.get("status") == "ok"  # deploy.py wrote it after compose + import
        assert marker.get("sha") == git_head(y_wf)  # the poller matched *this* push
        # No remote database was created: migrations are launcher-local. If the
        # generated deploy ever tried to run db/migrations, the ensure() step
        # would have created ``data`` and the pushed migration would show up.
        psql = ssh_server.docker.exec_psql(
            y,
            remote_compose,
            database="postgres",
            user="n8n",
            check=False,
            stdin="SELECT datname FROM pg_database WHERE datname = 'data';\n",
        )
        assert psql.returncode == 0
        assert "data" not in psql.stdout

        wait_for_n8n(f"http://127.0.0.1:{server.n8n_port}")
        states = ssh_server.docker.list_project_states()
        assert states.get(f"n8n-ws-{y_id}", {}).get("n8n") == "running"
        # The deploy's headline promise: the pushed workflow reached the remote
        # n8n *and* is live. The remote instance is only reachable from the
        # sandbox, so the session login runs there.
        remote_workflows = _remote_workflows(server, "owner@example.test", "IntegrationPass123!")
        assert [item["active"] for item in remote_workflows if item["name"] == "Publish smoke"] == [
            True
        ]
        # The export mirrored at the repo root and in n8nPipelines so the
        # remote deploy hired it.
        assert (y_wf / "n8nPipelines").is_dir()
        assert list((y_wf / "n8nPipelines").glob("*.json"))
    finally:
        subprocess.run(
            [
                DOCKER_COMMAND,
                "compose",
                "-p",
                f"n8n-ws-{y_id}",
                "-f",
                str(remote_compose),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            capture_output=True,
            timeout=120.0,
        )
        ssh_server.docker.down(x, compose_x, remove_orphans=True)
        subprocess.run(
            [DOCKER_COMMAND, "volume", "rm", "-f", f"n8ndata-{x_id}"],
            capture_output=True,
            timeout=60.0,
        )
