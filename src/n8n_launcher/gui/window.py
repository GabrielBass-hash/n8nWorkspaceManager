"""The launcher's main window: the workspace board.

A thin shell over a :class:`WorkspaceListModel`. It listens to the manager
through a :class:`WorkspaceNotifier` and updates the model, so the window never
polls and never touches Docker, Git or the config store itself. The board is an
icon-mode :class:`QListView` so the cards wrap into the available room; the
empty state is a separate page of a stack rather than an item, because an empty
board must not pretend to hold a row.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Workspace
from ..workspaces.manager import WorkspaceManager
from ..workspaces.status import status_for
from . import theme
from .actions import WorkspaceActions
from .card_delegate import CARD_HEIGHT, CARD_WIDTH, CardDelegate
from .create_panel import CreateWorkspaceDialog
from .notifier import WorkspaceNotifier
from .workspace_model import WorkspaceListModel


class MainWindow(QMainWindow):
    """Show every workspace as a card and keep the board in step with the manager."""

    #: Filled by :meth:`_build_header` during construction.
    _header_row: QHBoxLayout

    def __init__(self, manager: WorkspaceManager, parent: QWidget | None = None) -> None:
        """Build the window over *manager* and subscribe to its changes."""
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("n8n Launcher")
        self.resize(1000, 680)

        self._model = WorkspaceListModel(manager, self)
        self._actions = WorkspaceActions(manager, self)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header(central))

        self._board = QListView(central)
        self._configure_board(self._board)
        self._board.setModel(self._model)

        self._empty = QLabel("Aucun workspace. Créez-en un pour commencer.", central)
        self._empty.setObjectName("empty-state")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._stack = QStackedWidget(central)
        self._stack.addWidget(self._board)
        self._stack.addWidget(self._empty)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._model.modelReset.connect(self._sync_empty_state)
        self._model.refresh()
        self._sync_empty_state()

        self._notifier = WorkspaceNotifier(self)
        manager.add_observer(self._notifier)
        self._notifier.workspacesChanged.connect(self._model.refresh)
        self._notifier.workspaceChanged.connect(self._model.apply_workspace)

        selection = self._board.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._sync_actions)
        self._actions.failed.connect(self._on_failed)
        self._actions.busyChanged.connect(self._on_busy)
        self._sync_actions()

    def _build_header(self, parent: QWidget) -> QWidget:
        """Return the top bar; the actions layer fills it after construction."""
        header = QWidget(parent)
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(
            theme.CARD_PADDING, theme.CARD_PADDING, theme.CARD_PADDING, theme.CARD_PADDING
        )
        title = QLabel("Workspaces", header)
        title.setObjectName("title")
        row.addWidget(title)
        row.addStretch(1)
        self._new_button = QPushButton("Nouveau", header)
        self._new_button.setObjectName("new")
        self._start_button = QPushButton("Démarrer", header)
        self._start_button.setObjectName("start")
        self._stop_button = QPushButton("Arrêter", header)
        self._stop_button.setObjectName("stop")
        self._new_button.clicked.connect(self._on_new)
        self._start_button.clicked.connect(self._on_start)
        self._stop_button.clicked.connect(self._on_stop)
        for button in (self._new_button, self._start_button, self._stop_button):
            row.addWidget(button)
        self._header_row = row
        return header

    @staticmethod
    def _configure_board(view: QListView) -> None:
        """Lay the board out as a wrapping, single-selection grid of cards."""
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setFlow(QListView.Flow.LeftToRight)
        view.setWrapping(True)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setMovement(QListView.Movement.Static)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setUniformItemSizes(True)
        view.setSpacing(theme.SPACING)
        view.setGridSize(QSize(CARD_WIDTH, CARD_HEIGHT))
        view.setItemDelegate(CardDelegate(view))

    def _sync_empty_state(self) -> None:
        """Show the empty page exactly when there is no workspace."""
        self._stack.setCurrentWidget(self._board if self._model.rowCount() else self._empty)

    @property
    def model(self) -> WorkspaceListModel:
        """Return the list model (used by tests and the actions layer)."""
        return self._model

    @property
    def board(self) -> QListView:
        """Return the card view (used by the actions layer to read selection)."""
        return self._board

    @property
    def header_row(self) -> QHBoxLayout:
        """Return the header layout an actions layer appends its buttons to."""
        return self._header_row

    @property
    def shows_empty_state(self) -> bool:
        """Return True when the board is empty and the empty page is shown."""
        return self._stack.currentWidget() is self._empty

    def selected_workspace(self) -> Workspace | None:
        """Return the workspace under the current selection, or ``None``."""
        selection = self._board.selectionModel()
        if selection is None:
            return None
        indexes = selection.selectedIndexes()
        if not indexes:
            return None
        return self._model.workspace_at(indexes[0])

    @property
    def workspace_actions(self) -> WorkspaceActions:
        """Return the background actions layer (used by tests)."""
        return self._actions

    def _on_new(self) -> None:
        """Collect a creation plan and start the workspace creation."""
        dialog = CreateWorkspaceDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._actions.create(dialog.plan(), dialog.workflow_dir())

    def _on_start(self) -> None:
        """Start the selected workspace, if any."""
        workspace = self.selected_workspace()
        if workspace is not None:
            self._actions.start(workspace)

    def _on_stop(self) -> None:
        """Stop the selected workspace, if any."""
        workspace = self.selected_workspace()
        if workspace is not None:
            self._actions.stop(workspace)

    def _sync_actions(self) -> None:
        """Enable each action strictly on what the selected status allows."""
        workspace = self.selected_workspace()
        status = status_for(workspace.state) if workspace is not None else None
        self._start_button.setEnabled(bool(status and status.can_start))
        self._stop_button.setEnabled(bool(status and status.can_stop))

    def _on_busy(self, busy: bool) -> None:
        """Lock the actions while a task runs, then re-derive them."""
        self._new_button.setEnabled(not busy)
        if busy:
            self._start_button.setEnabled(False)
            self._stop_button.setEnabled(False)
        else:
            self._sync_actions()

    def _on_failed(self, title: str, message: str) -> None:
        """Surface a background failure instead of letting it vanish."""
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Unsubscribe from the manager before the window goes away."""
        self._manager.remove_observer(self._notifier)
        super().closeEvent(event)


__all__ = ["MainWindow"]
