"""The board: the wrapping grid, the pill hit-test, and the two card gestures.

The point of these tests is that the hit region is the *painted* pill. A control
drawn somewhere the click test does not look is the exact failure the shared
geometry in :mod:`n8n_launcher.gui.card_delegate` exists to prevent, so every
position here is derived from that geometry instead of hard-coded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QStyleOptionViewItem,
)

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.board import WorkspaceBoard
from n8n_launcher.gui.card_delegate import CARD_HEIGHT, CARD_SIZE, CARD_WIDTH, card_rect, pill_rect
from n8n_launcher.gui.workspace_model import WorkspaceListModel
from n8n_launcher.workspaces.status import WorkspaceStatus, status_for


class _Manager:
    """The only manager surface the list model reads."""

    def __init__(self, workspaces: list[Workspace]) -> None:
        self._workspaces = workspaces

    def list(self) -> list[Workspace]:
        return list(self._workspaces)


def _workspace(state: WorkspaceState = WorkspaceState.RUNNING) -> Workspace:
    return Workspace(
        id="ws",
        name="Mon workspace",
        workflows_dir=Path("/tmp/ws"),
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
        state=state,
    )


def _board(
    qt_app: QApplication, state: WorkspaceState = WorkspaceState.RUNNING
) -> tuple[WorkspaceBoard, QModelIndex]:
    """Return a laid-out board holding one card, and that card's index."""
    board = WorkspaceBoard()
    model = WorkspaceListModel(_Manager([_workspace(state)]))
    model.refresh()
    board.setModel(model)
    board.resize(CARD_SIZE)
    # The view only places its cards once it has a geometry of its own.
    board.show()
    board.viewport().resize(CARD_SIZE)
    return board, model.index(0, 0)


def _status_of(board: WorkspaceBoard, index: QModelIndex) -> WorkspaceStatus:
    """Return the status *index*'s card is painted from."""
    status = index.data(WorkspaceListModel.StatusRole)
    assert isinstance(status, WorkspaceStatus)
    return status


def _pill_point(board: WorkspaceBoard, index: QModelIndex) -> QPoint:
    """Return a viewport point at the centre of *index*'s painted pill."""
    pill = pill_rect(card_rect(board.visualRect(index)), _status_of(board, index), board.font())
    return QPoint(int(pill.center().x()), int(pill.center().y()))


def _body_point(board: WorkspaceBoard, index: QModelIndex) -> QPoint:
    """Return a viewport point on *index*'s card that is well clear of the pill."""
    rect = board.visualRect(index)
    return QPoint(int(rect.left()) + 20, int(rect.bottom()) - 20)


def _event(
    kind: QEvent.Type, board: WorkspaceBoard, point: QPoint, button: Qt.MouseButton
) -> QMouseEvent:
    """Build a mouse event at *point*, in *board*'s viewport coordinates."""
    return QMouseEvent(
        kind,
        point,
        board.viewport().mapToGlobal(point),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def _click(
    board: WorkspaceBoard, point: QPoint, button: Qt.MouseButton = Qt.MouseButton.LeftButton
) -> None:
    """Deliver a press and a release at *point*."""
    app = QApplication.instance()
    assert app is not None
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        app.sendEvent(board.viewport(), _event(kind, board, point, button))


def test_the_board_is_still_a_wrapping_icon_grid(qt_app) -> None:
    """The layout contract is unchanged; only the gestures were added."""
    board = WorkspaceBoard()
    assert isinstance(board, QListView)
    assert board.viewMode() == QListView.ViewMode.IconMode
    assert board.flow() == QListView.Flow.LeftToRight
    assert board.isWrapping() is True
    assert board.movement() == QListView.Movement.Static
    assert board.gridSize() == CARD_SIZE
    assert board.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection


def test_the_viewport_tracks_the_mouse_so_hover_can_be_seen(qt_app) -> None:
    assert WorkspaceBoard().viewport().hasMouseTracking() is True


def test_the_grid_size_matches_the_delegate_size_hint(qt_app) -> None:
    """A grid smaller than the card would clip the very pill we hit-test."""
    board = WorkspaceBoard()
    assert board.gridSize() == board.card_delegate().sizeHint(QStyleOptionViewItem(), QModelIndex())


def test_a_click_on_the_pill_activates_that_card(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.RUNNING)
    seen: list[QModelIndex] = []
    board.pillActivated.connect(seen.append)

    _click(board, _pill_point(board, index))

    assert seen == [index]


def test_a_click_away_from_the_pill_activates_nothing(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.RUNNING)
    seen: list[QModelIndex] = []
    board.pillActivated.connect(seen.append)

    _click(board, _body_point(board, index))

    assert seen == []


def test_a_pill_with_no_action_is_not_clickable(qt_app) -> None:
    """A start in flight owns the outcome, so its pill is a label only."""
    board, index = _board(qt_app, WorkspaceState.STARTING)
    seen: list[QModelIndex] = []
    board.pillActivated.connect(seen.append)

    _click(board, _pill_point(board, index))

    assert seen == []


def test_a_pill_click_still_selects_the_card(qt_app) -> None:
    """Otherwise the header action and the card would disagree on the target."""
    board, index = _board(qt_app, WorkspaceState.RUNNING)

    _click(board, _pill_point(board, index))

    assert board.currentIndex() == index


def test_hovering_the_pill_shows_the_hand_and_names_the_action(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.RUNNING)

    board.mouseMoveEvent(
        _event(QEvent.Type.MouseMove, board, _pill_point(board, index), Qt.MouseButton.NoButton)
    )

    assert board.card_delegate().hovered_pill.isValid() is True
    assert board.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert board.toolTip() == "Arrêter ce workspace"


def test_hovering_a_stopped_card_offers_to_open_it(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.STOPPED)

    board.mouseMoveEvent(
        _event(QEvent.Type.MouseMove, board, _pill_point(board, index), Qt.MouseButton.NoButton)
    )

    assert "Ouvrir" in board.toolTip()


def test_hovering_a_pill_with_no_action_offers_nothing(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.STOPPING)

    board.mouseMoveEvent(
        _event(QEvent.Type.MouseMove, board, _pill_point(board, index), Qt.MouseButton.NoButton)
    )

    assert board.toolTip() == ""
    assert board.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_leaving_the_board_drops_the_hover(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.RUNNING)
    board.mouseMoveEvent(
        _event(QEvent.Type.MouseMove, board, _pill_point(board, index), Qt.MouseButton.NoButton)
    )
    assert board.card_delegate().hovered_pill.isValid() is True

    board.leaveEvent(QEvent(QEvent.Type.Leave))

    assert board.card_delegate().hovered_pill.isValid() is False


def test_a_right_click_asks_for_the_menu_and_selects_the_card(qt_app) -> None:
    board, index = _board(qt_app, WorkspaceState.RUNNING)
    seen: list[QModelIndex] = []
    board.contextRequested.connect(seen.append)

    _click(board, _body_point(board, index), Qt.MouseButton.RightButton)

    assert seen == [index]
    assert board.currentIndex() == index


def test_a_right_click_on_empty_board_asks_for_nothing(qt_app) -> None:
    board, _ = _board(qt_app, WorkspaceState.RUNNING)
    # Widen the board so there is bare background beside the single card.
    board.viewport().resize(CARD_WIDTH * 3, CARD_HEIGHT)
    seen: list[QModelIndex] = []
    board.contextRequested.connect(seen.append)

    _click(board, QPoint(CARD_WIDTH * 2, 5), Qt.MouseButton.RightButton)

    assert seen == []


def test_a_context_menu_event_anywhere_on_the_card_asks_for_the_menu(qt_app) -> None:
    """Right-clicking the middle of a tile is what a user actually does."""
    board, index = _board(qt_app, WorkspaceState.RUNNING)
    seen: list[QModelIndex] = []
    board.contextRequested.connect(seen.append)

    centre = board.visualRect(index).center()
    board.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            centre,
            board.viewport().mapToGlobal(centre),
        )
    )

    assert seen == [index]


def test_a_context_menu_event_off_any_card_is_ignored(qt_app) -> None:
    board, _ = _board(qt_app, WorkspaceState.RUNNING)
    seen: list[QModelIndex] = []
    board.contextRequested.connect(seen.append)

    far = QPoint(board.viewport().width() + 40, board.viewport().height() + 40)
    board.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            far,
            board.viewport().mapToGlobal(far),
        )
    )

    assert seen == []


@pytest.mark.parametrize("state", list(WorkspaceState))
def test_a_point_past_the_card_belongs_to_no_workspace(qt_app, state: WorkspaceState) -> None:
    board, index = _board(qt_app, state)
    rect = board.visualRect(index)
    assert board.indexAt(QPoint(rect.right() + 50, rect.bottom() + 50)).isValid() is False


def test_the_status_under_test_is_the_one_the_card_was_built_from(qt_app) -> None:
    """Guards the helper itself: a stale status would silently pass every hit test."""
    board, index = _board(qt_app, WorkspaceState.ERROR)
    assert _status_of(board, index) == status_for(WorkspaceState.ERROR)
    assert _status_of(board, index).label == "Erreur"
    assert CARD_HEIGHT == 128
