"""The one way a dialog is built.

Twelve modal prompts used to each open their own ``tk.Toplevel``, pack their own
action bar, and wire their own Escape/Return/grab/geometry. Every one of them
was a slightly different copy, which is how the action bars came to be called
``!frame`` in some snapshots and unnamed in others, and how ``prompt_run_ci``
shipped a *second* copy of the per-module dialog setup helper that had
quietly lost the ``focus=`` argument.

This module is the shared part, and only the shared part:

* the window itself — title, background, resizability, modal grab;
* **one** action bar, named ``actions``, holding a cancel and a primary button
  (plus whatever else a dialog adds);
* ``<Escape>`` cancels, everywhere, which is the one key binding no dialog
  disagreed about;
* the result, and the ``wait()`` that returns it.

What it deliberately does **not** own is ``<Return>``. Three dialogs bind it
somewhere else — ``prompt_clone_plan`` uses it on the URL field to *load
branches* rather than to submit, and the GitHub repo picker binds it both on its
branch field and on the window — so a global binding here would silently break
them. Each dialog keeps its own, which is the one place the rule genuinely
differs.

The fields are packed straight onto the dialog rather than into a body frame: a
body frame plus the ``GUTTER`` the fields already carry would inset the content
twice, which is a real (and invisible in a unit test) change of every dialog's
width.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Literal, cast

from .layout import WindowFitter
from .theme import APP_BACKGROUND, configure_fonts
from .tokens import GUTTER, SPACE_3XL, SPACE_MD

# The gap between two buttons in a bar, and between the primary and the cancel.
ACTION_GAP = SPACE_MD


class Dialog[T](tk.Toplevel):
    """A modal prompt with one named action bar and a result.

    Subclassing ``tk.Toplevel`` captures the real widget at import time, so the
    unit suite rebases this class onto the fakes (see ``fake_dialog_bases`` in
    the test helpers) exactly as it does for ``gui.board.Card``.
    """

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        *,
        primary: str = "Valider",
        cancel: str = "Annuler",
        resizable: bool = False,
        primary_style: str = "Accent.TButton",
        bar_pady: tuple[int, int] = (0, SPACE_3XL),
        action_gap: int = ACTION_GAP,
    ) -> None:
        """Open the window, with an empty action bar carrying the two buttons.

        *primary* is the label on the confirming button — "Valider", "Créer",
        "Enregistrer", "Cloner", "Lancer", "Continuer", "J'ai collé" — and
        *primary_style* exists because one dialog's primary is a peer of its
        cancel rather than the accent, and flattening that would make the
        credentials prompt's real action look like a suggestion.

        *bar_pady* and *action_gap* are the bar's own vertical padding and its
        button gap. Both are parameters because two dialogs really do pack
        tighter than the rest — one tucks under a 14-row table, one under a
        list of credentials — and this class is not going to quietly restyle
        them on the way through.
        """
        super().__init__(parent)
        # Held explicitly rather than read back from ``master``: the test fakes
        # build a dialog without one, and a dialog that cannot name its own
        # parent cannot make itself modal against it.
        self._parent = parent
        self.title(title)
        self.configure(bg=APP_BACKGROUND)
        self.resizable(resizable, resizable)
        self._result: T | None = None
        self._cancelled = False
        #: Set by the caller once its submit function exists; ``<Return>`` and
        #: the primary button both go through this, so neither can be wired to a
        #: different function from the other.
        self.on_submit: Callable[[], None] | None = None
        # A literal name, not Tk's auto-name: the bar is the one container whose
        # path the structural parity snapshot names (``…actions.create_button``),
        # and an auto-name moves as soon as a widget is created before it.
        self.actions = tk.Frame(self, bg=APP_BACKGROUND, name="actions")
        self.actions.pack(fill="x", padx=GUTTER, pady=bar_pady)
        self._action_gap = action_gap
        self.cancel_button = self.add_action(cancel, self.cancel, gap=action_gap)
        self.primary_button = self.add_action(
            primary, self._submit, style=primary_style, gap=action_gap
        )
        self.bind("<Escape>", lambda _event: self.cancel())

    # -------------------------------------------------------------- the result
    @property
    def result(self) -> T | None:
        """What the dialog returned: the caller's value, or ``None`` if cancelled."""
        return None if self._cancelled else self._result

    def settle(self, value: T) -> None:
        """Record the answer and close. The caller calls this from its submit."""
        self._result = value
        self.destroy()

    def cancel(self) -> None:
        """Close without an answer, whatever the fields hold."""
        self._cancelled = True
        self.destroy()

    def _submit(self) -> None:
        if self.on_submit is not None:
            self.on_submit()

    # ----------------------------------------------------------------- the bar
    def add_action(
        self,
        label: str,
        command: Callable[[], None],
        *,
        style: str = "Secondary.TButton",
        side: Literal["left", "right", "top", "bottom"] = "right",
        gap: int | None = None,
    ) -> ttk.Button:
        """Add a button to the action bar and return it.

        Buttons pack from the right, so the *last* one added sits furthest left:
        the cancel and the primary are added first and end up rightmost, and an
        extra action lands to their left, which is where a dialog's other
        commands have always been. *gap* defaults to the bar's own ``action_gap``.
        """
        width = self._action_gap if gap is None else gap
        button = ttk.Button(self.actions, text=label, style=style, cursor="hand2", command=command)
        button.pack(side=side, padx=(width, 0) if width else 0)
        return button

    # ----------------------------------------------------------------- showing
    def finish(
        self,
        *,
        focus: tk.Widget | None = None,
        fitter: WindowFitter | None = None,
    ) -> None:
        """Make the window modal, size it to its content, and focus a field.

        The width is the one the content asks for, bounded by the screen and
        never shrunk afterwards: that is what ``layout.WindowFitter`` is for, and
        it owns the whole geometry string (position included) so a width is
        never applied twice. A dialog fed asynchronously — the GitHub repo
        picker — hands in the very fitter it will ask to grow, so both share one
        history.
        """
        # A dialog can be the first window against a freshly created Tk root (the
        # creation flow opens directly from the top bar); make sure the named UI
        # fonts exist on that interpreter so FONT_* strings render correctly.
        with contextlib.suppress(Exception):
            configure_fonts(self._parent)
        # Positioning is best-effort; the dialog must still open on exotic WMs.
        with contextlib.suppress(Exception):
            self.transient(cast("tk.Wm", self._parent))
            self.grab_set()
            self.update_idletasks()
            if fitter is None:
                fitter = WindowFitter(self, parent=self._parent)
            fitter.fit()
            if focus is not None:
                focus.focus_set()

    def wait(
        self, *, focus: tk.Widget | None = None, fitter: WindowFitter | None = None
    ) -> T | None:
        """Finish the setup, run the modal loop, and return the result."""
        self.finish(focus=focus, fitter=fitter)
        with contextlib.suppress(Exception):
            self.wait_window()
        return self.result
