"""The theme is data plus one painting call.

A colour that is not a valid hex value, or a stylesheet missing the accent, is a
visible regression that no widget test would catch — so the values are asserted
directly. ``apply_theme`` is exercised against the offscreen ``qapp``.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from n8n_launcher.gui import theme

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_every_named_colour_is_a_valid_hex_value() -> None:
    for name in ("BG", "SURFACE", "SURFACE_ALT", "BORDER", "TEXT", "TEXT_MUTED", "ACCENT"):
        value = getattr(theme, name)
        assert _HEX.match(value), f"{name}={value!r} is not a #rrggbb colour"


def test_the_dark_palette_is_actually_dark() -> None:
    palette = theme.dark_palette()
    assert palette.color(QPalette.ColorRole.Window) == QColor(theme.BG)
    assert palette.color(QPalette.ColorRole.WindowText) == QColor(theme.TEXT)
    # A dark theme whose window is lighter than its text is not a dark theme.
    assert QColor(theme.BG).lightness() < QColor(theme.TEXT).lightness()


def test_the_stylesheet_carries_the_accent_and_no_tk_colour_names() -> None:
    assert theme.ACCENT in theme.STYLESHEET
    assert "QPushButton" in theme.STYLESHEET


def test_apply_theme_installs_the_fusion_style_and_stylesheet(qapp: QApplication) -> None:
    styles = QStyleFactory.keys()
    assert "fusion" in {name.lower() for name in styles}
    theme.apply_theme(qapp)
    assert qapp.styleSheet() == theme.STYLESHEET
    # setPalette is what makes Fusion paint dark; assert it reached the app.
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(theme.BG)
