"""The list model behind the workspace board.

It caches its rows: Qt calls ``data()`` on every repaint, sometimes several times
per frame, and reading the SQLite config there would put file I/O on the paint
path. ``refresh`` reloads from the manager; ``apply_workspace`` updates a single
row in place when the notifier says so. The status decisions come from
:mod:`n8n_launcher.workspaces.status` — the model renders them, it does not
compute them.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

from ..core.models import Workspace
from ..workspaces.manager import WorkspaceManager
from ..workspaces.status import WorkspaceStatus, status_for


class WorkspaceListModel(QAbstractListModel):
    """A flat list of workspaces, cached and refreshed by notification."""

    #: Carries the :class:`~n8n_launcher.core.models.Workspace` itself.
    WorkspaceRole = Qt.ItemDataRole.UserRole + 1
    #: Carries the :class:`~n8n_launcher.workspaces.status.WorkspaceStatus`.
    StatusRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, manager: WorkspaceManager, parent: QObject | None = None) -> None:
        """Hold *manager* as the source of truth; nothing is read yet."""
        super().__init__(parent)
        self._manager = manager
        self._rows: list[Workspace] = []

    def refresh(self) -> None:
        """Reload every row from the manager, resetting the view."""
        self.beginResetModel()
        try:
            self._rows = list(self._manager.list())
        finally:
            self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        """Return the number of workspaces; a child of *parent* has none."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Return the value Qt asks for; ``None`` for an unsupported role."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        workspace = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return workspace.name
        if role == self.WorkspaceRole:
            return workspace
        if role == self.StatusRole:
            return status_for(workspace.state)
        return None

    def workspace_at(self, index: QModelIndex) -> Workspace | None:
        """Return the workspace at *index*, or ``None`` when it is invalid."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return self._rows[index.row()]

    def status_at(self, index: QModelIndex) -> WorkspaceStatus | None:
        """Return the status at *index*, or ``None`` when it is invalid."""
        workspace = self.workspace_at(index)
        return status_for(workspace.state) if workspace is not None else None

    def index_of(self, workspace_id: str) -> QModelIndex:
        """Return the index of *workspace_id*, or an invalid index."""
        for row, workspace in enumerate(self._rows):
            if workspace.id == workspace_id:
                return self.index(row, 0)
        return QModelIndex()

    def apply_workspace(self, workspace: Workspace) -> None:
        """Update one row in place, or reload when it is not yet known."""
        for row, current in enumerate(self._rows):
            if current.id == workspace.id:
                self._rows[row] = workspace
                index = self.index(row, 0)
                self.dataChanged.emit(index, index)
                return
        self.refresh()


__all__ = ["WorkspaceListModel"]
