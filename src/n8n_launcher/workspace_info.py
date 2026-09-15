"""Display metadata for workflow entries (per-workspace status summary)."""

from __future__ import annotations

from pathlib import Path

from .db_manager import has_db_layout
from .git_manager import git_is_repo
from .models import DbMode, Workspace


def git_repo_status(workflows_dir: Path) -> bool:
    """Return True when the workflows directory is inside a Git repository."""
    return git_is_repo(workflows_dir)


def db_connected(workspace: Workspace) -> bool:
    """Return True when the workspace has a managed DB with migration files."""
    if workspace.db.mode is DbMode.MANAGED:
        return has_db_layout(workspace.workflows_dir)
    return False


def db_label(workspace: Workspace) -> str:
    """Return a short French label describing the database mode."""
    if workspace.db.mode is DbMode.MANAGED:
        return "locale"
    return "aucune"


def git_label(workspace: Workspace) -> str:
    """Return a short French label indicating Git presence."""
    return "oui" if git_repo_status(workspace.workflows_dir) else "non"


def pipelines_count(workflows_dir: Path) -> int:
    """Count the number of exported workflow JSON files in ``n8nPipelines/``."""
    pipelines_dir = workflows_dir / "n8nPipelines"
    if not pipelines_dir.is_dir():
        return 0
    return sum(1 for path in pipelines_dir.glob("*.json") if path.is_file())


def format_row(workspace: Workspace) -> str:
    """Return a pipe-delimited one-line summary for the workspace."""
    return (
        f"{workspace.name} | {workspace.state} | :{workspace.port} "
        f"| db {db_label(workspace)} | git {git_label(workspace)} "
        f"| n8nPipelines {pipelines_count(workspace.workflows_dir)}"
    )