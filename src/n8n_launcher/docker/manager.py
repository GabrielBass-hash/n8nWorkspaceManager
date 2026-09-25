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


def parse_container_labels(raw: object) -> dict[str, str]:
    """Return the label map of one ``docker ps --format json`` row.

    The ``Labels`` field is shaped by the CLI version, and all three shapes
    occur in the wild: a real object, a JSON string, and — with recent Docker
    (Compose 2.35 on Docker Desktop) — a flat ``k=v,k=v`` string that is *not*
    JSON. Reading only the first two silently dropped every container on such a
    host, so the project map came back empty and no workspace ever looked
    running. Values may contain ``=``, hence the partition on the first one.
    """
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            return {str(key): str(value) for key, value in decoded.items()}
    labels: dict[str, str] = {}
    for pair in text.split(","):
        key, separator, value = pair.partition("=")
        if separator and key.strip():
            labels[key.strip()] = value.strip()
    return labels


def parse_container_states(raw: str) -> dict[str, dict[str, str]]:
    """Map every Compose project to its container states from ``docker ps``.

    One JSON object per container line, attributed to its Compose project and
    service through the ``com.docker.compose.project`` /
    ``com.docker.compose.service`` labels. Containers that are not part of a
    Compose project are ignored, as are malformed rows.
    """
    projects: dict[str, dict[str, str]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        labels = parse_container_labels(row.get("Labels"))
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        state = row.get("State")
        if project and service and isinstance(state, str):
            projects.setdefault(project, {})[service] = state
    return projects


class DockerManager:
    """Thin subprocess wrapper around ``docker compose`` lifecycle commands."""

    def __init__(
        self,
        command: str = "docker",
        timeout: float = 180.0,
        check_timeout: float = 10.0,
    ) -> None:
        self.command = command
        self.timeout = timeout
        # The availability probe gets its own, friendlier deadline: ``docker
        # info`` can take several seconds just to answer while Docker Desktop
        # is still starting (especially on Windows), so 5 s was too tight.
        self.check_timeout = check_timeout

    def check_available(self) -> DockerStatus:
        """Probe ``docker info`` and report daemon availability.

        Never raises: a missing CLI, a hung daemon and a non-zero exit all
        map to ``available=False`` so first-launch turns them into a friendly
        message instead of a traceback.
        """
        try:
            result = self._run([self.command, "info"], timeout=self.check_timeout, check=False)
        except DockerError as exc:
            # ``_run`` already folds both OSError (CLI missing) and
            # TimeoutExpired (daemon not answering) into a single DockerError;
            # keep the probe contract and report availability, not a crash.
            return DockerStatus(False, f"Docker daemon is not accessible: {exc}")
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

    def list_project_states(self) -> dict[str, dict[str, str]]:
        """Return every Compose project's container states from one call.

        A single ``docker ps -a --format json`` yields the state of every
        container the launcher manages, so the GUI can reconcile N workspaces
        in one process spawn instead of N ``compose ps`` calls. A non-zero
        exit is raised as :class:`DockerError`: a partial view would leave
        workspaces stuck on a stale state.
        """
        result = self._run([self.command, "ps", "-a", "--format", "json"], check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise DockerError(f"Docker command failed ({result.returncode}): {detail}")
        return parse_container_states(result.stdout)

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
            "-A",
            "-t",
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
