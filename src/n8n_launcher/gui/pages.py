"""Pages: the main window's tabbed views, and the journal subject they drive.

An information view (the CI pipeline tree, the server supervision) used to open
as a ``Toplevel`` on top of the launcher: a second window the user had to move,
resize and close, hiding the very journal that explains what the view is
showing. A *page* is the same view embedded in the main window, in a tab next
to the workspace list, so the list and the journal stay on either side of it.

:class:`PageHost` owns that notebook. It holds the permanent home tab (the
workspace list) and **one tab per :class:`PageKind`**: a second click on the
same kind retargets the tab that is already open instead of stacking a new one,
so a launcher full of workspaces never accumulates a dozen CI tabs.

Two things make a page more than a repackaged widget:

* it declares a :class:`PageSubject` — the label the user reads and the tokens
  the journal filters on while the tab is active, so opening a page scopes the
  log to the same subject the page is about;
* it is a *live* view, not a one-shot rendering: it is retargeted when the
  selected workspace changes, refreshed when it becomes visible, and told to
  stop its timers (:meth:`Page.on_close`) when its tab goes away. The timers are
  the whole reason a page cannot be a plain frame the notebook forgets — Tk
  would keep firing a poll against destroyed widgets.

The page actions are journalled (see :func:`log_page_event`): the operations
behind the pages emit almost no events of their own, so without this a page's
own history would be empty and the subject filter would have nothing to show.
"""

from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from tkinter import ttk
from typing import Protocol, cast

from ..core.models import Workspace

logger = logging.getLogger(__name__)

# Title of the permanent home tab. The workspace list stays the launcher's home
# and is never filtered: a page opens *next* to it, never over it.
HOME_TITLE = "Workspaces"


class PageKind(StrEnum):
    """The information views that open as a tab instead of a pop-up window.

    One value per view, not per workspace: :class:`PageHost` keys its tabs on
    this, so the same kind always lands on the same tab.
    """

    CI = "ci"
    SERVER = "server"


@dataclass(frozen=True)
class PageSubject:
    """What the journal shows while a page is the active tab.

    ``label`` is the human name — the tab's text and the journal's chip — and
    ``tokens`` are matched **case-sensitively** against the event text (see
    :func:`n8n_launcher.gui.monitoring.matches_tokens`). The launcher's messages
    are English and regularised ("Enabled CI for Foo"), so a case-sensitive
    match is precise where a case-insensitive one would also match "credential"
    or "spécifique" on a ``ci`` token.
    """

    label: str
    tokens: tuple[str, ...]


class Page(Protocol):
    """A view embedded in the main window.

    Structural on purpose: a page is a ``tk.Frame`` subclass built against
    whatever ``tk`` module is in effect (the unit-test fakes substitute that
    module, and subclassing the real ``tkinter.Frame`` at import time would
    bypass the swap), exactly like ``gui.app.RowFrame``. The host only ever
    calls the members below, so a page that has nothing to refresh simply leaves
    ``on_show``/``on_hide``/``on_close`` empty.

    The protocol is deliberately *not* a subclass of ``tk.Widget`` — a protocol
    cannot derive from a concrete class — so :meth:`PageHost._widget` is where a
    page is declared to the notebook as the widget it really is.
    """

    # The workspace the page currently shows; the host sets it through
    # ``retarget``, and the app reads it to scope the journal beside the page.
    workspace: Workspace | None
    # The name and journal tokens this page asks for while it is active.
    subject: PageSubject
    # Set by the host when the page's content width drives the window geometry.

    def retarget(self, workspace: Workspace) -> None:
        """Re-point the page at *workspace* (the list selection changed)."""

    def on_show(self) -> None:
        """Called when the page's tab becomes the visible one."""

    def on_hide(self) -> None:
        """Called when another tab covers the page."""

    def on_close(self) -> None:
        """Called before the page's tab is removed, to stop its timers."""

    def destroy(self) -> None:
        """Release the page's widgets; the host calls it when closing the tab."""


def log_page_event(action: str, subject: PageSubject, workspace: Workspace | None) -> None:
    """Journal one user action taken on a page.

    The page filters the journal on its own subject, so the actions taken on it
    ("Ouvert", "Actualiser", "Enregistré"…) are the events that filter is
    guaranteed to find. Without them a page would filter the journal down to the
    few lines the operations behind it happen to log, which is nearly nothing.
    """
    target = f" pour « {workspace.name} »" if workspace is not None else ""
    logger.info("Page %s : %s%s", subject.label, action, target)


class PageHost:
    """The notebook holding the workspace list and one tab per open page.

    The host is the only thing the app talks to: it opens, retargets, selects
    and closes pages, and reports the active one so the journal can follow it.
    Every change of tab — whether the user clicked it or the app opened a page —
    goes through the same :attr:`on_activate` callback, which is what makes
    "clicking a page filters the log" true by construction rather than by
    remembering to filter in each call site.
    """

    def __init__(
        self,
        parent: tk.Misc,
        home_factory: Callable[[ttk.Notebook], tk.Widget] | None = None,
        *,
        home_title: str = HOME_TITLE,
        on_activate: Callable[[Page | None], None] | None = None,
    ) -> None:
        """Build the notebook, add the home tab and follow the active tab.

        *home_factory* builds the workspace-list tab, and the notebook passes
        itself to it: Tk only accepts a **descendant** of the notebook as a tab,
        and promotes it to the child frame it finds, so the list card has to be
        created from the notebook rather than re-parented into it.
        """
        self._notebook = ttk.Notebook(parent)
        self._notebook.pack(fill="both", expand=True)
        self._home = (
            tk.Frame(self._notebook) if home_factory is None else home_factory(self._notebook)
        )
        self._pages: dict[PageKind, Page] = {}
        # The page currently on screen, so a change of tab can be turned into
        # exactly one ``on_hide``/``on_show`` pair.
        self._active: Page | None = None
        self._on_activate = on_activate
        # A programmatic ``select`` fires <<NotebookTabChanged>> just like a
        # click does; the guard keeps a single activation callback per change of
        # tab, whichever caused it.
        self._notifying = False
        self._notebook.add(self._home, text=home_title)
        with contextlib.suppress(Exception):
            self._notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------- properties
    @property
    def notebook(self) -> ttk.Notebook:
        """Return the notebook, for the host widget to pack."""
        return self._notebook

    @property
    def home(self) -> tk.Widget:
        """Return the permanent workspace-list tab, for the app to fill in."""
        return self._home

    @property
    def active(self) -> Page | None:
        """Return the visible page, or ``None`` when the workspace list is shown."""
        frame = self._selected_frame()
        if frame is None or frame is self._home:
            return None
        return next((page for page in self._pages.values() if page is frame), None)

    @property
    def subject(self) -> PageSubject | None:
        """Return the journal subject of the active page, if there is one."""
        page = self.active
        return None if page is None else page.subject

    def page(self, kind: PageKind) -> Page | None:
        """Return the open page of *kind*, or ``None`` if it is not open."""
        return self._pages.get(kind)

    def kinds(self) -> tuple[PageKind, ...]:
        """Return the kinds currently open, in the order they were opened."""
        return tuple(self._pages)

    # ----------------------------------------------------------------- pages
    def open(
        self,
        kind: PageKind,
        workspace: Workspace,
        factory: Callable[[Workspace], Page],
    ) -> Page:
        """Show the page of *kind* for *workspace*, building it on first use.

        The page is built by *factory* only when its tab does not exist yet;
        otherwise the open tab is retargeted and brought back to the front, so a
        second workspace never opens a second tab of the same kind.
        """
        page = self._pages.get(kind)
        if page is not None:
            page.retarget(workspace)
            log_page_event("ciblée sur", page.subject, page.workspace)
        else:
            page = factory(workspace)
            # A page is built empty ("call retarget to fill it"), so a fresh one
            # is filled here: nothing else knows the workspace it was opened for.
            page.retarget(workspace)
            self._pages[kind] = page
            self._notebook.add(cast(tk.Widget, page), text=page.subject.label)
            log_page_event("ouverte", page.subject, page.workspace)
        self.select(kind)
        return page

    def retarget(self, workspace: Workspace) -> None:
        """Re-point every open page at *workspace*.

        Called when the list selection moves: a page follows the selection
        instead of pinning itself to the workspace it was opened for. No
        activation callback is emitted — the active page has not changed, and
        the journal's scope already follows the selection the caller re-rendered.
        """
        for page in self._pages.values():
            page.retarget(workspace)

    def close(self, kind: PageKind) -> None:
        """Remove the page of *kind*, stopping its timers first.

        A closed page stops being a filter too: when it was the visible one the
        home tab is selected, so the journal goes back to the whole log instead
        of keeping a subject whose view no longer exists.
        """
        page = self._pages.pop(kind, None)
        if page is None:
            return
        was_active = self._active is page
        if was_active:
            # The page is going away: forget it before the block below, or the
            # notification would still tell it the tab changed.
            self._active = None
        with contextlib.suppress(Exception):
            # The notification is deferred past this block on purpose: Tk picks
            # another tab by itself when the active one is forgotten, and the
            # single activation below must describe the *final* state.
            self._notifying = True
            try:
                page.on_close()
                self._notebook.forget(self._widget(page))
                page.destroy()
                if was_active:
                    self._select_frame(self._home)
            finally:
                self._notifying = False
        log_page_event("fermée", page.subject, page.workspace)
        self._notify()

    def select(self, kind: PageKind | None) -> None:
        """Bring the tab of *kind* to the front (``None`` = the workspace list)."""
        frame = self._home if kind is None else self._widget(self._pages.get(kind))
        if frame is None:
            return
        self._select_frame(frame)
        self._notify()

    # ---------------------------------------------------------------- private
    def _on_tab_changed(self, _event: object = None) -> None:
        """Report the newly selected tab (a click on a tab in the notebook)."""
        self._notify()

    def _notify(self) -> None:
        """Sync the pages' visibility, then hand the active one to the host."""
        if self._notifying:
            return
        self._notifying = True
        try:
            self._sync_pages()
            if self._on_activate is not None:
                self._on_activate(self.active)
        finally:
            self._notifying = False

    def _sync_pages(self) -> None:
        """Tell the pages that came and went, once per change of tab.

        A page polls and fetches only while it is the visible one, so this is
        driven by the same notification as the journal's subject: a page the
        user cannot see must not spend an API budget.
        """
        page = self.active
        previous = self._active
        if previous is page:
            return
        self._active = page
        if previous is not None:
            with contextlib.suppress(Exception):
                previous.on_hide()
        if page is not None:
            with contextlib.suppress(Exception):
                page.on_show()

    def _widget(self, page: Page | None) -> tk.Widget | None:
        """Return *page* as the widget the notebook holds, or ``None``."""
        return None if page is None else cast(tk.Widget, page)

    def _selected_frame(self) -> object | None:
        """Return the widget of the selected tab, or ``None`` when unknown.

        Typed as a bare object and deliberately not checked against ``tk.Misc``:
        the unit tests build the pages and the home tab on fake widgets, and an
        ``isinstance`` on the real class would report every one of them as
        unknown. Callers only ever compare it by identity.
        """
        with contextlib.suppress(Exception):
            return self._notebook.select()
        return None

    def _select_frame(self, frame: tk.Widget) -> None:
        """Select *frame*'s tab, tolerating an interpreter that refuses.

        Tk fires ``<<NotebookTabChanged>>`` from ``select`` itself, so the
        selection happens behind the guard: the explicit notification in
        :meth:`select` is then the only one the host emits, whether the tab was
        clicked by the user or chosen by the app.
        """
        with contextlib.suppress(Exception):
            self._notifying = True
            try:
                self._notebook.select(frame)
            finally:
                self._notifying = False


__all__ = [
    "HOME_TITLE",
    "Page",
    "PageHost",
    "PageKind",
    "PageSubject",
    "log_page_event",
]
