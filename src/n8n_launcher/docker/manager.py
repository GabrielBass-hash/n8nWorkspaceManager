"""Docker Compose lifecycle management."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..core.models import Workspace
from .compose import compose_project_name

MACOS_DOCKER_SEARCH_DIRS = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/Applications/Docker.app/Contents/Resources/bin",
]


def resolve_docker_command() -> str:
    """Return an absolute path to a usable ``docker`` CLI.

    GUI launch services (Finder, Dock, Launchpad) start applications with a
    minimal ``PATH`` (``/usr/bin:/bin:/usr/sbin:/sbin``), so ``docker``
    installed via Homebrew or Docker Desktop is not discoverable by bare name.
    Probe the standard install locations first, then fall back to ``PATH``
    lookup and finally to the bare command so error reporting stays friendly.
    """
    if platform.system() == "Darwin":
        for directory in MACOS_DOCKER_SEARCH_DIRS:
            candidate = Path(directory) / "docker"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    resolved = shutil.which("docker")
    return resolved or "docker"


class DockerError(RuntimeError):
    """Raised when Docker or Compose cannot complete an operation."""


@dataclass(frozen=True)
class DockerStatus:
    available: bool
    message: str


@dataclass(frozen=True)
class ComposeStatus:
    raw_output: str
    returncode: int


def parse_compose_status(raw: str) -> dict[str, str]:
    """Map compose service names to their Docker container state.

    ``docker compose ps --format json`` emits one JSON object per line with
    ``{"Service": "...", "State": "..."}`` fields.
    """
    services: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        service = row.get("Service")
        state = row.get("State")
        if service and isinstance(state, str):
            services[str(service)] = state
    return services


class DockerManager:
    """Thin subprocess wrapper around ``docker compose`` lifecycle commands."""

    def __init__(self, command: str = "docker", timeout: float = 180.0) -> None:
        self.command = command
        self.timeout = timeout

    def check_available(self) -> DockerStatus:
        """Probe ``docker info`` and report daemon availability."""
        try:
            result = self._run([self.command, "info"], timeout=5.0, check=False)
        except OSError as exc:
            return DockerStatus(False, f"Docker is not installed or is not executable: {exc}")
        if result.returncode != 0:
            message = result.stderr.strip() or "Docker daemon is not accessible"
            return DockerStatus(False, message)
        return DockerStatus(True, "Docker daemon is accessible")

    def pull(
        self,
        images: list[str],
        on_output: Callable[[str], None] | None = None,
    ) -> None:
        """Pull images one at a time, raising :class:`DockerError` on failure."""
        for image in images:
            result = self._run([self.command, "pull", image], check=False)
            if on_output:
                for line in result.stdout.splitlines():
                    on_output(line)
            if result.returncode != 0:
                raise DockerError(f"Docker pull failed for {image}: {result.stderr.strip()}")

    def up(self, workspace: Workspace, compose_file: Path) -> None:
        """Bring the workspace stack up detached, removing orphans."""
        self._compose(workspace, compose_file, "up", "-d", "--remove-orphans")

    def down(self, workspace: Workspace, compose_file: Path, remove_orphans: bool = False) -> None:
        """Tear the workspace stack down, optionally removing orphans."""
        args = ["down"]
        if remove_orphans:
            args.append("--remove-orphans")
        self._compose(workspace, compose_file, *args)

    def status(self, workspace: Workspace, compose_file: Path) -> ComposeStatus:
        """Return raw ``docker compose ps --format json`` output and exit code."""
        result = self._compose(workspace, compose_file, "ps", "--format", "json", check=False)
        return ComposeStatus(result.stdout, result.returncode)

    def logs(self, workspace: Workspace, compose_file: Path, service: str | None = None) -> str:
        """Return container logs for one service (or the whole project)."""
        args = ["logs", "--no-color"]
        if service:
            args.append(service)
        result = self._compose(workspace, compose_file, *args)
        return result.stdout

    def exec_psql(
        self,
        workspace: Workspace,
        compose_file: Path,
        *,
        database: str,
        user: str,
        password: str = "",
        stdin: str = "",
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a psql command inside the workspace's Postgres service."""
        command = [
            self.command,
            "compose",
            "-p",
            compose_project_name(workspace),
            "-f",
            str(compose_file),
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={password}",
            "postgres",
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
        ]
        return self._run(command, input=stdin, check=check)

    def _compose(
        self,
        workspace: Workspace,
        compose_file: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            self.command,
            "compose",
            "-p",
            compose_project_name(workspace),
            "-f",
            str(compose_file),
            *args,
        ]
        return self._run(command, check=check)

    def _run(
        self,
        command: Sequence[str],
        *,
        input: str = "",
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                input=input,
                timeout=self.timeout if timeout is None else timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "strerror", None) or str(exc)
            raise DockerError(f"Docker command failed: {' '.join(command)} ({detail})") from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise DockerError(f"Docker command failed ({result.returncode}): {detail}")
        return result
