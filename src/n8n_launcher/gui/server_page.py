"""The read-only server supervision page (a notebook tab, not a dialog).

The window this lives in is shared with the workspace list and the journal, so
a page cannot be a modal overlay any more. ``ServerPage`` owns nothing but the
view: it asks the host for a snapshot (cached or freshly read) and renders what
it is handed. All SSH work stays on the app's background workers, and the
freshness window (20 s per workspace) plus the single in-flight read live there
too, so a page that is retargeted while a read is running never stacks a second
one.

The page deliberately has **no timer**. Four SSH commands per tick is a price
the user has to be willing to pay, so the snapshot is read when the tab opens,
when the selection moves to another workspace, and when "Actualiser" is
pressed — never on its own cadence.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Protocol

from n8n_launcher.core.models import Workspace
from n8n_launcher.gui import monitoring
from n8n_launcher.gui.monitoring import ServerSnapshot
from n8n_launcher.gui.pages import PageSubject, log_page_event
from n8n_launcher.gui.theme import APP_BACKGROUND, FONT_META, TEXT_MUTED

SERVER_SUBJECT = PageSubject("Serveur", ("Serveur", "Déploiement"))


class ServerReader(Protocol):
    """The host callback that reads a workspace's server, off the Tk thread.

    ``force`` skips the host's freshness window: it is the "Actualiser" button.
    Declared as a protocol so the page can ask for a forced read without
    depending on the app's signature.
    """

    def __call__(
        self,
        workspace: Workspace,
        page: ServerPage,
        *,
        force: bool = False,
    ) -> None: ...


NO_SERVER_NOTE = (
    "Ce workspace n'a pas de serveur configuré. Définissez-en un via "
    "« Configurer le serveur… » pour superviser son déploiement et ses exécutions."
)


class ServerPage(tk.Frame):
    """One workspace's server snapshot, as a notebook tab.

    The page keeps the ``Page`` contract (``retarget``/``on_show``/``on_hide``/
    ``on_close``) so ``PageHost`` drives it like every other page. Visibility is
    what starts a read: a hidden tab would spend four SSH round trips on data
    nobody is looking at.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        source: Callable[[Workspace], ServerSnapshot],
        refresh: ServerReader,
        available: Callable[[Workspace], bool] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        """Build the page; call :meth:`retarget` to fill it."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self.subject = SERVER_SUBJECT
        self.workspace: Workspace | None = None
        # True while this tab is the visible one: a hidden page reads nothing.
        self._active = False
        self._closed = False
        self._source = source
        self._refresh_cb = refresh
        self._available = available
        self._on_status = on_status

        header = tk.Frame(self, bg=APP_BACKGROUND)
        header.pack(fill="x", padx=18, pady=(14, 0))
        self._title = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._title.pack(side="left", expand=True, fill="x")
        # ``force`` skips the host's freshness window: the user asked for it.
        ttk.Button(
            header,
            text="Actualiser",
            style="Secondary.TButton",
            command=self.force_refresh,
        ).pack(side="right")

        self.panel = monitoring.ServerPanel(self)
        self.panel.pack(fill="both", expand=True)

    # ------------------------------------------------------------------- page
    def retarget(self, workspace: Workspace) -> None:
        """Follow the list selection to *workspace*, from the host's cache.

        The cache is served first so the tab is never blank while a read runs;
        a visible tab then asks for a fresh one, since what it shows is for the
        workspace that was selected a moment ago.
        """
        self.workspace = workspace
        self._title.config(text=f"Supervision du serveur — « {workspace.name} »")
        if not self._readable(workspace):
            self._apply_note(NO_SERVER_NOTE)
            return
        self.panel.apply(self._source(workspace))
        if self._active:
            self._refresh_cb(workspace, self)

    def on_show(self) -> None:
        """Read the server when the tab becomes the visible one."""
        self._active = True
        if self._closed or self.workspace is None or not self._readable(self.workspace):
            return
        self._refresh_cb(self.workspace, self)

    def on_hide(self) -> None:
        """Stop reading: nothing the page polls can be seen any more."""
        self._active = False

    def on_close(self) -> None:
        """Refuse every later render: the tab is being destroyed."""
        self._closed = True
        self._active = False

    # ----------------------------------------------------------------- public
    def force_refresh(self) -> None:
        """Re-read the server now, whatever the freshness window says."""
        if self._closed or self.workspace is None or not self._readable(self.workspace):
            return
        self._refresh_cb(self.workspace, self, force=True)

    def apply_snapshot(self, workspace_id: str, snapshot: ServerSnapshot) -> None:
        """Render a snapshot the host read for *workspace_id*.

        A read takes seconds over SSH, and the user can move the selection in
        the meantime: an answer for a previous workspace is cached by the host
        but never rendered here, which is the same stale-tick guard the CI page
        applies to its own poll.
        """
        if self._closed or self.workspace is None or self.workspace.id != workspace_id:
            return
        self.panel.apply(snapshot)
        verdict = "sain" if snapshot.healthy else "à vérifier"
        if self._on_status is not None:
            self._on_status(f"Supervision « {self.workspace.name} » — {verdict}")
        log_page_event(verdict, self.subject, self.workspace)

    def note_text(self) -> str:
        """Return the text currently rendered, for tests and the status line."""
        return self.panel.text()

    # ---------------------------------------------------------------- private
    def _readable(self, workspace: Workspace) -> bool:
        """Return whether *workspace* has a server deployment to read."""
        if self._available is None:
            return True
        try:
            return bool(self._available(workspace))
        except Exception:
            # An unreadable config is not a reason to crash a tab: the note is
            # shown instead, and the next retarget decides again.
            return False

    def _apply_note(self, note: str) -> None:
        """Show *note* in place of a snapshot, without a health verdict."""
        self.panel.apply(ServerSnapshot(error=None, note=note))


__all__ = ["NO_SERVER_NOTE", "SERVER_SUBJECT", "ServerPage", "ServerReader"]
