"""Display metadata for workflow entries (per-workspace status summary)."""

from __future__ import annotations

from pathlib import Path

from .db_manager import has_db_layout
from .models import DbMode, Workspace


def git_repo_status(workflows_dir: Path) -> bool:
    git_marker = workflows_dir / ".git"
    return git_marker.is_dir() or git_marker.is_file()


def db_connected(workspace: Workspace) -> bool:
    if workspace.db.mode is DbMode.EXTERNAL:
        return bool(workspace.db.connection_string and workspace.db.connection_string.strip())
    if workspace.db.mode is DbMode.MANAGED:
        return has_db_layout(workspace.workflows_dir)
    return False


def db_label(workspace: Workspace) -> str:
    if workspace.db.mode is DbMode.MANAGED:
        return "locale"
    if workspace.db.mode is DbMode.EXTERNAL:
        return "distante"
    return "aucune"


def git_label(workspace: Workspace) -> str:
    return "oui" if git_repo_status(workspace.workflows_dir) else "non"


def pipelines_count(workflows_dir: Path) -> int:
    pipelines_dir = workflows_dir / "n8nPipelines"
    if not pipelines_dir.is_dir():
        return 0
    return sum(1 for path in pipelines_dir.glob("*.json") if path.is_file())


def format_row(workspace: Workspace) -> str:
    return (
        f"{workspace.name} | {workspace.state} | :{workspace.port} "
        f"| db {db_label(workspace)} | git {git_label(workspace)} "
        f"| n8nPipelines {pipelines_count(workspace.workflows_dir)}"
    )