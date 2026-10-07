"""The launcher's main window: the workspace board.

A thin shell over a :class:`WorkspaceListModel`. It listens to the manager
through a :class:`WorkspaceNotifier` and updates the model, so the window never
polls and never touches Docker, Git or the config store itself. The board is a
:class:`~n8n_launcher.gui.board.WorkspaceBoard`, an icon-mode view whose cards
wrap into the available room; the empty state is a separate page of a stack
rather than an item, because an empty board must not pretend to hold a row.

Three gestures reach a workspace, and each one is owned by somebody else:
a double-click opens (see :func:`~n8n_launcher.workspaces.status.can_open`), the
board's status pill runs :func:`~n8n_launcher.workspaces.status.primary_action`
— *stop* on a live card, which is the teardown a user cannot reach any other way
— and the board's right-click offers the full
:func:`~n8n_launcher.workspaces.status.card_actions` menu. None of them is a
button beside the card: the header keeps only *Nouveau*, plus the keyboard
fallback *Arrêter* for the selected card.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Workspace
from ..gui_utils.responsive import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PREFERRED_WINDOW_HEIGHT,
    PREFERRED_WINDOW_WIDTH,
    Viewport,
    initial_window_size,
)
from ..workspaces.manager import Reachable, WorkspaceManager
from ..workspaces.status import (
    WorkspaceAction,
    card_actions,
    primary_action,
    status_for,
)
from . import theme
from .actions import WorkspaceActions
from .board import WorkspaceBoard
from .browser import WebAppLaunchError, open_web_app, workspace_url
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
        self.setMinimumSize(QSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT))
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        initial = initial_window_size(
            Viewport(
                available.width() if available is not None else PREFERRED_WINDOW_WIDTH,
                available.height() if available is not None else PREFERRED_WINDOW_HEIGHT,
            )
        )
        self.resize(QSize(*initial))

        self._actions = WorkspaceActions(manager, self)
        self._model = WorkspaceListModel(
            manager,
            self,
            rename_handler=self._actions.rename_workspace,
        )
        # Declared before the header is wired: _sync_actions reads it on the very
        # first call, and a card gesture is refused while it is set.
        self._busy = False

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header(central))

        self._board = WorkspaceBoard(central)
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
        self._notifier.workspacesChanged.connect(self._on_workspaces_changed)
        self._notifier.workspaceChanged.connect(self._on_workspace_changed)

        selection = self._board.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._sync_actions)

        self._board.doubleClicked.connect(self._on_double_click)
        self._board.nameEditRequested.connect(self._on_name_edit_requested)
        self._board.pillActivated.connect(self._on_pill_activated)
        self._board.contextRequested.connect(self._on_context_requested)
        self._actions.failed.connect(self._on_failed)
        self._actions.ready.connect(self._on_ready)
        self._actions.busyChanged.connect(self._on_busy)
        self._sync_actions()

    def _on_workspaces_changed(self) -> None:
        """Reload every card, keep the selected one selected, re-derive the header.

        Two things are load-bearing here. The re-derivation: a reconcile or a
        deletion can change the selected card's state without the user touching
        anything, and a header left describing the previous state is a control
        that lies about what it does. The re-selection: ``refresh`` is a model
        reset, which drops the view's selection outright — so without this the
        header would lose its target on every reconcile or creation.
        """
        selected = self.selected_workspace()
        selected_id = selected.id if selected is not None else None
        self._model.refresh()
        if selected_id is not None:
            restored = self._model.index_of(selected_id)
            if restored.isValid():
                self._board.setCurrentIndex(restored)
        self._sync_actions()

    def _on_workspace_changed(self, workspace: Workspace) -> None:
        """Update one card in place, then re-derive the header action."""
        self._model.apply_workspace(workspace)
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
        self._stop_button = QPushButton("Arrêter", header)
        self._stop_button.setObjectName("stop")
        self._new_button.clicked.connect(self._on_new)
        self._stop_button.clicked.connect(self._on_stop)
        row.addWidget(self._new_button)
        row.addWidget(self._stop_button)
        self._header_row = row
        return header

    def _sync_empty_state(self) -> None:
        """Show the empty page exactly when there is no workspace."""
        self._stack.setCurrentWidget(self._board if self._model.rowCount() else self._empty)

    @property
    def model(self) -> WorkspaceListModel:
        """Return the list model (used by tests and the actions layer)."""
        return self._model

    @property
    def board(self) -> WorkspaceBoard:
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

    def _on_name_edit_requested(self, index: QModelIndex) -> None:
        """Start inline editing of the workspace name."""
        if index.isValid():
            self._board.edit(index)

    def _on_double_click(self, index: QModelIndex) -> None:
        """Open a workspace's n8n: start it when stopped, raise it when running.

        Double-click is the whole gesture for every state that can be opened —
        including a **running** workspace, whose n8n Docker already holds and
        which a second ``docker up`` would only race.
        """
        workspace = self._model.workspace_at(index)
        if workspace is not None and status_for(workspace.state).can_open:
            self._actions.open(workspace)

    def _on_stop(self) -> None:
        """Stop the selected workspace, if allowed."""
        workspace = self.selected_workspace()
        if workspace is not None and status_for(workspace.state).can_stop:
            self._actions.stop(workspace)

    def _run(self, workspace: Workspace, action: WorkspaceAction) -> None:
        """Dispatch *action* on *workspace*, ignoring one that is not available.

        The busy lock is checked here rather than trusted from the caller: a card
        gesture is reachable while a task runs (a right-click opens a menu, a
        pill click lands on a card whose state has not caught up), and a second
        lifecycle call would race the one in flight.
        """
        if self._busy:
            return
        status = status_for(workspace.state)
        if action is WorkspaceAction.OPEN and status.can_open:
            self._actions.open(workspace)
        elif action is WorkspaceAction.STOP and status.can_stop:
            self._actions.stop(workspace)

    def _on_pill_activated(self, index: QModelIndex) -> None:
        """Run the card's primary action when its status pill is clicked.

        The pill is the one control a card already wears, so it carries the
        action that must not need a second gesture: *stop* on a live workspace.
        Which one that is comes from
        :func:`~n8n_launcher.workspaces.status.primary_action`; this method only
        dispatches it.
        """
        workspace = self._model.workspace_at(index)
        if workspace is None:
            return
        action = primary_action(workspace.state)
        if action is not None:
            self._run(workspace, action)

    def _on_context_requested(self, index: QModelIndex) -> None:
        """Offer *index*'s card menu, built from its status and nothing else.

        Suppressed while a task runs: the menu would offer *Arrêter* on a card
        whose state says the stop in flight already owns the outcome.
        """
        workspace = self._model.workspace_at(index)
        if workspace is None or self._busy:
            return
        self._show_card_menu(
            workspace, self._board.mapToGlobal(self._board.visualRect(index).center())
        )

    def _show_card_menu(self, workspace: Workspace, position: QPoint) -> None:
        """Build and run the card's menu at *position* (viewport-independent).

        Every action is added whether or not it applies, so a disabled item tells
        the user the action exists — a missing one would read as a missing
        feature. Which items are live is :func:`card_actions`' decision alone.
        """
        menu = QMenu(self)
        actions: dict[WorkspaceAction, QAction] = {}
        for entry in card_actions(workspace.state):
            # The divider goes *above* the destructive item: separating it from the
            # safe actions is what stops *Supprimer…* reading as the third thing
            # you can do to a card that happens to be stopped.
            if entry.destructive and len(actions):
                menu.addSeparator()
            action = menu.addAction(entry.label)
            action.setEnabled(entry.enabled)
            actions[entry.action] = action
        chosen = menu.exec(position)
        if chosen is None:
            return
        if chosen is actions[WorkspaceAction.DELETE]:
            self._confirm_delete(workspace)
            return
        for candidate, action in actions.items():
            if chosen is action and candidate is not WorkspaceAction.DELETE:
                self._run(workspace, candidate)

    def _confirm_delete(self, workspace: Workspace) -> None:
        """Ask before deleting, then delete in the background.

        Deleting drops the workspace's compose stack reference and its config
        entry; the card itself is gone from the board either way, so the question
        is the only moment the user can change their mind.
        """
        answer = QMessageBox.question(
            self,
            "Supprimer le workspace",
            f"Supprimer « {workspace.name} » ? Son instance Docker et sa "
            "configuration seront perdues.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._actions.delete(workspace)

    def _sync_actions(self) -> None:
        """Enable the header stop button for the selected card, and name its target.

        The tooltip exists because a greyed button says *that* it cannot be used
        but not *which* card it means; a single disabled control in an otherwise
        empty header reads as a broken app rather than as an inapplicable one.
        """
        workspace = self.selected_workspace()
        status = status_for(workspace.state) if workspace is not None else None
        can_stop = bool(status and status.can_stop) and not self._busy
        self._stop_button.setEnabled(can_stop)
        self._stop_button.setToolTip(
            f"Arrêter « {workspace.name} »" if can_stop and workspace is not None else ""
        )

    def _on_busy(self, busy: bool) -> None:
        """Lock the actions while a task runs, then re-derive them."""
        self._busy = busy
        self._new_button.setEnabled(not busy)
        self._sync_actions()

    def _on_ready(self, reachable: Reachable) -> None:
        """Show a reachable n8n instance, raising the window it already has.

        Which of the two happens is the browser's call, matched on the
        instance's origin; the launcher only says whether the window on screen
        can still be alive.
        """
        try:
            open_web_app(
                workspace_url(reachable.workspace.port),
                reuse=not reachable.started,
            )
        except (ValueError, WebAppLaunchError) as exc:
            self._on_failed("Ouverture de n8n impossible", str(exc))

    def _on_failed(self, title: str, message: str) -> None:
        """Surface a background failure instead of letting it vanish."""
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Unsubscribe from the manager before the window goes away."""
        self._manager.remove_observer(self._notifier)
        super().closeEvent(event)


__all__ = ["MainWindow"]
