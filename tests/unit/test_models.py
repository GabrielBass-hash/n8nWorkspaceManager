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


def test_app_config_round_trip(tmp_path: Path) -> None:
    config = AppConfig(
        owner_email="owner@example.test",
        owner_password="secret",
        work_dir=tmp_path,
        workspaces=[],
    )

    assert AppConfig.from_dict(config.to_dict()) == config
