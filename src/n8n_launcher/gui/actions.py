"""Workspace actions run off the GUI thread.

Every manager call that touches Docker, Git or a database blocks: starting a
workspace waits for ``docker compose up`` and the workflow import, stopping it
waits for the export and the push. Run on the GUI thread, any of them freezes
the window. So a click is turned into a *task* handed to a
:class:`QThreadPool`, and the window keeps painting.

The thread boundary is also where a failure has to be caught: an exception
raised on a worker thread that reached Qt's event loop would be routed to the
exception hook and lost to the user. :class:`WorkspaceActions` turns it into a
``failed`` signal instead.

The executor is injectable, so a test can run the same code synchronously and
assert on it without a race.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core.models import Workspace
from ..workspaces.dialogs import CreatePlan
from ..workspaces.manager import Reachable, WorkspaceManager

#: A blocking unit of work and the two ways it can end.
Work = Callable[[], object]
OnSuccess = Callable[[object], None]
OnFailure = Callable[[Exception], None]
#: Runs *work* and calls exactly one of done/fail, on any thread.
Executor = Callable[[Work, OnSuccess, OnFailure], None]


class _Task(QRunnable):
    """Run one :class:`Work` on a pool thread and report its outcome."""

    def __init__(self, work: Work, done: OnSuccess, fail: OnFailure) -> None:
        """Capture the work and its callbacks; nothing runs until ``run``."""
        super().__init__()
        self._work = work
        self._done = done
        self._fail = fail
        self.setAutoDelete(True)

    def run(self) -> None:
        """Execute the work, routing success and failure to their callbacks."""
        try:
            result = self._work()
        except Exception as exc:
            # A worker must report the failure, never let it reach Qt's loop.
            self._fail(exc)
        else:
            self._done(result)


def _pool_executor(work: Work, done: OnSuccess, fail: OnFailure) -> None:
    """Hand the work to Qt's global thread pool."""
    QThreadPool.globalInstance().start(_Task(work, done, fail))


class WorkspaceActions(QObject):
    """Start, stop, delete and create workspaces without blocking the window."""

    #: Emitted with True while at least one task is in flight.
    busyChanged = Signal(bool)
    #: Emitted as ``(title, message)`` when a task raised.
    failed = Signal(str, str)
    #: Emitted with the :class:`Reachable` whose n8n is ready to be shown.
    ready = Signal(object)
    #: Emitted with the new :class:`Workspace` once a creation succeeds.
    created = Signal(object)

    def __init__(
        self,
        manager: WorkspaceManager,
        parent: QObject | None = None,
        *,
        executor: Executor | None = None,
    ) -> None:
        """Hold *manager*; run tasks through *executor* or the global pool."""
        super().__init__(parent)
        self._manager = manager
        self._executor = executor or _pool_executor
        self._pending = 0
        self._lock = threading.Lock()

    def open(self, workspace: Workspace) -> None:
        """Make *workspace*'s n8n reachable and openable, in the background.

        The manager starts the workspace only when Docker says it is not up,
        so this is the one action behind both a stopped card and a running one.
        """
        self._dispatch(
            "Ouverture impossible",
            lambda: self._manager.ensure_serving(workspace.id),
            announce_ready=True,
        )

    def stop(self, workspace: Workspace) -> None:
        """Stop *workspace* in the background, exporting and pushing first.

        ``stop_with_sync`` and not ``stop``: a user who stops one workspace by
        hand expects the workflows they just built in n8n to reach the
        repository, exactly as they would by closing the app. It is the call
        whose docstring has promised this since it was written.
        """
        self._dispatch(
            "Arrêt impossible",
            lambda: self._manager.stop_with_sync(workspace.id),
        )

    def delete(self, workspace: Workspace) -> None:
        """Delete *workspace* in the background."""
        self._dispatch(
            "Suppression impossible",
            lambda: self._manager.delete(workspace.id),
        )

    def create(self, plan: CreatePlan, workflows_dir: Path) -> None:
        """Create a workspace from *plan* under *workflows_dir*, in the background."""
        self._dispatch(
            "Création impossible",
            lambda: self._manager.create(plan.name, workflows_dir, db=plan.db),
            announce_created=True,
        )

    def rename_workspace(self, workspace_id: str, new_name: str) -> None:
        """Rename a workspace in the background."""
        self._dispatch(
            "Renommage impossible",
            lambda: self._manager.rename_workspace(workspace_id, new_name),
        )

    def _dispatch(
        self,
        failure_title: str,
        work: Work,
        *,
        announce_created: bool = False,
        announce_ready: bool = False,
    ) -> None:
        """Run *work*, emit ``created`` on success and ``failed`` otherwise."""
        self._begin_busy()

        def done(result: object) -> None:
            self._end_busy()
            if announce_created and isinstance(result, Workspace):
                self.created.emit(result)
            if announce_ready and isinstance(result, Reachable):
                self.ready.emit(result)

        def fail(exc: Exception) -> None:
            self._end_busy()
            self.failed.emit(failure_title, str(exc))

        self._executor(work, done, fail)

    def _begin_busy(self) -> None:
        """Account for a task starting and announce the busy state."""
        with self._lock:
            self._pending += 1
        self.busyChanged.emit(True)

    def _end_busy(self) -> None:
        """Account for a task ending; announce idle exactly once."""
        with self._lock:
            self._pending = max(0, self._pending - 1)
            idle = self._pending == 0
        if idle:
            self.busyChanged.emit(False)


__all__ = ["Executor", "WorkspaceActions"]
