"""Paints one workspace as a card in the board.

The decision of *what* a card says belongs to
:mod:`n8n_launcher.workspaces.status` (``summary_line``, the status tone, and
``primary_action`` for the pill); this module only turns that into pixels. The
things it owns are the mapping from a
:class:`~n8n_launcher.workspaces.status.StatusTone` to a palette colour and the
geometry of the status pill — both plain functions, so a test can assert them
without rendering.

The pill geometry is deliberately a module-level function rather than a private
method: :mod:`n8n_launcher.gui.board` hit-tests the *same* rectangle to decide
whether a click landed on it. One function, one rectangle — a hit test that
disagreed with the painting would be a control that is drawn where it cannot be
clicked.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from ..core.models import Workspace
from ..gui_utils.text import ellipsize
from ..workspaces.status import StatusTone, WorkspaceStatus, primary_action, summary_line
from . import theme
from .workspace_model import WorkspaceListModel

#: The card's outer size; a card is never elastic, the grid absorbs the room.
CARD_WIDTH = 300
CARD_HEIGHT = 128
#: The same size as a value, for the grid and for tests that build a viewport.
CARD_SIZE = QSize(CARD_WIDTH, CARD_HEIGHT)

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


def card_rect(rect: QRect | QRectF) -> QRectF:
    """Return the rectangle a card is *painted* in: *rect* inset by its border.

    Shared with the board's hit test on purpose. The inset is one pixel, which is
    invisible to a user and exactly enough for a control to end up drawn outside
    the region that answers clicks.
    """
    return QRectF(rect).adjusted(_INSET, _INSET, -_INSET, -_INSET)


def pill_rect(rect: QRectF, status: WorkspaceStatus, font: QFont) -> QRectF:
    """Return the status pill's rectangle inside the already-inset card *rect*.

    *rect* is the value :func:`card_rect` returned, because the board hit-tests
    against the same rectangle it paints from.
    """
    metrics = QFontMetrics(font)
    text_width = metrics.horizontalAdvance(status.label)
    return QRectF(
        rect.right() - _PILL_PADDING_H * 2 - text_width - theme.CARD_PADDING,
        rect.top() + theme.CARD_PADDING,
        text_width + _PILL_PADDING_H * 2,
        metrics.height() + _PILL_PADDING_V,
    )


def pill_hit(rect: QRectF, status: WorkspaceStatus, font: QFont, point: QPointF | QPoint) -> bool:
    """Return True when *point* (card-local) lands on *status*'s pill.

    Empty when the pill is not a control for this state — a start or a stop
    already in flight — so the card stays inert where it has nothing to offer.
    """
    if primary_action(status.state) is None:
        return False
    return pill_rect(rect, status, font).contains(QPointF(point))


def name_text_rect(rect: QRectF, workspace: Workspace, font: QFont, pill_left: float) -> QRectF:
    """Return the rectangle of the workspace name text (same as painted)."""
    name_font = QFont(font)
    name_font.setBold(True)
    metrics = QFontMetrics(name_font)
    room = max(int(pill_left - rect.left() - theme.CARD_PADDING - _GAP), 0)
    top = rect.top() + theme.CARD_PADDING
    return QRectF(rect.left() + theme.CARD_PADDING, top, room, metrics.height())


class CardDelegate(QStyledItemDelegate):
    """Draws a workspace as a rounded card: name, summary and a status pill.

    The pill doubles as the card's one visible control when the state offers an
    action (:func:`~n8n_launcher.workspaces.status.primary_action`). The board
    sets :attr:`hovered_pill` to the index under the cursor; the ring drawn here
    is what tells the user the pill is a control and not just a label, so no
    button has to sit next to the card to advertise it.
    """

    def __init__(self, view: QAbstractItemView, parent: QObject | None = None) -> None:
        """Bind to *view* — the board that owns the cards — with nothing hovered."""
        super().__init__(parent)
        self._view = view
        self._hovered_pill = QPersistentModelIndex()

    @property
    def hovered_pill(self) -> QPersistentModelIndex:
        """Return the card whose pill is under the cursor, if any."""
        return self._hovered_pill

    @hovered_pill.setter
    def hovered_pill(self, index: QModelIndex | QPersistentModelIndex) -> None:
        """Hover *index*'s pill, repainting the old and new cards only."""
        previous = self._hovered_pill
        if previous == index:
            return
        self._hovered_pill = (
            QPersistentModelIndex(index) if index.isValid() else QPersistentModelIndex()
        )
        for changed in (previous, self._hovered_pill):
            if changed.isValid():
                # Emitted on the model, not the view: that is the signal a view
                # listens to in order to repaint one row.
                model = self._view.model()
                if model is not None:
                    model.dataChanged.emit(changed, changed)

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
        rect = card_rect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        self._paint_frame(painter, rect, selected)
        name_font = QFont(option.font)
        name_font.setBold(True)
        hovered = index == self._hovered_pill
        pill_left = self._paint_pill(painter, rect, status, option.font, hovered=hovered)
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
        *,
        hovered: bool = False,
    ) -> float:
        """Draw the status pill at the card's top-right; return its left edge.

        *hovered* draws the accent ring that marks the pill as a control. It is
        only honoured when the state actually offers an action, so a pill with
        nothing behind it never looks clickable.
        """
        pill = pill_rect(rect, status, font)
        colour = QColor(tone_color(status.tone))
        tint = QColor(colour)
        tint.setAlpha(38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint)
        painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        if hovered and primary_action(status.state) is not None:
            ring = QPen(QColor(theme.ACCENT), 1.5)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                pill.adjusted(0.75, 0.75, -0.75, -0.75), pill.height() / 2, pill.height() / 2
            )
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
        name_r = name_text_rect(rect, workspace, base_font, pill_left)
        name_metrics = QFontMetrics(name_font)
        room = max(int(name_r.width()), 0)
        name = ellipsize(workspace.name, room, name_metrics.horizontalAdvance)
        top = rect.top() + theme.CARD_PADDING
        painter.setFont(name_font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            name_r,
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


__all__ = [
    "CARD_HEIGHT",
    "CARD_SIZE",
    "CARD_WIDTH",
    "CardDelegate",
    "card_rect",
    "name_text_rect",
    "pill_hit",
    "pill_rect",
    "tone_color",
]
