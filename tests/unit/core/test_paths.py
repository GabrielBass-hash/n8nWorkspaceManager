from pathlib import Path

import pytest

from n8n_launcher.core import paths


def test_app_name_constant() -> None:
    assert paths.APP_NAME == "n8n-launcher"


def test_config_dir_uses_platformdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "user_config_dir", lambda name: str(tmp_path / name))

    assert paths.config_dir() == tmp_path / "n8n-launcher"


def test_config_file_appends_legacy_config_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.config_file() == tmp_path / "config.json"


def test_launcher_db_appends_launcher_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.launcher_db() == tmp_path / "launcher.db"


def test_logs_dir_uses_platformdirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "user_log_dir", lambda name: str(tmp_path / name))

    assert paths.logs_dir() == tmp_path / "n8n-launcher"


def test_workspace_runtime_dir_appends_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.workspace_runtime_dir("w1") == tmp_path / "workspaces" / "w1"


def test_compose_file_appends_compose_yml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.compose_file("w1") == tmp_path / "workspaces" / "w1" / "compose.yml"


def test_browser_app_dir_appends_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.browser_app_dir("w1") == tmp_path / "browser" / "w1"


def test_updates_dir_appends_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.updates_dir() == tmp_path / "updates"


def test_path_helpers_do_not_create_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    logs = tmp_path / "logs"
    monkeypatch.setattr(paths, "user_config_dir", lambda _name: str(config))
    monkeypatch.setattr(paths, "user_log_dir", lambda _name: str(logs))

    paths.workspace_runtime_dir("w1")
    paths.compose_file("w1")
    paths.browser_app_dir("w1")
    paths.updates_dir()
    paths.logs_dir()

    assert not config.exists()
    assert not logs.exists()
