"""Integration fixtures: a real Docker daemon plus throwaway remote servers.

The remote legs share one key-only sshd container (``ssh_server.Dockerfile``)
published on a random high port, so a local dev machine and CI exercise the
same setup without privileged ports.

The generated server-side code talks to the n8n Compose published on the *host*
through ``http://127.0.0.1:<port>``, which a bridge-networked sandbox cannot
reach: its loopback is its own. ``n8n_tunnel`` closes that gap the way a real
server does not need to — a reverse SSH tunnel opened by the host, so the
sandbox's ``127.0.0.1:<port>`` lands on the host's published n8n. (Host
networking is not an alternative: Docker Desktop's host mode lives in the VM, so
the host then cannot reach the sandbox's sshd at all.)
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from n8n_launcher.core.models import ServerConfig
from n8n_launcher.docker.manager import DockerManager, resolve_docker_command
from n8n_launcher.platform.ports import is_port_available
from n8n_launcher.remote.ssh import SshError, ssh_run

SSHD_IMAGE = "n8n-launcher-sshd:test"
DOCKERFILE = Path(__file__).with_name("ssh_server.Dockerfile")
SSHD_KEYS_VOLUME = "n8n-launcher-sshd-hostkeys"
# mac-GUI/apps and Linux runners can expose docker outside PATH; reuse the same
# resolver the launcher itself uses so integration never depends on a bare
# ``docker`` being on PATH.
DOCKER_COMMAND = resolve_docker_command()
# Docker Desktop/Colima keep the daemon socket off /var/run/docker.sock; the
# env override lets a local dev run the full publish leg on such hosts.
DOCKER_SOCKET = os.environ.get("N8N_LAUNCHER_TEST_DOCKER_SOCKET", "/var/run/docker.sock")


@dataclass
class SshServer:
    """A running sshd sandbox plus the ServerConfig that reaches it."""

    server: ServerConfig
    key_path: Path
    docker: DockerManager
    container: str
    server_root: Path | None = None
    """Host directory published at the *same* path inside the sandbox.

    The deployed stack bind-mounts its checkout (``./:/workflows``), and a
    Docker Desktop daemon only accepts bind sources it knows from the host: a
    path that exists solely in the container's filesystem (``$HOME``) is
    refused with "Mounts denied". Publishing a host directory at an identical
    path inside the sandbox lets the full publish run with an absolute
    ``base_dir`` on every platform, Linux CI included. ``None`` when the
    fixture could not publish one.
    """


def wait_for_n8n(base_url: str, timeout: float = 240.0) -> None:
    """Block until the n8n health endpoint answers 200 on ``base_url``."""
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


def generate_keypair(directory: Path) -> Path:
    """Create an ed25519 keypair for the sandbox and return the private half."""
    key = directory / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        timeout=30.0,
    )
    return key


def random_host_port() -> int:
    """Return a free high port for the bridge-networked sandbox."""
    for candidate in range(22220, 22400):
        if is_port_available(candidate):
            return candidate
    raise AssertionError("no free host port found for the sshd fixture")


def port_listening(port: int) -> bool:
    """Return True when something *listens* on ``127.0.0.1:port``.

    A connect probe is used instead of a bind check because port 22 is
    privileged: an unprivileged process cannot bind it even when it is free,
    while a listener — including the host-networked sandbox — needs no such
    permission from us.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


def build_sshd_image() -> None:
    """Build the throwaway sshd image from the sibling Dockerfile."""
    # DOCKER_HOST is pinned by _isolated_docker_home, so the legacy builder
    # used by `docker build` reaches the active daemon.
    subprocess.run(
        [
            DOCKER_COMMAND,
            "build",
            "-q",
            "-t",
            SSHD_IMAGE,
            "-f",
            str(DOCKERFILE),
            str(DOCKERFILE.parent),
        ],
        check=True,
        timeout=300.0,
    )


def docker_socket_available() -> bool:
    """Return True when the host-side docker socket the hook needs exists."""
    return Path(DOCKER_SOCKET).exists()


@contextmanager
def _isolated_docker_home(home: Path) -> Iterator[None]:
    """Point HOME at *home* for the duration, keeping the docker endpoint.

    ssh/git must write known_hosts in a throwaway HOME, not the developer's.
    Two docker knobs are captured *before* the HOME override, because a bare
    HOME breaks the CLI in two different ways:

    * the socket comes from the active context, so it is re-exported as
      ``DOCKER_HOST`` (otherwise docker silently falls back to
      ``/var/run/docker.sock``);
    * Docker Desktop installs the ``compose`` plugin under the user's
      ``.docker`` directory, so without ``DOCKER_CONFIG`` pointing back there
      ``docker compose -p …`` is parsed as the top-level CLI and dies with
      "unknown shorthand flag: 'p'".
    """
    previous_home = os.environ.get("HOME")
    previous_docker_host = os.environ.get("DOCKER_HOST")
    previous_docker_config = os.environ.get("DOCKER_CONFIG")
    probe_env = dict(os.environ)
    if previous_home:
        probe_env["HOME"] = previous_home
    docker_host = subprocess.run(
        [DOCKER_COMMAND, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        env=probe_env,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    ).stdout.strip()
    docker_config = subprocess.run(
        [DOCKER_COMMAND, "context", "inspect", "--format", "{{.Name}}"],
        env=probe_env,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    ).stdout.strip()
    os.environ["HOME"] = str(home)
    if docker_host:
        os.environ["DOCKER_HOST"] = docker_host
    if docker_config and previous_home:
        os.environ["DOCKER_CONFIG"] = str(Path(previous_home) / ".docker")
    try:
        yield
    finally:
        os.environ.pop("DOCKER_HOST", None)
        if previous_docker_host is not None:
            os.environ["DOCKER_HOST"] = previous_docker_host
        os.environ.pop("DOCKER_CONFIG", None)
        if previous_docker_config is not None:
            os.environ["DOCKER_CONFIG"] = previous_docker_config
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home


def _run_sshd(
    key: Path,
    pubkey: str,
    *,
    host_port: int,
    mount_socket: bool,
    server_root: Path | None = None,
) -> SshServer:
    """Start the sandbox, then wait until key-only SSH answers on *host_port*."""
    build_sshd_image()
    subprocess.run(
        [DOCKER_COMMAND, "volume", "create", SSHD_KEYS_VOLUME],
        check=True,
        capture_output=True,
        timeout=30.0,
    )
    container = f"n8n-launcher-sshd-{uuid4().hex[:6]}"
    args = [
        DOCKER_COMMAND,
        "run",
        "-d",
        "--name",
        container,
        "-e",
        f"AUTHORIZED_KEYS={pubkey}",
        "-v",
        f"{SSHD_KEYS_VOLUME}:/etc/ssh/hostkeys",
    ]
    args += ["-p", f"127.0.0.1:{host_port}:22"]
    if mount_socket:
        # The container's docker CLI reaches the daemon through its default
        # /var/run/docker.sock, whatever the host-side socket path is.
        args += ["-v", f"{DOCKER_SOCKET}:/var/run/docker.sock"]
    if server_root is not None:
        # Same path on both sides: the daemon resolves a compose bind source on
        # the host, so the checkout path in the compose file must be one the
        # host knows (see SshServer.server_root).
        args += ["-v", f"{server_root}:{server_root}"]
    args += [SSHD_IMAGE]
    subprocess.run(args, check=True, timeout=120.0)

    server = ServerConfig(
        enabled=True,
        host="127.0.0.1",
        ssh_port=host_port,
        user="root",
        key_path=str(key),
    )
    deadline = time.monotonic() + 60.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ssh_run(server, "true", timeout=5.0)
            return SshServer(
                server=server,
                key_path=key,
                docker=DockerManager(command=DOCKER_COMMAND, timeout=180.0),
                container=container,
                server_root=server_root,
            )
        except SshError as exc:
            last_error = exc
            time.sleep(0.5)
    subprocess.run([DOCKER_COMMAND, "rm", "-f", container], capture_output=True, timeout=60.0)
    raise AssertionError(f"sshd never became reachable: {last_error}")


@pytest.fixture(scope="session")
def docker_manager() -> DockerManager:
    """Session-wide Docker wrapper; skips the run when no daemon answers."""
    manager = DockerManager(command=DOCKER_COMMAND, timeout=180.0)
    status = manager.check_available()
    if not status.available:
        pytest.skip(f"Docker is not available: {status.message}")
    return manager


@pytest.fixture(scope="module")
def ssh_server(
    docker_manager: DockerManager, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[SshServer]:
    """Bridge-networked sshd on a random high port, with a throwaway HOME."""
    work = tmp_path_factory.mktemp("sshd")
    server_root = work / "server-root"
    server_root.mkdir()
    with _isolated_docker_home(work / "home"):
        key = generate_keypair(work)
        pubkey = (work / f"{key.name}.pub").read_text(encoding="ascii").strip()
        sandbox: SshServer | None = None
        try:
            sandbox = _run_sshd(
                key,
                pubkey,
                host_port=random_host_port(),
                mount_socket=True,
                server_root=server_root,
            )
            sandbox.docker = docker_manager
            yield sandbox
        finally:
            if sandbox is not None:
                subprocess.run(
                    [DOCKER_COMMAND, "rm", "-f", sandbox.container],
                    capture_output=True,
                    timeout=60.0,
                )


def _port_answers(ssh_server: SshServer, port: int) -> bool:
    """Return True when a plain TCP connect to ``127.0.0.1:port`` succeeds in the sandbox."""
    probe = (
        f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}), 2).close()\""
    )
    result = ssh_run(ssh_server.server, probe, timeout=10.0, check=False)
    return result.returncode == 0


@pytest.fixture
def n8n_tunnel(ssh_server: SshServer) -> Iterator[Callable[[int], None]]:
    """Return a factory opening a reverse tunnel for the sandbox's n8n port.

    ``open_tunnel(n8n_port)`` makes ``127.0.0.1:n8n_port`` *inside the sandbox*
    reach the host's own published n8n — exactly what the generated
    ``deploy.py`` assumes about a real server. The tunnel is torn down with the
    test.
    """
    tunnels: list[subprocess.Popen[bytes]] = []

    def open_tunnel(n8n_port: int, *, timeout: float = 20.0) -> None:
        process = subprocess.Popen(
            [
                "ssh",
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=5",
                "-i",
                str(ssh_server.key_path),
                "-p",
                str(ssh_server.server.ssh_port),
                "-R",
                f"{n8n_port}:127.0.0.1:{n8n_port}",
                f"{ssh_server.server.user}@{ssh_server.server.host}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tunnels.append(process)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = (process.stderr.read() if process.stderr else b"").decode().strip()
                raise AssertionError(f"reverse tunnel to port {n8n_port} failed: {detail}")
            if _port_answers(ssh_server, n8n_port):
                return
            time.sleep(0.5)
        raise AssertionError(f"reverse tunnel to port {n8n_port} never came up")

    try:
        yield open_tunnel
    finally:
        for process in tunnels:
            process.terminate()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
