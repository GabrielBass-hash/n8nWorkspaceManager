"""Paints one workspace as a card in the board.

The decision of *what* a card says belongs to
:mod:`n8n_launcher.workspaces.status` (``summary_line`` and the status tone);
this module only turns that into pixels. The one thing it owns is the mapping
from a :class:`~n8n_launcher.workspaces.status.StatusTone` to a palette colour,
kept as a plain function so a test can assert it without rendering.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..core.models import Workspace
from ..gui_utils.text import ellipsize
from ..workspaces.status import StatusTone, WorkspaceStatus, summary_line
from . import theme
from .workspace_model import WorkspaceListModel

#: The card's outer size; a card is never elastic, the grid absorbs the room.
CARD_WIDTH = 300
CARD_HEIGHT = 128

_INSET = 1
_PILL_PADDING_H = 10
_PILL_PADDING_V = 4
_GAP = 6

_TONE_COLORS: dict[StatusTone, str] = {
    StatusTone.NEUTRAL: theme.TEXT_MUTED,
    StatusTone.ACTIVE: theme.RUNNING,
    StatusTone.PENDING: theme.WARNING,
    StatusTone.ERROR: theme.DANGER,
}


def tone_color(tone: StatusTone) -> str:
    """Return the hex colour a status tone is painted with."""
    return _TONE_COLORS[tone]


class CardDelegate(QStyledItemDelegate):
    """Draws a workspace as a rounded card: name, summary and a status pill."""

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        """Return the fixed card size (the grid needs a uniform item)."""
        return QSize(CARD_WIDTH, CARD_HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Paint the card for *index*, or defer to the base for foreign rows."""
        workspace = index.data(WorkspaceListModel.WorkspaceRole)
        status = index.data(WorkspaceListModel.StatusRole)
        if not isinstance(workspace, Workspace) or not isinstance(status, WorkspaceStatus):
            super().paint(painter, option, index)
            return

        self.initStyleOption(option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(_INSET, _INSET, -_INSET, -_INSET)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        self._paint_frame(painter, rect, selected)
        name_font = QFont(option.font)
        name_font.setBold(True)
        pill_left = self._paint_pill(painter, rect, status, option.font)
        self._paint_texts(painter, rect, workspace, option.font, name_font, pill_left)
        painter.restore()

    @staticmethod
    def _paint_frame(painter: QPainter, rect: QRectF, selected: bool) -> None:
        """Fill the card and draw its border, accenting it when selected."""
        border = theme.ACCENT if selected else theme.BORDER
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(theme.SURFACE_ALT))
        painter.drawRoundedRect(rect, theme.CARD_RADIUS, theme.CARD_RADIUS)

    @staticmethod
    def _paint_pill(
        painter: QPainter,
        rect: QRectF,
        status: WorkspaceStatus,
        font: QFont,
    ) -> float:
        """Draw the status pill at the card's top-right; return its left edge."""
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(status.label)
        pill = QRectF(
            rect.right() - _PILL_PADDING_H * 2 - text_width - theme.CARD_PADDING,
            rect.top() + theme.CARD_PADDING,
            text_width + _PILL_PADDING_H * 2,
            metrics.height() + _PILL_PADDING_V,
        )
        colour = QColor(tone_color(status.tone))
        tint = QColor(colour)
        tint.setAlpha(38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        painter.setPen(colour)
        painter.setFont(font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, status.label)
        return pill.left()

    @staticmethod
    def _paint_texts(
        painter: QPainter,
        rect: QRectF,
        workspace: Workspace,
        base_font: QFont,
        name_font: QFont,
        pill_left: float,
    ) -> None:
        """Draw the name (left of the pill) and the summary line under it."""
        name_metrics = QFontMetrics(name_font)
        room = max(int(pill_left - rect.left() - theme.CARD_PADDING - _GAP), 0)
        name = ellipsize(workspace.name, room, name_metrics.horizontalAdvance)
        top = rect.top() + theme.CARD_PADDING
        painter.setFont(name_font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(rect.left() + theme.CARD_PADDING, top, room, name_metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )

        body_font = QFont(base_font)
        body_font.setBold(False)
        body_metrics = QFontMetrics(body_font)
        summary_room = int(rect.width() - theme.CARD_PADDING * 2)
        summary = ellipsize(summary_line(workspace), summary_room, body_metrics.horizontalAdvance)
        painter.setFont(body_font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            QRectF(
                rect.left() + theme.CARD_PADDING,
                top + name_metrics.height() + _GAP,
                summary_room,
                body_metrics.height(),
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            summary,
        )


__all__ = ["CARD_HEIGHT", "CARD_WIDTH", "CardDelegate", "tone_color"]
