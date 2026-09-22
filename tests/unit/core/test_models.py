from pathlib import Path

from n8n_launcher.core.models import (
    AppConfig,
    DbConfig,
    DbMode,
    GitConfig,
    Workspace,
    WorkspaceState,
)


def test_workspace_round_trip() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(
            mode=DbMode.MANAGED, database_name="data", username="n8ndata", password="secret"
        ),
        state=WorkspaceState.RUNNING,
        restart_required=True,
    )

    restored = Workspace.from_dict(workspace.to_dict())

    assert restored == workspace


def test_workspace_round_trip_includes_postgres_image() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(mode=DbMode.MANAGED),
        postgres_image="custom/pg:16",
        postgres_preload_timescaledb=True,
    )

    restored = Workspace.from_dict(workspace.to_dict())

    assert restored.postgres_image == "custom/pg:16"
    assert restored.postgres_preload_timescaledb is True
    assert restored == workspace


def test_workspace_postgres_image_defaults_to_none() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(mode=DbMode.NONE),
    )

    assert workspace.postgres_image is None
    assert workspace.postgres_preload_timescaledb is False
    restored = Workspace.from_dict(workspace.to_dict())
    assert restored.postgres_image is None
    assert restored.postgres_preload_timescaledb is False


def test_workspace_from_dict_backcompat_without_postgres_image() -> None:
    data = {
        "id": "abc",
        "name": "My workspace",
        "workflows_dir": "/tmp/workflows",
        "port": 5680,
        "db": {"mode": "none"},
    }

    restored = Workspace.from_dict(data)

    assert restored.postgres_image is None
    assert restored.postgres_preload_timescaledb is False


def test_db_config_from_dict_legacy_external_falls_back_to_none() -> None:
    legacy = DbConfig.from_dict(
        {
            "mode": "external",
            "connection_string": "postgresql://user:secret@db.example/app",
            "database_name": None,
            "username": None,
            "password": None,
        }
    )

    assert legacy.mode is DbMode.NONE
    assert legacy.database_name is None

    round_trip = DbConfig.from_dict(legacy.to_dict())
    assert round_trip.mode is DbMode.NONE


def test_app_config_round_trip(tmp_path: Path) -> None:
    config = AppConfig(
        owner_email="owner@example.test",
        owner_password="secret",
        work_dir=tmp_path,
        workspaces=[],
    )

    assert AppConfig.from_dict(config.to_dict()) == config


def test_app_config_round_trip_keeps_github_token(tmp_path: Path) -> None:
    config = AppConfig(
        owner_email="owner@example.test",
        owner_password="secret",
        work_dir=tmp_path,
        workspaces=[],
        github_token="ghp_remembered",
    )

    assert AppConfig.from_dict(config.to_dict()) == config


def test_app_config_without_github_token_defaults_to_none(tmp_path: Path) -> None:
    legacy = {
        "owner_email": "owner@example.test",
        "owner_password": "secret",
        "work_dir": str(tmp_path),
        "workspaces": [],
    }

    assert AppConfig.from_dict(legacy).github_token is None


def test_git_config_round_trip() -> None:
    config = GitConfig(enabled=True, remote_url="https://example.test/repo.git", branch="main")

    assert GitConfig.from_dict(config.to_dict()) == config


def test_git_config_round_trip_keeps_ci_fields() -> None:
    config = GitConfig(
        enabled=True,
        remote_url="https://example.test/repo.git",
        ci_enabled=True,
        ci_credentials=[{"name": "API", "type": "httpRequest"}],
    )

    assert GitConfig.from_dict(config.to_dict()) == config


def test_git_config_defaults_ci_fields_on_legacy_dict() -> None:
    # Legacy configs carry no CI metadata; they degrade to empty.
    restored = GitConfig.from_dict({"enabled": True})

    assert restored == GitConfig(enabled=True)
    assert restored.ci_enabled is False
    assert restored.ci_credentials == []


def test_git_config_defaults_from_empty_dict() -> None:
    restored = GitConfig.from_dict({})

    assert restored == GitConfig()


def test_git_config_defaults_from_none() -> None:
    assert GitConfig.from_dict(None) == GitConfig()


def test_workspace_round_trip_includes_git_config() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(mode=DbMode.MANAGED),
        git=GitConfig(enabled=True, remote_url="https://example.test/repo.git"),
    )

    restored = Workspace.from_dict(workspace.to_dict())

    assert restored.git == workspace.git
    assert restored == workspace


def test_workspace_round_trip_includes_git_push_failed() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(mode=DbMode.NONE),
        git_push_failed=True,
    )

    assert Workspace.from_dict(workspace.to_dict()).git_push_failed is True
    assert Workspace.from_dict(workspace.to_dict()) == workspace


def test_workspace_from_dict_defaults_push_failed_to_false() -> None:
    data = {
        "id": "abc",
        "name": "My workspace",
        "workflows_dir": "/tmp/workflows",
        "port": 5680,
        "db": {"mode": "none"},
    }

    restored = Workspace.from_dict(data)

    assert restored.git_push_failed is False


def test_workspace_from_dict_backcompat_without_git() -> None:
    data = {
        "id": "abc",
        "name": "My workspace",
        "workflows_dir": "/tmp/workflows",
        "port": 5680,
        "db": {"mode": "none"},
    }

    restored = Workspace.from_dict(data)

    assert restored.git == GitConfig()
