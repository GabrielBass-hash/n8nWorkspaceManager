from pathlib import Path

from n8n_launcher.models import AppConfig, DbConfig, DbMode, Workspace, WorkspaceState


def test_workspace_round_trip() -> None:
    workspace = Workspace(
        id="abc",
        name="My workspace",
        workflows_dir=Path("/tmp/workflows"),
        port=5680,
        db=DbConfig(mode=DbMode.EXTERNAL, connection_string="postgresql://user:secret@db/app"),
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


def test_app_config_round_trip(tmp_path: Path) -> None:
    config = AppConfig(
        owner_email="owner@example.test",
        owner_password="secret",
        work_dir=tmp_path,
        workspaces=[],
    )

    assert AppConfig.from_dict(config.to_dict()) == config
