"""End-to-end remote deployment against a real SSH server (sshd in Docker).

This is the only end-to-end coverage of the whole remote flow:
``install_server`` (probe + generated listener + bare repo), ``publish``
(git push of ``dev:main``), the ``post-receive`` hook (compose up +
``deploy.py``) and the resulting ``last-deploy.json`` marker.

The listener is exercised against a throwaway ``sshd`` container built from
``ssh_server.Dockerfile`` (key-only login; host keys pinned in the named volume
``n8n-launcher-sshd-hostkeys`` so re-runs never trip a host-key change).
Phase A (listener install) runs on any Docker host: neither port 22 nor a
docker socket is required, so a local dev machine exercises it too. Phase B
(the full publish) additionally needs the sshd to answer on the host's own
port 22 — git pushes to the scp-style server URL that carries no port — and a
docker socket mounted into the container so the hook can drive the same daemon
that runs the local n8n source workspace. It is gated to a free port 22 with a
socket present, i.e. basically the Linux CI runner; a local dev can opt in via
``N8N_LAUNCHER_TEST_DOCKER_SOCKET`` (Docker Desktop/Colima), though Docker
Desktop's file-sharing sandbox will still refuse the server's ``/root``-based
bind mounts, so the Linux runner remains the definitive Phase B gate.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import requests

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
from n8n_launcher.docker.manager import DockerManager, resolve_docker_command
from n8n_launcher.git import git_head, git_init, git_remote_url, workspace_branch
from n8n_launcher.n8n.api import N8nApiClient
from n8n_launcher.n8n.owner import OwnerSetup
from n8n_launcher.platform.ports import is_port_available, suggest_port
from n8n_launcher.remote import bare_dir, resolve_base, server_remote_url
from n8n_launcher.remote.ssh import SshError, ssh_run
from n8n_launcher.workspaces.manager import WorkspaceManager

pytestmark = pytest.mark.integration

_SSHD_IMAGE = "n8n-launcher-sshd:test"
_DOCKERFILE = Path(__file__).with_name("ssh_server.Dockerfile")
# Persistent sshd host keys (see ssh_server.Dockerfile): macOS's ssh resolves
# ~/.ssh through the passwd database, not $HOME, so a fresh container per run
# would trip "REMOTE HOST IDENTIFICATION HAS CHANGED" against a known_hosts
# that was accepted on a previous run. A named volume pins the keys.
_SSHD_KEYS_VOLUME = "n8n-launcher-sshd-hostkeys"
# mac-GUI/apps and Linux runners can expose docker outside PATH; reuse the same
# resolver the launcher itself uses so integration never depends on a bare
# ``docker`` being on PATH.
_DOCKER_COMMAND = resolve_docker_command()
# Docker Desktop/Colima keep the daemon socket off /var/run/docker.sock; the
# env override lets a local dev run the full publish leg on such hosts.
_DOCKER_SOCKET = os.environ.get("N8N_LAUNCHER_TEST_DOCKER_SOCKET", "/var/run/docker.sock")


@dataclass
class SshServer:
    """A running sshd sandbox plus the ServerConfig that reaches it."""

    server: ServerConfig
    key_path: Path
    publish_capable: bool
    docker: DockerManager
    container: str


def _wait_for_n8n(base_url: str, timeout: float = 240.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/healthz", timeout=2.0)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2.0)
    raise AssertionError(f"n8n did not become healthy at {base_url}")


def _generate_keypair(directory: Path) -> Path:
    key = directory / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        timeout=30.0,
    )
    return key


def _random_host_port() -> int:
    for candidate in range(22220, 22400):
        if is_port_available(candidate):
            return candidate
    raise AssertionError("no free host port found for the sshd fixture")


def _port_listening(port: int) -> bool:
    """Return True when something *listens* on ``127.0.0.1:port``.

    A connect probe is used instead of a bind check because port 22 is
    privileged: an unprivileged process cannot bind it even when it is free,
    while docker-proxy (root) publishing ``127.0.0.1:22:22`` needs no such
    permission.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _build_sshd_image() -> None:
    # DOCKER_HOST is pinned by the _isolate_ssh_home fixture, so the legacy
    # builder used by `docker build` reaches the active daemon.
    subprocess.run(
        [
            _DOCKER_COMMAND,
            "build",
            "-q",
            "-t",
            _SSHD_IMAGE,
            "-f",
            str(_DOCKERFILE),
            str(_DOCKERFILE.parent),
        ],
        check=True,
        timeout=300.0,
    )


@pytest.fixture(scope="module")
def ssh_server(docker_manager, tmp_path_factory: pytest.TempPathFactory) -> SshServer:
    """Start a key-only sshd container with persistent sshd host keys."""
    work = tmp_path_factory.mktemp("sshd")
    keys = _generate_keypair(work)
    pubkey = (keys.parent / f"{keys.name}.pub").read_text(encoding="ascii").strip()

    _build_sshd_image()
    subprocess.run(
        [_DOCKER_COMMAND, "volume", "create", _SSHD_KEYS_VOLUME],
        check=True,
        capture_output=True,
        timeout=30.0,
    )

    container = f"n8n-launcher-sshd-{uuid4().hex[:6]}"
    # Git pushes to the scp-style server remote carry no port, so the full
    # publish leg needs sshd reachable on the host's own port 22. Only when
    # that holds (and a docker socket exists) do we mount it for the hook.
    publish_capable = (
        (sys.platform == "linux" or _DOCKER_SOCKET != "/var/run/docker.sock")
        and not _port_listening(22)
        and Path(_DOCKER_SOCKET).exists()
    )
    host_port = 22 if publish_capable else _random_host_port()

    run_args = [
        _DOCKER_COMMAND,
        "run",
        "-d",
        "--name",
        container,
        "-p",
        f"127.0.0.1:{host_port}:22",
        "-e",
        f"AUTHORIZED_KEYS={pubkey}",
        "-v",
        f"{_SSHD_KEYS_VOLUME}:/etc/ssh/hostkeys",
    ]
    if publish_capable:
        # The container's docker CLI reaches the daemon through its default
        # /var/run/docker.sock, whatever the host-side socket path is.
        run_args += ["-v", f"{_DOCKER_SOCKET}:/var/run/docker.sock"]
    run_args += [_SSHD_IMAGE]

    sshd_started = False
    try:
        subprocess.run(run_args, check=True, timeout=120.0)

        server = ServerConfig(
            enabled=True,
            host="127.0.0.1",
            ssh_port=host_port,
            user="root",
            key_path=str(keys),
        )
        deadline = time.monotonic() + 60.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ssh_run(server, "true", timeout=5.0)
                sshd_started = True
                break
            except SshError as exc:
                last_error = exc
                time.sleep(0.5)
        if not sshd_started:
            raise AssertionError(f"sshd never became reachable: {last_error}")
        yield SshServer(server, keys, publish_capable, docker_manager, container)
    finally:
        # The sshd host keys persist in _SSHD_KEYS_VOLUME (deliberately not
        # removed); only the container goes away.
        subprocess.run([_DOCKER_COMMAND, "rm", "-f", container], capture_output=True, timeout=60.0)


@pytest.fixture(scope="module", autouse=True)
def _isolate_ssh_home(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point HOME at a temp dir so ssh/git write known_hosts there, not the user's.

    The docker CLI resolves its socket through the per-user context (Docker
    Desktop), so the active context's endpoint is captured *before* the HOME
    override and re-exported as ``DOCKER_HOST`` for the module's lifetime —
    otherwise docker would silently fall back to ``/var/run/docker.sock``.
    """
    previous_home = os.environ.get("HOME")
    probe_env = dict(os.environ)
    if previous_home:
        probe_env["HOME"] = previous_home
    docker_host = subprocess.run(
        [_DOCKER_COMMAND, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        env=probe_env,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    ).stdout.strip()
    previous_docker_host = os.environ.get("DOCKER_HOST")

    home = tmp_path_factory.mktemp("home")
    os.environ["HOME"] = str(home)
    if docker_host:
        os.environ["DOCKER_HOST"] = docker_host
    yield
    os.environ.pop("DOCKER_HOST", None)
    if previous_docker_host is not None:
        os.environ["DOCKER_HOST"] = previous_docker_host
    if previous_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = previous_home


def _workspace(ws_id: str, workflows_dir: Path, port: int) -> Workspace:
    return Workspace(
        id=ws_id,
        name=f"Remote {ws_id}",
        workflows_dir=workflows_dir,
        port=port,
        db=DbConfig(DbMode.NONE),
        git=GitConfig(enabled=True),
    )


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


@pytest.mark.timeout(600)
def test_publish_deploys_to_the_server(ssh_server: SshServer, tmp_path_factory) -> None:
    if not ssh_server.publish_capable:
        pytest.skip("host port 22 / docker socket are required for the git-over-SSH publish")

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
    server = ServerConfig(
        enabled=True,
        host="127.0.0.1",
        ssh_port=ssh_server.server.ssh_port,
        user="root",
        key_path=ssh_server.server.key_path,
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
        _wait_for_n8n(base_url)
        credentials = OwnerSetup(timeout=10.0).bootstrap(
            base_url, "owner@example.test", "IntegrationPass123!"
        )
        api = N8nApiClient(f"{base_url}/api/v1", credentials.api_key, timeout=10.0)
        api.create_workflow(
            {"name": "Publish smoke", "nodes": [], "connections": {}, "settings": {}}
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

        _wait_for_n8n(f"http://127.0.0.1:{server.n8n_port}")
        states = ssh_server.docker.list_project_states()
        assert states.get(f"n8n-ws-{y_id}", {}).get("n8n") == "running"
        # The export mirrored at the repo root and in n8nPipelines so the
        # remote deploy hired it.
        assert (y_wf / "n8nPipelines").is_dir()
        assert list((y_wf / "n8nPipelines").glob("*.json"))
    finally:
        subprocess.run(
            [
                _DOCKER_COMMAND,
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
            [_DOCKER_COMMAND, "volume", "rm", "-f", f"n8ndata-{x_id}"],
            capture_output=True,
            timeout=60.0,
        )
