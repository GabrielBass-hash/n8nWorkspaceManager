"""The workspace board: the card grid and its two card-level gestures.

The board is a :class:`QListView` that knows how to lay cards out in a wrapping
grid (nothing else does — see :mod:`n8n_launcher.gui.window`) and, on top of
that, answers two questions the cards themselves cannot:

* is the cursor over the **status pill** of a card, and if so which action does
  that state make it perform (:func:`~n8n_launcher.workspaces.status.primary_action`);
* has the user **right-clicked** a card, so the window can offer its full menu.

Both are answered against the very rectangles
:mod:`n8n_launcher.gui.card_delegate` paints — :func:`~n8n_launcher.gui.card_delegate.card_rect`
and :func:`~n8n_launcher.gui.card_delegate.pill_rect` — so a control can never
be drawn somewhere it cannot be clicked. That shared geometry is the reason this
module exists instead of a hit test buried in the window.

The board reports gestures and nothing else: it runs no Docker, Git or database
work and decides no lifecycle rule. Which action a gesture means comes from
:mod:`n8n_launcher.workspaces.status`; :mod:`n8n_launcher.gui.window` binds it
to the actions layer.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QListView, QWidget

from ..workspaces.status import WorkspaceAction, WorkspaceStatus, primary_action
from . import theme
from .card_delegate import CARD_SIZE, CardDelegate, card_rect, pill_hit
from .workspace_model import WorkspaceListModel

#: Tooltip wording for the pill, so the hover ring is never the only cue.
_PILL_TOOLTIPS: dict[WorkspaceAction, str] = {
    WorkspaceAction.OPEN: "Ouvrir ce workspace (démarre l'instance si besoin)",
    WorkspaceAction.STOP: "Arrêter ce workspace",
}


class WorkspaceBoard(QListView):
    """The card grid, plus the pill click and right-click that act on a card."""

    #: Emitted with the card whose status pill was clicked.
    pillActivated = Signal(QModelIndex)
    #: Emitted with the card that was right-clicked. The window reads the
    #: position itself: a right-click may also arrive through ``contextMenuEvent``,
    #: which carries its own point, so one signal has to serve both paths.
    contextRequested = Signal(QModelIndex)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build the grid; nothing is read until a model is attached."""
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(theme.SPACING)
        self.setGridSize(CARD_SIZE)
        self._delegate = CardDelegate(self)
        self.setItemDelegate(self._delegate)
        # Without mouse tracking the view is never told the cursor moved, and a
        # hover ring that only shows up on an unrelated repaint is not a cue.
        self.viewport().setMouseTracking(True)
        self._hovered = QPersistentModelIndex()

    def card_delegate(self) -> CardDelegate:
        """Return the card delegate that owns the hover ring and the painting."""
        return self._delegate

    # --- hover ---------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Track the pill under the cursor, then let the view behave as usual."""
        self._set_hover(self._pill_at(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Drop the hover when the cursor leaves the board entirely."""
        self._set_hover(QModelIndex())
        super().leaveEvent(event)

    def _set_hover(self, index: QModelIndex) -> None:
        """Point the delegate's ring at *index*'s pill and update the cursor."""
        self._hovered = QPersistentModelIndex(index) if index.isValid() else QPersistentModelIndex()
        self._delegate.hovered_pill = self._hovered
        hovered = self._hovered.isValid()
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if hovered else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(self._pill_tooltip(self._hovered) if hovered else "")

    def _pill_tooltip(self, index: QPersistentModelIndex) -> str:
        """Return the pill's tooltip for *index*, or an empty string."""
        status = index.data(WorkspaceListModel.StatusRole)
        if not isinstance(status, WorkspaceStatus):
            return ""
        action = primary_action(status.state)
        return _PILL_TOOLTIPS[action] if action is not None else ""

    # --- hit testing ---------------------------------------------------------

    def _pill_at(self, position: QPoint) -> QModelIndex:
        """Return the card whose pill covers *position*, or an invalid index."""
        index = self.indexAt(position)
        if not index.isValid():
            return QModelIndex()
        status = index.data(WorkspaceListModel.StatusRole)
        if not isinstance(status, WorkspaceStatus):
            return QModelIndex()
        painted = card_rect(self.visualRect(index))
        local = QPointF(position.x() - painted.left(), position.y() - painted.top())
        if not pill_hit(painted, status, self.font(), local):
            return QModelIndex()
        return index

    # --- gestures ------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Relay a pill click or a right-click, then keep the normal behaviour.

        The base call is deliberately reached even for a pill click: selecting
        the card is what a click anywhere else on it does, and a pill that
        stopped selecting would desynchronise the header action from the card.
        """
        position = event.position().toPoint()
        pill = self._pill_at(position)
        if event.button() == Qt.MouseButton.RightButton:
            index = self.indexAt(position)
            if index.isValid():
                self.setCurrentIndex(index)
                self.contextRequested.emit(index)
            event.accept()
            return
        super().mousePressEvent(event)
        if pill.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.pillActivated.emit(pill)
            event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Ask for the card's menu on a right-click anywhere on the card.

        ``mousePressEvent`` already covers the pill; this covers the rest of the
        tile, which is where a user right-clicks expecting a menu.
        """
        index = self.indexAt(event.pos())
        if not index.isValid():
            event.ignore()
            return
        self.setCurrentIndex(index)
        self.contextRequested.emit(index)
        event.accept()


__all__ = ["WorkspaceBoard"]
