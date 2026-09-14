from pathlib import Path
from unittest.mock import patch

import pytest

from n8n_launcher.docker_manager import DockerManager, parse_compose_status
from n8n_launcher.models import DbConfig, DbMode, Workspace


def workspace(tmp_path: Path) -> Workspace:
    return Workspace("abc123", "Demo", tmp_path, 5678, DbConfig(DbMode.MANAGED))


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from subprocess import CompletedProcess

    return CompletedProcess(["docker"], returncode, stdout, stderr)


def test_check_available_reports_daemon_state() -> None:
    with patch("n8n_launcher.docker_manager.subprocess.run", return_value=completed()):
        status = DockerManager().check_available()

    assert status.available is True


def test_up_uses_isolated_compose_project(tmp_path: Path) -> None:
    manager = DockerManager()
    compose_file = tmp_path / "compose file.yml"

    with patch("n8n_launcher.docker_manager.subprocess.run", return_value=completed()) as run:
        manager.up(workspace(tmp_path), compose_file)

    command = run.call_args.args[0]
    assert command == [
        "docker",
        "compose",
        "-p",
        "n8n-ws-abc123",
        "-f",
        str(compose_file),
        "up",
        "-d",
        "--remove-orphans",
    ]
    assert run.call_args.kwargs.get("shell") is not True


def test_failed_command_raises_docker_error(tmp_path: Path) -> None:
    manager = DockerManager()

    with patch(
        "n8n_launcher.docker_manager.subprocess.run",
        return_value=completed(1, stderr="daemon unavailable"),
    ), pytest.raises(Exception, match="daemon unavailable"):
        manager.up(workspace(tmp_path), tmp_path / "compose.yml")


def test_parse_compose_status_maps_services_to_state() -> None:
    raw = (
        '{"Service":"postgres","State":"running"}\n'
        '{"Service":"n8n","State":"running"}\n'
    )
    assert parse_compose_status(raw) == {"postgres": "running", "n8n": "running"}


def test_parse_compose_status_ignores_garbage_lines() -> None:
    raw = "not-json\n{\"Service\":\"n8n\",\"State\":\"exited\"}\n"
    assert parse_compose_status(raw) == {"n8n": "exited"}


def test_parse_compose_status_empty_when_no_rows() -> None:
    assert parse_compose_status("") == {}
