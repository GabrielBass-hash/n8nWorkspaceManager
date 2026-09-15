from pathlib import Path

from n8n_launcher.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.workspace_info import (
    db_connected,
    db_label,
    format_row,
    git_label,
    git_repo_status,
    pipelines_count,
)


def make_workspace(path: Path, *, mode: DbMode = DbMode.MANAGED) -> Workspace:
    return Workspace(
        id="w1",
        name="Demo",
        workflows_dir=path,
        port=5678,
        db=DbConfig(mode=mode),
    )


def add_migration(path: Path) -> None:
    (path / "db" / "migrations").mkdir(parents=True)
    (path / "db" / "migrations" / "001.sql").write_text("select 1;")


def add_pipelines(path: Path) -> None:
    pipelines = path / "n8nPipelines"
    pipelines.mkdir(parents=True, exist_ok=True)
    (pipelines / "hello-1.json").write_text("{}")


def test_git_repo_status_detects_git_directory(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    assert git_repo_status(tmp_path) is True


def test_git_repo_status_detects_git_worktree_marker(tmp_path) -> None:
    (tmp_path / ".git").write_text("gitdir: ../.git\n")
    assert git_repo_status(tmp_path) is True


def test_git_repo_status_missing(tmp_path) -> None:
    assert git_repo_status(tmp_path) is False


def test_db_connected_managed_requires_layout(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    assert db_connected(workspace) is False

    add_migration(tmp_path)
    assert db_connected(workspace) is True


def test_format_row_shows_none_db_and_no_pipelines(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    add_migration(tmp_path)
    add_pipelines(tmp_path)
    workspace = make_workspace(tmp_path)
    workspace.state = WorkspaceState.RUNNING

    assert format_row(workspace) == "Demo | running | :5678 | db locale | git oui | n8nPipelines 1"


def test_format_row_shows_none_db_and_no_pipelines(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    assert format_row(workspace) == "Demo | stopped | :5678 | db aucune | git non | n8nPipelines 0"


def test_db_label_maps_mode(tmp_path) -> None:
    from n8n_launcher.workspace_info import db_label as db_lbl

    assert db_lbl(make_workspace(tmp_path, mode=DbMode.MANAGED)) == "locale"
    assert db_lbl(make_workspace(tmp_path, mode=DbMode.NONE)) == "aucune"


def test_pipelines_count_counts_json_files(tmp_path) -> None:
    assert pipelines_count(tmp_path) == 0
    add_pipelines(tmp_path)
    assert pipelines_count(tmp_path) == 1