"""Unit tests for the runtime font resolution (``theme.configure_fonts``)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("tkinter")

from n8n_launcher.gui import theme


def test_font_constants_match_registered_font_names() -> None:
    names = {spec[0] for spec in theme._FONT_SPECS}
    assert {
        theme.FONT_TITLE,
        theme.FONT_SUBTITLE,
        theme.FONT_ROWS,
        theme.FONT_META,
        theme.FONT_STATUS,
        theme.FONT_PILL,
        theme.FONT_EMPTY_TITLE,
        theme.FONT_EMPTY_BADGE,
    } == names


def test_configure_fonts_registers_all_fonts_with_scanned_family() -> None:
    tk = MagicMock()
    root = SimpleNamespace(tk=tk)
    families = ["fixed", "liberation mono", "liberation sans"]

    with (
        patch("tkinter.font.families", return_value=families) as probe,
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=set()),
    ):
        assert theme.configure_fonts(root) is True

    probe.assert_called_once_with(root)
    calls = [call.args for call in tk.call.call_args_list]
    assert (
        "font",
        "create",
        "Launcher.Title",
        "-family",
        "liberation sans",
        "-size",
        17,
        "-weight",
        "bold",
    ) in calls
    assert ("font", "create", "Launcher.Meta", "-family", "liberation sans", "-size", 12) in calls
    assert len(calls) == len(theme._FONT_SPECS)


def test_configure_fonts_prefers_first_available_and_ignores_case() -> None:
    # Linux Tk canonicalizes family names to lowercase; the probe must still
    # pick the exact (cased) name so Tk resolves it without ambiguity.
    families = ["segoe ui", "liberation sans"]
    tk = MagicMock()

    with (
        patch("tkinter.font.families", return_value=families),
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=set()),
    ):
        assert theme.configure_fonts(SimpleNamespace(tk=tk)) is True

    title = next(
        call.args
        for call in tk.call.call_args_list
        if call.args[:2] == ("font", "create") and call.args[2] == "Launcher.Title"
    )
    assert title[3:5] == ("-family", "segoe ui")


def test_configure_fonts_falls_back_to_tkdefaultfont() -> None:
    tk = MagicMock()
    root = SimpleNamespace(tk=tk)

    with (
        patch("tkinter.font.families", return_value=["fixed"]),
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=set()),
    ):
        assert theme.configure_fonts(root) is True

    pill = next(
        call.args
        for call in tk.call.call_args_list
        if call.args[:2] == ("font", "create") and call.args[2] == "Launcher.Pill"
    )
    assert pill[3:5] == ("-family", "TkDefaultFont")


def test_configure_fonts_is_idempotent_when_names_present() -> None:
    tk = MagicMock()
    root = SimpleNamespace(tk=tk)
    registered = {spec[0] for spec in theme._FONT_SPECS}

    # First probe: no named font yet -> the call must register them. Second
    # probe: every Launcher.* name exists -> nothing may be re-created.
    with (
        patch("tkinter.font.families", return_value=["liberation sans"]),
        patch(
            "n8n_launcher.gui.theme._registered_font_names",
            side_effect=[set(), registered],
        ),
    ):
        assert theme.configure_fonts(root) is True
        tk.call.reset_mock()
        assert theme.configure_fonts(root) is True

    tk.call.assert_not_called()


def test_configure_fonts_registers_on_a_fresh_second_root() -> None:
    # The old process-wide flag made a *second* interpreter (e.g. the first
    # launch wizard's own tk.Tk()) keep whatever the first root chose without
    # re-registering its fonts. Registration must follow the interpreter: each
    # fresh root still gets the named fonts created on its own tk.call.
    tk_a = MagicMock()
    tk_b = MagicMock()
    probed: list[MagicMock] = []

    def probe(root):
        # Both interpreters start with no named UI font, as real fresh roots do.
        probed.append(root.tk)
        return set()

    with (
        patch("tkinter.font.families", return_value=["liberation sans"]),
        patch("n8n_launcher.gui.theme._registered_font_names", side_effect=probe),
    ):
        assert theme.configure_fonts(SimpleNamespace(tk=tk_a)) is True
        assert theme.configure_fonts(SimpleNamespace(tk=tk_b)) is True

    assert probed == [tk_a, tk_b]
    for tk in (tk_a, tk_b):
        creates = [c for c in tk.call.call_args_list if c.args[:2] == ("font", "create")]
        assert len(creates) == len(theme._FONT_SPECS)


def test_configure_fonts_returns_false_without_tk_root() -> None:
    # Fake/headless roots (unit-test harness) must leave the fonts unregistered
    # without raising, so widgets keep working against the fakes.
    with patch("tkinter.font.families", side_effect=AttributeError):
        assert theme.configure_fonts(SimpleNamespace()) is False
