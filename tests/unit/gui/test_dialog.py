"""Unit tests for the shared modal base in :mod:`n8n_launcher.gui.dialog`.

The prompts that use it are covered where they live (``test_dialogs.py``,
``test_ci_edit.py``); what is tested here is the base's own contract: the named
action bar, the primary/cancel routing, ``<Escape>``, the result, and the
modal sizing the prompts used to each re-implement.
"""

from __future__ import annotations

from tkinter import TclError
from unittest.mock import patch

from helpers import FakeRoot, FakeTk, FakeTtk, fire

from n8n_launcher.gui.dialog import Dialog


def _dialog(gui_mocks, **kwargs) -> Dialog:
    """Build a dialog on the fakes and return it, without waiting."""
    dialog: Dialog[str] = Dialog(FakeRoot(), "Titre", **kwargs)
    return dialog


def _bar(dialog) -> list:
    return list(dialog.actions.children)


def _by_text(dialog, text: str):
    return next(b for b in _bar(dialog) if (b.text or "").startswith(text))


def test_the_action_bar_is_named_so_its_path_is_stable(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)

    assert dialog.actions._name == "actions"
    assert dialog.actions in dialog.children


def test_the_bar_holds_the_cancel_then_the_primary_rightmost_first(gui_mocks) -> None:
    dialog = _dialog(gui_mocks, primary="Créer")

    labels = [b.text for b in _bar(dialog)]
    assert labels == ["Annuler", "Créer"]
    # Both are packed from the right, so the cancel ends up rightmost.
    assert all(b._pack_options.get("side") == "right" for b in _bar(dialog))


def test_the_primary_is_the_accent_by_default(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)

    assert dialog.primary_button.style == "Accent.TButton"
    assert dialog.cancel_button.style == "Secondary.TButton"


def test_a_dialog_whose_primary_is_a_peer_keeps_it_peer(gui_mocks) -> None:
    """The credentials prompt's "J'ai collé" is not more committing than "Annuler"."""
    dialog = _dialog(gui_mocks, primary="J'ai collé", primary_style="Secondary.TButton")

    assert dialog.primary_button.style == "Secondary.TButton"


def test_extra_actions_land_to_the_left_of_the_pair(gui_mocks) -> None:
    dialog = _dialog(gui_mocks, primary="J'ai collé")
    dialog.add_action("Copier le JSON", lambda: None)

    assert [b.text for b in _bar(dialog)] == ["Annuler", "J'ai collé", "Copier le JSON"]


def test_an_action_can_be_packed_on_the_left_instead(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)
    dialog.add_action("Créer sur GitHub…", lambda: None, side="left")

    extra = _by_text(dialog, "Créer sur GitHub")
    assert extra._pack_options.get("side") == "left"


def test_the_bar_packs_at_the_gap_it_was_given(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)

    for button in _bar(dialog):
        assert button._pack_options.get("padx") == (8, 0)

    tighter = _dialog(gui_mocks, action_gap=6)
    for button in _bar(tighter):
        assert button._pack_options.get("padx") == (6, 0)


def test_the_bar_packs_at_the_vertical_padding_it_was_given(gui_mocks) -> None:
    assert _dialog(gui_mocks).actions._pack_options.get("pady") == (0, 16)

    tucked = _dialog(gui_mocks, bar_pady=(6, 14))
    assert tucked.actions._pack_options.get("pady") == (6, 14)


def test_the_primary_button_calls_on_submit(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)
    calls: list[str] = []
    dialog.on_submit = lambda: calls.append("submit")

    dialog.primary_button.command()

    assert calls == ["submit"]


def test_the_primary_button_is_inert_until_on_submit_is_set(gui_mocks) -> None:
    """No silent crash: a dialog with nothing to submit simply does nothing."""
    dialog = _dialog(gui_mocks)

    dialog.primary_button.command()

    assert dialog.result is None
    assert not dialog.destroyed


def test_settle_records_the_answer_and_closes(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)

    dialog.settle("dev")

    assert dialog.result == "dev"
    assert dialog.destroyed


def test_cancel_discards_any_answer(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)
    dialog.settle("dev")

    dialog.cancel()

    assert dialog.result is None


def test_escape_cancels(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)
    dialog.settle("dev")

    fire(dialog, "<Escape>", None)

    assert dialog.result is None
    assert dialog.destroyed


def test_return_is_left_to_the_caller(gui_mocks) -> None:
    """A global <Return> would break prompt_clone_plan, which loads branches."""
    dialog = _dialog(gui_mocks)

    assert "<Return>" not in dialog._bindings


def test_a_resizable_dialog_is_configured_resizable(gui_mocks) -> None:
    sizes: list[tuple] = []
    with patch.object(gui_mocks.tk.Toplevel, "resizable", lambda self, *a: sizes.append(a)):
        _dialog(gui_mocks)
        _dialog(gui_mocks, resizable=True)

    assert sizes == [(False, False), (True, True)]


def test_finish_registers_fonts_on_the_parent_root(gui_mocks) -> None:
    """A dialog can be the first window on a fresh root: its fonts must exist."""
    dialog = _dialog(gui_mocks)

    with patch("n8n_launcher.gui.dialog.configure_fonts") as fonts:
        dialog.finish()

    fonts.assert_called_once_with(dialog._parent)


def test_finish_focuses_the_widget_it_is_given(gui_mocks) -> None:
    dialog = _dialog(gui_mocks)
    entry = gui_mocks.tk.Entry(dialog)
    focused: list = []
    entry.focus_set = lambda: focused.append(entry)  # type: ignore[method-assign]

    dialog.finish(focus=entry)

    assert focused == [entry]


def test_wait_returns_what_the_caller_settled(gui_mocks) -> None:
    dialog: Dialog[str] = Dialog(FakeRoot(), "Titre")
    dialog.on_submit = lambda: dialog.settle("répondu")

    with patch.object(FakeTk.Toplevel, "wait_window", lambda self: dialog.on_submit()):
        assert dialog.wait() == "répondu"


def test_wait_returns_none_when_the_user_cancels(gui_mocks) -> None:
    dialog: Dialog[str] = Dialog(FakeRoot(), "Titre")
    gui_mocks.tk.Toplevel.cancel_on_wait = True
    try:
        assert dialog.wait() is None
    finally:
        gui_mocks.tk.Toplevel.cancel_on_wait = False


def test_wait_survives_a_window_manager_that_refuses_to_position(gui_mocks) -> None:
    """Positioning is best-effort; a dialog must still open on an exotic WM."""
    dialog: Dialog[str] = Dialog(FakeRoot(), "Titre")

    with (
        patch.object(
            FakeTk.Toplevel, "transient", lambda self, root: (_ for _ in ()).throw(TclError())
        ),
        patch.object(FakeTk.Toplevel, "wait_window", lambda self: None),
    ):
        assert dialog.wait() is None
    assert not dialog.destroyed


def test_the_buttons_are_created_on_the_fakes_not_a_real_tk(gui_mocks) -> None:
    """A regression guard: the base must build through the module's own names."""
    _dialog(gui_mocks)

    assert all(isinstance(b, FakeTtk.Button) for b in FakeTtk.Button.instances)
