"""Display metadata for workflow entries (per-workspace status summary)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.models import DbMode, Workspace
from ..database import has_db_layout
from ..git import (
    git_has_remote,
    git_has_uncommitted,
    git_has_unpushed_commits,
    git_is_repo,
    git_remote_url,
)


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


@dataclass
class GitRowStatus:
    """Per-workspace git state for the row widget.

    All probes are local (no network) so they are safe to run at every render.
    """

    is_repo: bool = False
    dirty: bool = False
    diverged: bool = False
    push_failed: bool = False
    remote_url: str | None = None

    @property
    def tooltip(self) -> str:
        lines: list[str] = []
        if self.remote_url:
            lines.append(f"Dépôt distant : {self.remote_url}")
        if self.dirty:
            lines.append("Changements locaux non commités")
        if self.diverged:
            lines.append("Commits à pousser vers le dépôt distant")
        if self.push_failed:
            lines.append("Le dernier push a échoué")
        return "\n".join(lines) if lines else "Dépôt Git initialisé (aucun dépôt distant)"


def git_row_status(workspace: Workspace) -> GitRowStatus:
    """Probe the workspace folder and return its git state without networking."""
    folder = workspace.workflows_dir
    if not git_is_repo(folder):
        return GitRowStatus()
    has_remote = git_has_remote(folder)
    return GitRowStatus(
        is_repo=True,
        dirty=git_has_uncommitted(folder),
        diverged=has_remote and git_has_unpushed_commits(folder),
        push_failed=workspace.git_push_failed,
        remote_url=git_remote_url(folder) if has_remote else None,
    )


def git_row_label(status: GitRowStatus) -> str:
    """Return the chip label for a git row status."""
    if not status.is_repo:
        return "git"
    if status.push_failed:
        return "git ✗"
    if status.diverged:
        return "git ⇅"
    return "git"


def format_row(workspace: Workspace) -> str:
    """Return a pipe-delimited one-line summary for the workspace."""
    return (
        f"{workspace.name} | {workspace.state} | :{workspace.port} "
        f"| db {db_label(workspace)} | git {git_label(workspace)} "
        f"| n8nPipelines {pipelines_count(workspace.workflows_dir)}"
    )