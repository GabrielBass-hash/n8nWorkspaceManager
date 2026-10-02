"""The launcher's dark theme.

A palette and a stylesheet, and nothing that decides anything. Every colour the
interface uses is named here so a future view never invents one, and the values
are plain data (hex strings, radii) so a test can assert them without a display.

``apply_theme`` is the only function that touches Qt, and it only paints: it sets
the Fusion style, a dark :class:`QPalette` and the stylesheet below. A GUI is
unusable without it, but no *decision* lives here — which is why the sizing rule
stayed in :mod:`n8n_launcher.gui_utils.text`.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

# Window and surfaces.
BG = "#1e1f22"
SURFACE = "#2b2d31"
SURFACE_ALT = "#313338"
SURFACE_HOVER = "#35373c"
BORDER = "#3f4147"

# Foreground.
TEXT = "#e6e7ea"
TEXT_MUTED = "#a3a6ad"
TEXT_DISABLED = "#6d7078"

# Accents and states (the state colours double as the workspace status dots).
ACCENT = "#5865f2"
ACCENT_HOVER = "#6b75f5"
ACCENT_PRESSED = "#4752c4"
RUNNING = "#3ba55d"
WARNING = "#faa81a"
DANGER = "#ed4245"

# Geometry.
RADIUS = 10
CARD_RADIUS = 12
SPACING = 12
CARD_PADDING = 14

#: The stylesheet applied application-wide. Kept as one string so the look is
#: described in exactly one place; a widget that needs a rule adds it here.
STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QFrame#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS}px;
}}
QFrame#card:hover {{
    background-color: {SURFACE_HOVER};
}}
QLabel#title {{
    font-size: 18px;
    font-weight: 600;
}}
QLabel#muted {{
    color: {TEXT_MUTED};
}}
QPushButton {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {SURFACE_HOVER};
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    border-color: {BG};
}}
QPushButton#primary {{
    background-color: {ACCENT};
    border: none;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#primary:pressed {{
    background-color: {ACCENT_PRESSED};
}}
QLineEdit, QPlainTextEdit, QComboBox {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def dark_palette() -> QPalette:
    """Return the dark :class:`QPalette` the Fusion style is driven with."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.Text, QColor(TEXT_DISABLED))
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DISABLED))
    palette.setColor(disabled, QPalette.ColorRole.WindowText, QColor(TEXT_DISABLED))
    return palette


def apply_theme(app: QApplication) -> None:
    """Apply the Fusion style, the dark palette and the stylesheet to *app*.

    Fusion is chosen explicitly because the native style ignores most of a
    custom palette: without it a dark palette is applied to a light widget set
    and the result is illegible.
    """
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLESHEET)


__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_PRESSED",
    "BG",
    "BORDER",
    "CARD_PADDING",
    "CARD_RADIUS",
    "DANGER",
    "RADIUS",
    "RUNNING",
    "SPACING",
    "STYLESHEET",
    "SURFACE",
    "SURFACE_ALT",
    "SURFACE_HOVER",
    "TEXT",
    "TEXT_DISABLED",
    "TEXT_MUTED",
    "WARNING",
    "apply_theme",
    "dark_palette",
]
