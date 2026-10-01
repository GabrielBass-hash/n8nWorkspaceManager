"""Validate the remote execution-status contract against a real n8n.

``remote_execution_status`` is a thin SSH wrapper, but everything it trusts
lives in the *generated* ``deploy.py``: the session-cookie login, the shape of
``GET /rest/executions`` and the whitelist that turns a raw payload into
:class:`RemoteExecution`. A unit test can only prove the wrapper, so this
module runs the real thing: a real n8n container on the host, a real
execution triggered through the internal REST API, and the generated script
queried over SSH from the host-networked sshd sandbox — the only place where
``http://127.0.0.1:<port>`` means the deployed n8n.

``conftest.n8n_tunnel`` gives the sandbox the loopback view of the deployed n8n
that the generated script assumes.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict
from uuid import uuid4

import pytest
import requests
from conftest import DOCKER_COMMAND, SshServer, wait_for_n8n

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, DbConfig, DbMode, GitConfig, ServerConfig, Workspace
from n8n_launcher.docker.compose import write_compose
from n8n_launcher.git import git_init, workspace_branch
from n8n_launcher.n8n.api import N8nApiClient
from n8n_launcher.n8n.owner import OwnerSetup
from n8n_launcher.platform.ports import suggest_port
from n8n_launcher.remote import build_secrets_document, remote_execution_status
from n8n_launcher.remote.ssh import chmod_remote, write_remote_file
from n8n_launcher.workspaces.manager import WorkspaceManager

pytestmark = pytest.mark.integration

_OWNER_EMAIL = "owner@example.test"
_OWNER_PASSWORD = "IntegrationPass123!"
_WORKFLOW_NAME = "Remote status probe"
_TRIGGER_NODE_NAME = "When clicking 'Execute workflow'"


def _manual_trigger_node() -> dict[str, object]:
    """Return a single manual-trigger node, the only shape a pin-less run needs."""
    return {
        "parameters": {},
        "id": "manual-trigger",
        "name": _TRIGGER_NODE_NAME,
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [0, 0],
    }


def _trigger_and_wait(base_url: str, workflow_id: str, *, timeout: float = 90.0) -> str:
    """Run *workflow_id* through the internal REST API and return its execution id.

    The internal ``/rest/*`` endpoints authenticate with the ``n8n-auth``
    session cookie, not a bearer token, so a plain session login is the only
    way to produce a real execution from outside.
    """
    session = requests.Session()
    login = session.post(
        f"{base_url}/rest/login",
        json={"emailOrLdapLoginId": _OWNER_EMAIL, "password": _OWNER_PASSWORD},
        timeout=15.0,
    )
    assert login.status_code == 200, login.text
    # Same ManualRunDto shape the generated CI runner sends for a manual
    # trigger: n8n 2.x refuses a run without an explicit start point.
    run = session.post(
        f"{base_url}/rest/workflows/{workflow_id}/run",
        json={"triggerToStartFrom": {"name": _TRIGGER_NODE_NAME}},
        timeout=30.0,
    )
    assert run.status_code < 400, run.text
    body = run.json()
    execution_id = body.get("executionId") or (body.get("data") or {}).get("executionId")
    assert execution_id, run.text

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = session.get(f"{base_url}/rest/executions/{execution_id}", timeout=10.0)
        if detail.status_code == 200:
            payload = detail.json()
            data = payload.get("data", payload)
            if data.get("finished"):
                assert data.get("status") == "success", detail.text
                return str(execution_id)
        time.sleep(1.0)
    raise AssertionError("the triggered execution never finished")


@pytest.mark.timeout(900)
def test_remote_execution_status_reports_a_real_execution(
    ssh_server: SshServer, n8n_tunnel, tmp_path_factory
) -> None:
    """A real n8n execution must survive the SSH hop, redaction and parsing."""
    docker = ssh_server.docker
    work = tmp_path_factory.mktemp("status")
    store = ConfigStore(work / "config.db")
    manager = WorkspaceManager(store, docker)

    ws_id = f"st{uuid4().hex[:6]}"
    workflows_dir = work / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    # The status command queries http://127.0.0.1:<n8n_port> from inside the
    # sandbox, so the port Compose publishes here is the one it must be told
    # about, reached through the reverse tunnel.
    port = suggest_port(requested=5600)
    server = ServerConfig(
        enabled=True,
        host="127.0.0.1",
        ssh_port=ssh_server.server.ssh_port,
        user="root",
        key_path=ssh_server.server.key_path,
        n8n_port=port,
    )
    ws = Workspace(
        id=ws_id,
        name=f"Status {ws_id}",
        workflows_dir=workflows_dir,
        port=port,
        db=DbConfig(DbMode.NONE),
        git=GitConfig(enabled=True),
    )
    ws.server = server
    git_init(workflows_dir, branch=workspace_branch(ws_id))
    store.save(AppConfig(_OWNER_EMAIL, _OWNER_PASSWORD, work, [ws]))

    compose_file = work / "compose.yml"
    write_compose(ws, compose_file)
    base_url = f"http://127.0.0.1:{port}"
    try:
        docker.pull([f"n8nio/n8n:{ws.n8n_version}"])
        docker.up(ws, compose_file)
        wait_for_n8n(base_url)
        credentials = OwnerSetup(timeout=10.0).bootstrap(base_url, _OWNER_EMAIL, _OWNER_PASSWORD)
        api = N8nApiClient(f"{base_url}/api/v1", credentials.api_key, timeout=10.0)
        created = api.create_workflow(
            {
                "name": _WORKFLOW_NAME,
                "nodes": [_manual_trigger_node()],
                "connections": {},
                "settings": {},
            }
        )
        first_execution_id = _trigger_and_wait(base_url, created["id"])
        # A second run proves the list, not just the first item, survives the
        # hop (the limit and the per-item whitelist both apply to every entry).
        second_execution_id = _trigger_and_wait(base_url, created["id"])
        assert second_execution_id != first_execution_id
        n8n_tunnel(port)

        # Same server-side state a publish leaves behind: generated script +
        # hook + the owner secrets it authenticates with.
        manager.install_server(ws, server)
        document = build_secrets_document(_OWNER_EMAIL, _OWNER_PASSWORD, {})
        write_remote_file(
            server,
            manager._secrets_remote_path(ws),
            json.dumps(document, indent=2, ensure_ascii=False),
        )
        chmod_remote(server, manager._secrets_remote_path(ws), mode="600")

        status = remote_execution_status(server, ws_id, limit=10)
        assert status.supported is True, status.error
        assert status.executions, "the deployed n8n reported no execution at all"
        reported = {item.execution_id for item in status.executions}
        assert {first_execution_id, second_execution_id} <= reported, sorted(reported)
        found = next(
            (item for item in status.executions if item.execution_id == first_execution_id), None
        )
        assert found is not None, sorted(reported)
        assert found.status == "success"
        assert found.workflow_name == _WORKFLOW_NAME
        # n8n 2.40 sends no ``finished`` flag: the launcher derives it from the
        # status, which is why a real terminal run still reports True.
        assert found.finished is True
        assert found.started_at

        # Read-only contract: nothing secret rides along with the summaries.
        serialized = json.dumps([asdict(item) for item in status.executions])
        assert _OWNER_PASSWORD not in serialized
        assert credentials.api_key not in serialized
    finally:
        subprocess.run(
            [
                DOCKER_COMMAND,
                "compose",
                "-p",
                f"n8n-ws-{ws_id}",
                "-f",
                str(compose_file),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            capture_output=True,
            timeout=120.0,
        )
        subprocess.run(
            [DOCKER_COMMAND, "volume", "rm", "-f", f"n8ndata-{ws_id}"],
            capture_output=True,
            timeout=60.0,
        )
