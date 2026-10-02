"""Qt bridge from the manager's toolkit-free notifications to signals.

The manager knows nothing about Qt: it calls
:class:`~n8n_launcher.workspaces.manager.WorkspaceObserver` methods. This object
implements that protocol and re-emits each call as a Qt signal, so a view
connects slots instead of polling and never lets a widget leak into the
business layer. A manager method that runs on a worker thread emits here too;
Qt delivers the signal to a widget's slot through a queued connection.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.models import Workspace


class WorkspaceNotifier(QObject):
    """Re-emit :class:`WorkspaceObserver` notifications as Qt signals."""

    #: The set of workspaces changed (create / delete / reconcile).
    workspacesChanged = Signal()
    #: One workspace changed; carries the :class:`~n8n_launcher.core.models.Workspace`.
    workspaceChanged = Signal(object)

    def workspaces_changed(self) -> None:
        """Emit :attr:`workspacesChanged` (observer protocol)."""
        self.workspacesChanged.emit()

    def workspace_changed(self, workspace: Workspace) -> None:
        """Emit :attr:`workspaceChanged` with *workspace* (observer protocol)."""
        self.workspaceChanged.emit(workspace)


__all__ = ["WorkspaceNotifier"]
