"""Close sequence: export workflows, sync git, then stop — with no toolkit.

Closing the launcher must not lose work. For every workspace, in order: pull the
current workflows out of n8n into JSON, commit and push them, then stop the
stack. The order is the whole point — exporting *after* stopping would read a
container that is already going down, and syncing *after* stopping would push
files nobody exported.

This module owns that order and nothing else. Asking a person what to do when a
step fails is a decision belonging to whatever is driving the close (a dialog, a
prompt, a log line), so each decision is a :class:`CloseHooks` callback. That is
what makes the sequence callable from a CLI and testable without a display.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.models import Workspace, WorkspaceState
from ..n8n.api import N8nApiClient
from ..n8n.workflows import SyncRunner
from .manager import WorkspaceManager


@dataclass
class CloseHooks:
    """What the caller wants to be asked, said and shown while closing.

    Every default is the quiet headless answer: report nothing, fail nothing,
    keep going. A caller that wants a dialog implements the two ``on_*_failed``
    hooks; one that wants progress implements :attr:`on_progress`.
    """

    #: Called with a human sentence describing the step about to run.
    on_progress: Callable[[str], None] = field(default=lambda _message: None)
    #: Return True to retry the export, False to stop without syncing, and
    #: neither (None) to abort the whole close and leave things running.
    on_sync_failed: Callable[[Workspace, Exception], bool | None] = field(
        default=lambda _workspace, _error: None
    )
    #: Return True to retry the stop, False to skip it and carry on, None to abort.
    on_stop_failed: Callable[[Workspace, Exception], bool | None] = field(
        default=lambda _workspace, _error: None
    )
    #: Called after a sync that committed locally but could not push. The
    #: ``git_push_failed`` flag is also raised by stage/commit failures, so the
    #: message must not claim the push itself is what failed.
    on_push_failed: Callable[[Workspace], None] = field(default=lambda _workspace: None)


class CloseSequence:
    """Run the ordered close flow for every workspace.

    Constructed with a manager and the hooks the caller implements. :meth:`run`
    is synchronous: the steps are SSH- and Docker-scale operations, so blocking
    is the honest default and whoever wants progress on another thread wraps the
    call rather than having threads created behind its back.
    """

    def __init__(self, manager: WorkspaceManager, hooks: CloseHooks | None = None) -> None:
        """Bind the manager and the caller's decisions to this sequence."""
        self.manager = manager
        self.hooks = hooks or CloseHooks()

    def run(self) -> bool:
        """Close every workspace; return False if the user aborted.

        Reconciles first so the per-workspace decision below is made on a real
        state: a workspace marked running that is in fact stopped has no n8n to
        export from, and one marked stopped that is still up must still be
        stopped.
        """
        workspaces = self.manager.list()
        if not workspaces:
            return True
        self.hooks.on_progress("Fermeture : synchronisation des workflows puis arrêt de n8n…")
        with contextlib.suppress(Exception):
            self.manager.reconcile_all()
        return self._next(self.manager.list())

    def _next(self, workspaces: list[Workspace]) -> bool:
        """Close the first workspace, then recurse; False once aborted."""
        if not workspaces:
            return True
        workspace, rest = workspaces[0], workspaces[1:]
        # Only a running workspace with a key can be exported from: without the
        # key there is no session to read the workflows back with, and a stopped
        # one has nothing running to read them from.
        if workspace.state is WorkspaceState.RUNNING and workspace.api_key:
            return self._sync(workspace, rest)
        return self._stop(workspace, rest)

    def _sync(self, workspace: Workspace, rest: list[Workspace]) -> bool:
        """Export, commit and push one workspace, then stop it."""
        self.hooks.on_progress(f"Fermeture : synchronisation de « {workspace.name} »…")
        try:
            self._export(workspace)
            self.manager.sync_git(workspace, push=True)
        except Exception as exc:
            return self._after_sync_failed(workspace, rest, exc)
        if workspace.git_push_failed:
            # Committed locally but not delivered: worth telling the user, and
            # never worth retrying the export over.
            self.hooks.on_push_failed(workspace)
        return self._stop(workspace, rest)

    def _export(self, workspace: Workspace) -> None:
        """Refresh the workspace's JSON exports from the running n8n."""
        api_key = workspace.api_key
        if api_key is None:
            return
        api = N8nApiClient(f"http://127.0.0.1:{workspace.port}/api/v1", api_key)
        pipelines_dir = workspace.workflows_dir / "n8nPipelines"
        SyncRunner(api, pipelines_dir).export_all(mirror=workspace.workflows_dir)

    def _stop(self, workspace: Workspace, rest: list[Workspace]) -> bool:
        """Stop one workspace, then continue with the rest."""
        self.hooks.on_progress(f"Fermeture : arrêt de « {workspace.name} »…")
        try:
            self.manager.stop(workspace.id)
        except Exception as exc:
            return self._after_stop_failed(workspace, rest, exc)
        return self._next(rest)

    def _after_sync_failed(
        self, workspace: Workspace, rest: list[Workspace], error: Exception
    ) -> bool:
        """Apply the caller's decision to a failed export or git sync."""
        choice = self.hooks.on_sync_failed(workspace, error)
        if choice is True:
            return self._sync(workspace, rest)
        if choice is False:
            # "Stop without syncing" is a valid answer: the workspaces still have
            # to come down, and the JSON on disk is whatever it already was.
            return self._stop(workspace, rest)
        return False

    def _after_stop_failed(
        self, workspace: Workspace, rest: list[Workspace], error: Exception
    ) -> bool:
        """Apply the caller's decision to a failed stop."""
        choice = self.hooks.on_stop_failed(workspace, error)
        if choice is True:
            return self._stop(workspace, rest)
        if choice is False:
            # "Ignore and continue" skips only this workspace; the rest still
            # have to close, and the close must not be reported as complete.
            self.hooks.on_progress(f"Fermeture : arrêt de « {workspace.name} » ignoré.")
            return self._next(rest)
        return False


__all__ = ["CloseHooks", "CloseSequence"]
