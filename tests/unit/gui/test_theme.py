"""Unit tests for the runtime font resolution (``theme.configure_fonts``)."""

from types import SimpleNamespace
from typing import ClassVar
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


def test_configure_fonts_uses_resolved_default_family() -> None:
    tk = MagicMock()
    root = SimpleNamespace(tk=tk)
    default_font = MagicMock()
    default_font.actual.return_value = "fixed"

    with (
        patch("tkinter.font.families", return_value=["fixed"]),
        patch("tkinter.font.nametofont", return_value=default_font) as resolve,
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=set()),
    ):
        assert theme.configure_fonts(root) is True

    resolve.assert_called_once_with("TkDefaultFont", root=root)
    pill = next(
        call.args
        for call in tk.call.call_args_list
        if call.args[:2] == ("font", "create") and call.args[2] == "Launcher.Pill"
    )
    assert pill[3:5] == ("-family", "fixed")


def test_configure_fonts_returns_false_when_default_family_unavailable() -> None:
    root = SimpleNamespace(tk=MagicMock())

    with (
        patch("tkinter.font.families", return_value=["fixed"]),
        patch("tkinter.font.nametofont", side_effect=RuntimeError("headless")),
    ):
        assert theme.configure_fonts(root) is False


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


def test_configure_fonts_repairs_partial_registry() -> None:
    tk = MagicMock()
    partial = {theme._FONT_SPECS[0][0]}

    with (
        patch("tkinter.font.families", return_value=["liberation sans"]),
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=partial),
    ):
        assert theme.configure_fonts(SimpleNamespace(tk=tk)) is True

    created = [call.args[2] for call in tk.call.call_args_list]
    assert theme._FONT_SPECS[0][0] not in created
    assert set(created) == {spec[0] for spec in theme._FONT_SPECS[1:]}


def test_configure_fonts_returns_false_when_registration_fails() -> None:
    tk = MagicMock()
    tk.call.side_effect = RuntimeError("cannot create font")

    with (
        patch("tkinter.font.families", return_value=["liberation sans"]),
        patch("n8n_launcher.gui.theme._registered_font_names", return_value=set()),
    ):
        assert theme.configure_fonts(SimpleNamespace(tk=tk)) is False


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


def test_registered_font_names_returns_empty_on_tcl_failure() -> None:
    tk = MagicMock()
    tk.call.side_effect = RuntimeError("headless")

    assert theme._registered_font_names(SimpleNamespace(tk=tk)) == set()


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (theme.WorkspaceState.STOPPED, "Arrêté"),
        (theme.WorkspaceState.STARTING, "Démarrage"),
        (theme.WorkspaceState.RUNNING, "En cours"),
        (theme.WorkspaceState.STOPPING, "Arrêt"),
        (theme.WorkspaceState.ERROR, "Erreur"),
    ],
)
def test_state_label_maps_workspace_states(state: theme.WorkspaceState, label: str) -> None:
    assert theme.state_label(state) == label


def test_configure_fonts_returns_false_without_tk_root() -> None:
    # Fake/headless roots (unit-test harness) must leave the fonts unregistered
    # without raising, so widgets keep working against the fakes.
    with patch("tkinter.font.families", side_effect=AttributeError):
        assert theme.configure_fonts(SimpleNamespace()) is False


# --- text_measure -------------------------------------------------------------


def test_text_measure_uses_the_interpreter_metrics() -> None:
    # A host that can measure returns the interpreter's own width, and resolves
    # the font once instead of on every call.
    class Handle:
        calls: ClassVar[list[str]] = []

        def measure(self, text: str) -> int:
            """Return a predictable width for *text*."""
            Handle.calls.append(text)
            return len(text) * 8

    created: list[str] = []

    def factory(*, root, font):
        created.append(font)
        return Handle()

    with patch("tkinter.font.Font", factory):
        measure = theme.text_measure(SimpleNamespace(), "TkDefaultFont")
        assert measure("Marketing") == 9 * 8
        assert measure("Arrêté") == 6 * 8
        # One font handle, two calls: the lookup is not repeated per string.
        assert created == ["TkDefaultFont"]
        assert Handle.calls == ["Marketing", "Arrêté"]


def test_text_measure_falls_back_without_text_metrics() -> None:
    # A fake or headless root has no font metrics: the per-character estimate
    # keeps the layout working, it only decides where a string gets cut.
    with patch("tkinter.font.Font", side_effect=AttributeError):
        measure = theme.text_measure(SimpleNamespace(), "TkDefaultFont")

    assert measure("Marketing") == len("Marketing") * theme._FALLBACK_CHAR_WIDTH
    assert measure("") == 0


def test_text_measure_falls_back_when_a_measure_raises() -> None:
    # A font that resolves but fails mid-session must not break a row render.
    class Broken:
        def measure(self, _text: str) -> int:
            """Fail the way a torn-down interpreter does."""
            raise RuntimeError("invalid command name")

    with patch("tkinter.font.Font", lambda **_kwargs: Broken()):
        measure = theme.text_measure(SimpleNamespace(), "TkDefaultFont")

    assert measure("Marketing") == len("Marketing") * theme._FALLBACK_CHAR_WIDTH


def test_text_measure_resolves_the_font_lazily() -> None:
    # The app registers its fonts in ``_apply_theme``, after the widgets are built:
    # a measure taken before that must still see the real font once it exists.
    created: list[str] = []

    def factory(*, root, font):
        created.append(font)
        return SimpleNamespace(measure=lambda text: len(text))

    with patch("tkinter.font.Font", factory):
        measure = theme.text_measure(SimpleNamespace(), "TkdRow")
        assert created == []
        assert measure("ok") == 2
        assert created == ["TkdRow"]
