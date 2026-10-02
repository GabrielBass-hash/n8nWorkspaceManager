"""The card renderer: the tone mapping, the fixed size, and that it paints."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui import theme
from n8n_launcher.gui.card_delegate import (
    CARD_HEIGHT,
    CARD_WIDTH,
    CardDelegate,
    tone_color,
)
from n8n_launcher.gui.workspace_model import WorkspaceListModel
from n8n_launcher.workspaces.status import StatusTone


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


def test_every_tone_maps_to_a_palette_colour() -> None:
    assert tone_color(StatusTone.NEUTRAL) == theme.TEXT_MUTED
    assert tone_color(StatusTone.ACTIVE) == theme.RUNNING
    assert tone_color(StatusTone.PENDING) == theme.WARNING
    assert tone_color(StatusTone.ERROR) == theme.DANGER
    assert len({tone_color(tone) for tone in StatusTone}) == len(StatusTone)


def test_size_hint_is_the_fixed_card() -> None:
    model = _model_with(_workspace())
    size = CardDelegate().sizeHint(QStyleOptionViewItem(), model.index(0, 0))
    assert size == QSize(CARD_WIDTH, CARD_HEIGHT)


def test_painting_a_workspace_puts_ink_on_the_card() -> None:
    model = _model_with(_workspace())
    image = QImage(CARD_WIDTH, CARD_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, CARD_WIDTH, CARD_HEIGHT)
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected

    painter = QPainter(image)
    try:
        CardDelegate().paint(painter, option, model.index(0, 0))
    finally:
        painter.end()

    # The frame fills the whole card, so the centre pixel must be opaque.
    assert image.pixelColor(CARD_WIDTH // 2, CARD_HEIGHT // 2).alpha() > 0
