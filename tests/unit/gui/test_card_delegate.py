"""The card renderer: the tone mapping, the shared geometry, and that it paints."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui import theme
from n8n_launcher.gui.board import WorkspaceBoard
from n8n_launcher.gui.card_delegate import (
    CARD_HEIGHT,
    CARD_WIDTH,
    CardDelegate,
    card_rect,
    pill_hit,
    pill_rect,
    tone_color,
)
from n8n_launcher.gui.workspace_model import WorkspaceListModel
from n8n_launcher.workspaces.status import StatusTone, status_for


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


def _model_with(workspace: Workspace) -> WorkspaceListModel:
    model = WorkspaceListModel(_Manager([workspace]))
    model.refresh()
    return model


def _delegate(qt_app) -> tuple[WorkspaceBoard, CardDelegate]:
    """Return a board and the delegate it owns (the delegate needs a view)."""
    board = WorkspaceBoard()
    return board, board.card_delegate()


def test_every_tone_maps_to_a_palette_colour() -> None:
    assert tone_color(StatusTone.NEUTRAL) == theme.TEXT_MUTED
    assert tone_color(StatusTone.ACTIVE) == theme.RUNNING
    assert tone_color(StatusTone.PENDING) == theme.WARNING
    assert tone_color(StatusTone.ERROR) == theme.DANGER
    assert len({tone_color(tone) for tone in StatusTone}) == len(StatusTone)


def test_size_hint_is_the_fixed_card(qt_app) -> None:
    model = _model_with(_workspace())
    _, delegate = _delegate(qt_app)
    size = delegate.sizeHint(QStyleOptionViewItem(), model.index(0, 0))
    assert size == QSize(CARD_WIDTH, CARD_HEIGHT)


def test_painting_a_workspace_puts_ink_on_the_card(qt_app) -> None:
    model = _model_with(_workspace())
    _, delegate = _delegate(qt_app)
    image = QImage(CARD_WIDTH, CARD_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected

    painter = QPainter(image)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()

    # The frame fills the whole card, so the centre pixel must be opaque.
    assert image.pixelColor(CARD_WIDTH // 2, CARD_HEIGHT // 2).alpha() > 0


def test_the_painted_rect_is_the_card_its_border_inset() -> None:
    painted = card_rect(QRect(0, 0, CARD_WIDTH, CARD_HEIGHT))
    assert painted.left() == 1 and painted.top() == 1
    assert painted.right() == CARD_WIDTH - 1
    assert painted.width() == CARD_WIDTH - 2


def test_the_pill_sits_in_the_cards_top_right_corner() -> None:
    font = QFont()
    status = status_for(WorkspaceState.RUNNING)
    pill = pill_rect(card_rect(QRect(0, 0, CARD_WIDTH, CARD_HEIGHT)), status, font)

    assert pill.right() < CARD_WIDTH - theme.CARD_PADDING
    assert pill.top() == theme.CARD_PADDING + 1
    assert pill.left() > CARD_WIDTH / 2  # never over the workspace name
    assert pill.height() > 0


def test_a_longer_label_makes_a_wider_pill() -> None:
    """The pill is sized by its text, so a state change re-measures it."""
    font = QFont()
    rect = card_rect(QRect(0, 0, CARD_WIDTH, CARD_HEIGHT))
    short = pill_rect(rect, status_for(WorkspaceState.RUNNING), font)
    long = pill_rect(rect, status_for(WorkspaceState.STARTING), font)
    assert long.width() > short.width()


def test_the_pill_hit_region_is_exactly_the_painted_pill() -> None:
    """A control drawn outside its own hit region is a control that never fires."""
    font = QFont()
    status = status_for(WorkspaceState.RUNNING)
    rect = card_rect(QRect(0, 0, CARD_WIDTH, CARD_HEIGHT))
    pill = pill_rect(rect, status, font)

    assert pill_hit(rect, status, font, pill.center()) is True
    assert pill_hit(rect, status, font, pill.topLeft()) is True
    # Just outside the pill, still inside the card.
    below = QPoint(int(pill.center().x()), int(pill.bottom()) + 2)
    assert pill_hit(rect, status, font, below) is False
    left = QPoint(int(pill.left()) - 2, int(pill.center().y()))
    assert pill_hit(rect, status, font, left) is False


@pytest.mark.parametrize(
    "state",
    [WorkspaceState.STARTING, WorkspaceState.STOPPING],
)
def test_a_pill_with_nothing_behind_it_is_not_a_target(state: WorkspaceState) -> None:
    """No action means no target: a start or stop in flight owns the outcome."""
    font = QFont()
    rect = card_rect(QRect(0, 0, CARD_WIDTH, CARD_HEIGHT))
    status = status_for(state)
    pill = pill_rect(rect, status, font)
    assert pill_hit(rect, status, font, pill.center()) is False


def _render(qt_app, delegate: CardDelegate, index, *, hovered: bool) -> QImage:
    """Paint one card and return the image, optionally with the pill hovered."""
    image = QImage(CARD_WIDTH, CARD_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    option.state = QStyle.StateFlag.State_Enabled
    painter = QPainter(image)
    try:
        if hovered:
            delegate.hovered_pill = index
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return image


def _accent_pixels(image: QImage) -> int:
    """Count pixels close to the accent colour, the ring's own colour."""
    accent = QColor(theme.ACCENT)
    hits = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - accent.red()) < 40
                and abs(pixel.green() - accent.green()) < 40
                and abs(pixel.blue() - accent.blue()) < 40
            ):
                hits += 1
    return hits


def test_hovering_the_pill_draws_a_ring(qt_app) -> None:
    """The ring is what advertises the pill as a control, with no button added."""
    model = _model_with(_workspace())
    _, delegate = _delegate(qt_app)
    index = model.index(0, 0)

    plain = _accent_pixels(_render(qt_app, delegate, index, hovered=False))
    hovered = _accent_pixels(_render(qt_app, delegate, index, hovered=True))

    assert hovered > plain


def test_a_pill_with_no_action_never_gets_a_ring(qt_app) -> None:
    model = _model_with(_workspace(WorkspaceState.STARTING))
    _, delegate = _delegate(qt_app)
    index = model.index(0, 0)

    plain = _accent_pixels(_render(qt_app, delegate, index, hovered=False))
    hovered = _accent_pixels(_render(qt_app, delegate, index, hovered=True))

    assert hovered == plain


def test_clearing_the_hover_releases_the_card(qt_app) -> None:
    model = _model_with(_workspace())
    _, delegate = _delegate(qt_app)
    index = model.index(0, 0)

    delegate.hovered_pill = index
    assert delegate.hovered_pill.isValid() is True

    delegate.hovered_pill = QModelIndex()
    assert delegate.hovered_pill.isValid() is False


def test_card_rect_accepts_both_rect_flavours() -> None:
    assert card_rect(QRectF(0, 0, CARD_WIDTH, CARD_HEIGHT)) == card_rect(
        QRect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    )
