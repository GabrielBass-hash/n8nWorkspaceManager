from pathlib import Path
from unittest.mock import patch

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.docker.manager import (
    DockerManager,
    parse_compose_status,
    parse_container_states,
    resolve_docker_command,
)


def workspace(tmp_path: Path) -> Workspace:
    return Workspace("abc123", "Demo", tmp_path, 5678, DbConfig(DbMode.MANAGED))


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from subprocess import CompletedProcess

    return CompletedProcess(["docker"], returncode, stdout, stderr)


def test_check_available_reports_daemon_state() -> None:
    with patch("n8n_launcher.docker.manager.subprocess.run", return_value=completed()):
        status = DockerManager().check_available()

    assert status.available is True


def test_up_uses_isolated_compose_project(tmp_path: Path) -> None:
    manager = DockerManager()
    compose_file = tmp_path / "compose file.yml"

    with patch("n8n_launcher.docker.manager.subprocess.run", return_value=completed()) as run:
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


def test_resolve_docker_command_returns_docker_on_linux() -> None:
    with (
        patch("n8n_launcher.docker.manager.platform.system", return_value="Linux"),
        patch("n8n_launcher.docker.manager.shutil.which", return_value="/usr/bin/docker"),
    ):
        assert resolve_docker_command() == "/usr/bin/docker"


def test_resolve_docker_command_finds_homebrew_on_macos(tmp_path) -> None:
    docker_path = tmp_path / "docker"
    docker_path.write_bytes(b"\x00")
    docker_path.chmod(0o755)

    with (
        patch("n8n_launcher.docker.manager.platform.system", return_value="Darwin"),
        patch(
            "n8n_launcher.docker.manager.MACOS_DOCKER_SEARCH_DIRS",
            [str(tmp_path)],
        ),
        patch("n8n_launcher.docker.manager.shutil.which", return_value=None),
    ):
        assert resolve_docker_command() == str(docker_path)


def test_resolve_docker_command_falls_back_to_name() -> None:
    with (
        patch("n8n_launcher.docker.manager.platform.system", return_value="Linux"),
        patch("n8n_launcher.docker.manager.shutil.which", return_value=None),
    ):
        assert resolve_docker_command() == "docker"


def test_failed_command_raises_docker_error(tmp_path: Path) -> None:
    manager = DockerManager()

    with (
        patch(
            "n8n_launcher.docker.manager.subprocess.run",
            return_value=completed(1, stderr="daemon unavailable"),
        ),
        pytest.raises(Exception, match="daemon unavailable"),
    ):
        manager.up(workspace(tmp_path), tmp_path / "compose.yml")


def test_parse_compose_status_maps_services_to_state() -> None:
    raw = '{"Service":"postgres","State":"running"}\n{"Service":"n8n","State":"running"}\n'
    assert parse_compose_status(raw) == {"postgres": "running", "n8n": "running"}


def test_parse_compose_status_ignores_garbage_lines() -> None:
    raw = 'not-json\n{"Service":"n8n","State":"exited"}\n'
    assert parse_compose_status(raw) == {"n8n": "exited"}


def test_parse_compose_status_empty_when_no_rows() -> None:
    assert parse_compose_status("") == {}


def test_parse_container_states_groups_by_compose_project() -> None:
    raw = (
        '{"Id":"a","State":"running","Labels":{"com.docker.compose.project":"n8n-ws-1",'
        '"com.docker.compose.service":"n8n"}}\n'
        '{"Id":"b","State":"exited","Labels":{"com.docker.compose.project":"n8n-ws-2",'
        '"com.docker.compose.service":"n8n"}}\n'
        '{"Id":"c","State":"running","Labels":{"com.docker.compose.project":"n8n-ws-2",'
        '"com.docker.compose.service":"postgres"}}\n'
    )
    assert parse_container_states(raw) == {
        "n8n-ws-1": {"n8n": "running"},
        "n8n-ws-2": {"n8n": "exited", "postgres": "running"},
    }


def test_parse_container_states_accepts_string_labels() -> None:
    raw = (
        '{"Id":"a","State":"paused","Labels":"{\\"com.docker.compose.project\\":\\"n8n-ws-1\\",'
        '\\"com.docker.compose.service\\":\\"n8n\\"}"}\n'
    )
    assert parse_container_states(raw) == {"n8n-ws-1": {"n8n": "paused"}}


def test_parse_container_states_ignores_non_compose_containers() -> None:
    raw = (
        '{"Id":"a","State":"running","Labels":{"com.docker.compose.project":"n8n-ws-1",'
        '"com.docker.compose.service":"n8n"}}\n'
        '{"Id":"b","State":"running","Labels":{"org.label-schema.name":"influxdb"}}\n'
        "not-json\n"
    )
    assert parse_container_states(raw) == {"n8n-ws-1": {"n8n": "running"}}


def test_parse_container_states_empty_when_no_rows() -> None:
    assert parse_container_states("") == {}


def test_list_project_states_runs_one_docker_ps_batch(tmp_path: Path) -> None:
    manager = DockerManager()
    raw = (
        '{"Id":"a","State":"running","Labels":{"com.docker.compose.project":"n8n-ws-abc123",'
        '"com.docker.compose.service":"n8n"}}\n'
    )

    with patch("n8n_launcher.docker.manager.subprocess.run", return_value=completed(stdout=raw)):
        states = manager.list_project_states()

    assert states == {"n8n-ws-abc123": {"n8n": "running"}}


def test_list_project_states_raises_when_daemon_fails(tmp_path: Path) -> None:
    manager = DockerManager()

    with (
        patch(
            "n8n_launcher.docker.manager.subprocess.run",
            return_value=completed(1, stderr="cannot connect"),
        ),
        pytest.raises(Exception, match="cannot connect"),
    ):
        manager.list_project_states()
