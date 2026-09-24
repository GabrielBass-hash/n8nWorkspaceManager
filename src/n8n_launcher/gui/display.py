"""Display metadata for workflow entries (per-workspace status summary)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from ..core.models import DbMode, Workspace
from ..database import has_db_layout
from ..git import git_has_unpushed_commits, git_is_repo, git_probe_status
from ..workspaces import ci

# Rows refresh on a timer; a 3-second TTL keeps the git probe from re-spawning
# git for every workspace on every tick while still showing near-live status.
_GIT_CACHE_TTL_SECONDS = 3.0


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
    ci_enabled: bool = False

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
        if self.ci_enabled:
            lines.append("Tests GitHub Actions activés")
        return "\n".join(lines) if lines else "Dépôt Git initialisé (aucun dépôt distant)"


def git_row_status(workspace: Workspace) -> GitRowStatus:
    """Probe the workspace folder and return its git state without networking.

    The git probe is cached per (id, folder) for a few seconds so a GUI tick
    re-rendering every row does not spawn git for each workspace every time.
    Workspace-derived fields (push failure, CI toggle) are read live on every
    call, so a status change never waits for the cache to expire.
    """
    key = (workspace.id, workspace.workflows_dir)
    now = time.monotonic()
    with _git_cache_guard:
        entry = _git_status_cache.get(key)
        cached = entry[1] if entry is not None and now - entry[0] < _GIT_CACHE_TTL_SECONDS else None
    if cached is None:
        cached = _git_row_probe(workspace)
        with _git_cache_guard:
            _git_status_cache[key] = (now, cached)
    return replace(
        cached,
        push_failed=workspace.git_push_failed,
        ci_enabled=workspace.git.ci_enabled,
    )


def invalidate_git_status(workspace: Workspace) -> None:
    """Drop the cached git probe for *workspace* after a state-changing action."""
    key = (workspace.id, workspace.workflows_dir)
    with _git_cache_guard:
        _git_status_cache.pop(key, None)


def _git_row_probe(workspace: Workspace) -> GitRowStatus:
    """Run the (cached) git probes, mapping them onto row semantics."""
    folder = workspace.workflows_dir
    probe = git_probe_status(folder)
    if not probe.is_repo:
        return GitRowStatus()
    has_remote = probe.has_remote
    # Without an upstream, ahead/behind is unknown; fall back to a direct
    # unpushed-commits probe (the historical semantic) so a fresh launcher
    # repo that was never pushed to a remote still reports "git <>" once the
    # remote is configured.
    unpushed = probe.ahead > 0 if probe.upstream else git_has_unpushed_commits(folder)
    return GitRowStatus(
        is_repo=True,
        dirty=probe.dirty,
        diverged=has_remote and unpushed,
        remote_url=probe.remote_url,
    )


_git_status_cache: dict[tuple[str, Path], tuple[float, GitRowStatus]] = {}
_git_cache_guard = threading.Lock()


def git_row_label(status: GitRowStatus) -> str:
    """Return the chip label for a git row status."""
    if not status.is_repo:
        return "git"
    if status.push_failed:
        return "git KO"  # ASCII: ✗ (U+2717) is absent from Linux UI fonts
    if status.diverged:
        return "git <>"  # ASCII: ⇅ (U+21C5) is absent from Linux UI fonts
    return "git"


def format_row(workspace: Workspace) -> str:
    """Return a pipe-delimited one-line summary for the workspace."""
    return (
        f"{workspace.name} | {workspace.state} | :{workspace.port} "
        f"| db {db_label(workspace)} | git {git_label(workspace)} "
        f"| n8nPipelines {pipelines_count(workspace.workflows_dir)}"
    )


def ci_enabled(workspace: Workspace) -> bool:
    """Return True when the workspace's GitHub Actions tests are enabled."""
    return workspace.git.ci_enabled


def server_enabled(workspace: Workspace) -> bool:
    """Return True when the workspace deploys to a production server."""
    return workspace.server.enabled


def server_label(workspace: Workspace) -> str:
    """Return the chip label for the server deployment state."""
    if not workspace.server.enabled:
        return "serv"
    if workspace.server_last_error:
        return "serv KO"  # ASCII: ✗ (U+2717) is absent from Linux UI fonts
    return "serv"


def server_tooltip(workspace: Workspace) -> str:
    """Return the server chip tooltip with the last deploy outcome."""
    if not workspace.server.enabled:
        return "Déploiement serveur désactivé (cliquez pour configurer)"
    lines = [f"Serveur de production : {workspace.server.user}@{workspace.server.host}"]
    if workspace.server_last_error:
        lines.append(f"Dernier déploiement en échec : {workspace.server_last_error}")
    else:
        lines.append("Dernier déploiement réussi")
    return "\n".join(lines)


def ci_tooltip(workspace: Workspace) -> str:
    """Return the CI chip tooltip with live selection counters."""
    if not workspace.git.ci_enabled:
        return "Tests GitHub Actions désactivés (cliquez pour configurer)"
    provided = ci.provided_credentials(workspace.git.ci_credentials)
    counts = ci.ci_counts(workspace.workflows_dir, provided)
    lines = [
        "Tests GitHub Actions activés :",
        f"{counts['selected']} pipeline(s) sélectionnée(s) sur {counts['eligible']} testable(s)",
    ]
    if not counts["eligible"]:
        lines.append("Aucune pipeline testable (déclencheurs ou credentials manquants)")
    elif counts["selected_eligible"] < counts["selected"]:
        lines.append("Certaines pipelines sélectionnées ne sont plus testables")
    return "\n".join(lines)
