"""The dashboard: the workspace list, the page dock and the journal, side by side.

An information view (the CI pipeline tree, the server supervision) used to open
in two nested ``ttk.Notebook``s: one tab per view next to the workspace list, and
two more tabs inside the CI view itself. Six widgets and a list of names is a
browser's tab strip, and it hid the journal — the very thing that explains what
the view is showing — behind whichever tab happened to be selected.

This module replaces both notebooks with a **board**: three columns in one
horizontal ``ttk.PanedWindow``, no tab bar anywhere.

    ┌─────────────────┬──────────────────────┬──────────────────┐
    │ Workspaces      │ pages (the dock)     │ Journal         │
    │ (always)        │ one card each,       │ (collapsible     │
    │                 │ all visible at once  │  to a rail)      │
    └─────────────────┴──────────────────────┴──────────────────┘

Two constraints shape everything below, and both were **measured on a real Tk**
rather than assumed:

* ``ttk::panedwindow``'s ``-orient`` is read-only, so a single paned window can
  never be turned from horizontal to vertical. The board therefore never
  restacks: the three columns stay side by side, and what changes when the window
  narrows is *how much each one gets*.
* Tk refuses to re-parent a widget (``can't pack ".frame" inside ".frame2"``), so
  a widget is bound to its parent for life. The dock is therefore built once, and
  a page is shown by *adding* it to the paned window, never by moving it there.

:class:`Board` drives the split itself, because a ``ttk.PanedWindow`` never
re-reads a pane's request (``weight`` only says who absorbs the leftover; a
``weight=0`` pane stays frozen at whatever its first layout gave it). It decides
the three widths from the room it actually has, in the order of what a user
cares about, and applies them through ``sashpos`` — see :meth:`Board.reflow`.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk

from ..core.models import Workspace
from .layout import bind_ellipsize
from .pages import Page, PageKind, PageSubject, log_page_event
from .theme import APP_BACKGROUND, FONT_CARD, TEXT_PRIMARY
from .tokens import SPACE_HAIRLINE, SPACE_MD, SPACE_XL

# --------------------------------------------------------------------------
# Breakpoints. These are the widths at which a column stops being usable, not
# the widths the content happens to need: the content is fitted to the room (see
# ``gui.layout``), the room is what these bound.

# The list stays a column of ellipsized names and chips down to this.
LIST_MINIMUM = 240
# A page card narrower than this cannot show a table and its buttons at once.
DOCK_MINIMUM = 420
# The journal is a search field, a table and a detail pane; below this it cannot
# show a whole column of a log line.
JOURNAL_MINIMUM = 340
# What the journal keeps when it is collapsed: its download icon, and the button
# that expands it again.
JOURNAL_RAIL = 46

# What a sash handle and a pane's own border cost, per pane. Measured on the
# real widget (a ttk pane is what the window has left *minus* this), and applied
# as a constant because the alternative is asking the widget, which answers from
# the layout it last computed and so reports the pre-split value.
PANE_CHROME = 12

# How long the board waits before re-deciding the split, in milliseconds: the
# same coalescing the table fitter uses, so a window drag is one reflow.
REFLOW_DEBOUNCE_MS = 80


@dataclass(frozen=True)
class Split:
    """The three column widths one reflow decided.

    A value, not an instruction: :meth:`Board.reflow` computes it (purely, from
    the room and the dock's contents) and then applies it, so what the board would
    do can be asserted on without moving a single sash. *journal_rail* is part
    of the decision rather than a side effect, which is why a reflow is
    reproducible.
    """

    list: int
    dock: int
    journal: int
    journal_rail: bool


def _pending_title(kind: PageKind) -> str:
    """Return the title a card shows before its page has been built.

    A card is created before the page inside it (the page is born in the card's
    body), so it needs a name for the few milliseconds between the two — and a
    kind is a stable enough placeholder that no frame is ever shown untitled.
    """
    return kind.value.capitalize()


class Card(tk.Frame):
    """A titled container: a header that stays on one line, and a body.

    The header is what a notebook tab used to be — the thing naming the view and
    carrying its actions — with two differences: it cannot be *selected* (nothing
    is hidden behind it), and it lives with the content it names. The title is a
    plain label that ellipsizes, so a long workspace name in a card header can
    never widen the card.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str = "",
        on_close: Callable[[], None] | None = None,
        measure: Callable[[str], int] | None = None,
    ) -> None:
        """Build an empty card; the content goes in :meth:`body`."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self._header = tk.Frame(self, bg=APP_BACKGROUND)
        self._header.pack(fill="x", padx=SPACE_XL, pady=(SPACE_MD, SPACE_HAIRLINE))
        self._title_text = title
        self._title = tk.Label(
            self._header,
            text=title,
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_CARD,
            anchor="w",
        )
        if measure is not None:
            bind_ellipsize(self._title, lambda: self._title_text, measure)
        self._title.pack(side="left", fill="x", expand=True)
        if on_close is not None:
            ttk.Button(
                self._header,
                text="x",
                width=3,
                style="Secondary.TButton",
                cursor="hand2",
                command=on_close,
            ).pack(side="right")
        self._body = tk.Frame(self, bg=APP_BACKGROUND)
        self._body.pack(fill="both", expand=True)

    def body(self) -> tk.Frame:
        """Return the frame a card's content must be packed into."""
        return self._body

    @property
    def title(self) -> str:
        """Return the card's current title, the way the header shows it."""
        return self._title_text

    def set_title(self, title: str) -> None:
        """Rename the card (a page retargeted to another workspace)."""
        self._title_text = title
        with contextlib.suppress(Exception):
            self._title.config(text=title)


class PageDock:
    """The second space: one card per open page, every card visible at once.

    Where the left pane used to be a notebook with one tab per view, the dock
    keeps a vertical ``ttk.PanedWindow`` with one
    :class:`Card` per open :class:`PageKind` — so two open pages are two panels
    on screen rather than two things to click between, which is the whole point
    of a dashboard. With at most two kinds there is never a stack to scroll, and
    the sash between two cards is the user resizing the dashboard.

    A card *is* the pane: the page is built into the card's body, so the header
    that names it and the content it holds can never drift apart. (Tk refuses to
    re-parent a widget, so the page is created in the card from the start and
    never moved afterwards.)

    The dock owns exactly the lifecycle the notebook owned: a page is built on
    first use, retargeted on every selection change, told when it becomes or
    stops being the *focused* card, and destroyed with its card.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_activate: Callable[[Page | None], None] | None = None,
        measure: Callable[[str], int] | None = None,
    ) -> None:
        """Build the empty dock; it is added to the board only when it is used.

        *on_activate* receives the focused page (or ``None`` once the last card
        is closed) so the host can point the journal at that page's subject.
        *measure* is the text measurement the card headers ellipsize with.
        """
        self._pane = ttk.PanedWindow(parent, orient="vertical")
        self._pages: dict[PageKind, Page] = {}
        self._cards: dict[PageKind, Card] = {}
        self._focus: PageKind | None = None
        self._active: Page | None = None
        self._on_activate = on_activate
        self._measure = measure
        # Set by the board once it owns the dock: opening or closing a card
        # changes how many columns the dashboard has, which is the board's call
        # and not the dock's.
        self._on_change: Callable[[], None] | None = None
        # Only the dock's own callbacks are guarded: ``on_activate`` is the
        # host's, and a page that raises while being notified must not leave the
        # dock half-updated. A change requested *from* one of them is answered
        # after the current publication instead of inside it.
        self._notifying = False
        self._notify_again = False

    def bind_changes(self, on_change: Callable[[], None]) -> None:
        """Tell the dock to report that its column count changed.

        The board does this so the dock's pane is added or forgotten as soon as
        the first card opens or the last one closes, instead of waiting for the
        next reflow to notice.
        """
        self._on_change = on_change

    # ------------------------------------------------------------- properties
    @property
    def pane(self) -> ttk.PanedWindow:
        """Return the paned window holding the cards, for the board to add."""
        return self._pane

    @property
    def active(self) -> Page | None:
        """Return the focused page, or ``None`` when the dock is empty."""
        return self._pages.get(self._focus) if self._focus is not None else None

    @property
    def subject(self) -> PageSubject | None:
        """Return the journal subject of the focused page, if there is one."""
        page = self.active
        return None if page is None else page.subject

    def page(self, kind: PageKind) -> Page | None:
        """Return the open page of *kind*, or ``None`` if it is not open."""
        return self._pages.get(kind)

    def card(self, kind: PageKind) -> Card | None:
        """Return the card holding the page of *kind*, or ``None`` if closed."""
        return self._cards.get(kind)

    def kinds(self) -> tuple[PageKind, ...]:
        """Return the kinds currently open, in the order they were opened."""
        return tuple(self._pages)

    @property
    def empty(self) -> bool:
        """Whether the dock holds no page (the board hides its pane)."""
        return not self._pages

    # ------------------------------------------------------------------ pages
    def open(
        self,
        kind: PageKind,
        workspace: Workspace,
        factory: Callable[[Workspace, tk.Misc], Page],
    ) -> Page:
        """Show the page of *kind* for *workspace*, building it on first use.

        *factory* builds the page **into the card's body**, which is why it takes
        the parent: Tk cannot move a widget into another parent afterwards, so a
        page is born in the card that will hold it. A second workspace reuses the
        open card — the page is retargeted and raised, never duplicated — so
        walking the list does not multiply cards.
        """
        page = self._pages.get(kind)
        if page is not None:
            page.retarget(workspace)
            self._raise(kind)
            log_page_event("ciblée sur", page.subject, page.workspace)
        else:
            card = Card(
                self._pane,
                title=_pending_title(kind),
                on_close=lambda: self.close(kind),
                measure=self._measure,
            )
            page = factory(workspace, card.body())
            # A page is built empty ("call retarget to fill it"), so a fresh one
            # is filled here: nothing else knows the workspace it was opened for.
            page.retarget(workspace)
            card.set_title(page.subject.label)
            self._pages[kind] = page
            self._cards[kind] = card
            self._pane.add(card, weight=3)
            log_page_event("ouverte", page.subject, page.workspace)
        self.focus(kind)
        self._changed()
        return page

    def close(self, kind: PageKind) -> None:
        """Remove the page of *kind*, stopping its timers first.

        A closed page stops being a filter too: with no page left to focus the
        dock notifies ``None``, so the journal goes back to the whole log instead
        of keeping a subject whose view no longer exists.
        """
        page = self._pages.pop(kind, None)
        card = self._cards.pop(kind, None)
        if page is None:
            return
        with contextlib.suppress(Exception):
            page.on_close()
            if card is not None:
                self._pane.forget(card)
            page.destroy()
            if card is not None:
                card.destroy()
        if self._focus is kind:
            self._focus = next(reversed(self._pages), None)
        log_page_event("fermée", page.subject, page.workspace)
        self._notify()
        self._changed()

    def focus(self, kind: PageKind) -> None:
        """Make the page of *kind* the focused one (its subject filters the log).

        The focused card is the one whose actions the user is looking at, so it
        is also the one the journal filters on. Focusing raises the card: with two
        cards open, the one being acted on comes to the top rather than being
        selected behind the other.

        Focusing the card that already has the focus does nothing at all. Nothing
        moved, so there is no new subject and no new visibility, and the host is
        not told about a change that did not happen.
        """
        if kind not in self._pages or kind is self._focus:
            return
        self._focus = kind
        self._raise(kind)
        self._notify()

    def retarget(self, workspace: Workspace) -> None:
        """Re-point every open page at *workspace*.

        Called when the list selection moves: a page follows the selection
        instead of pinning itself to the workspace it was opened for. No
        activation is emitted — the focused page has not changed, and the
        journal's scope already follows the selection the caller re-rendered.
        """
        for kind, page in self._pages.items():
            page.retarget(workspace)
            card = self._cards.get(kind)
            if card is not None:
                card.set_title(page.subject.label)

    # ---------------------------------------------------------------- private
    def _raise(self, kind: PageKind) -> None:
        """Bring the card of *kind* to the top of the stack.

        ``tkraise`` rather than re-ordering the paned window: moving a pane would
        renumber the sashes the board drives, and a card the user just clicked is
        already on top of the stack with at most two cards open.
        """
        card = self._cards.get(kind)
        if card is None:
            return
        with contextlib.suppress(Exception):
            card.tkraise()

    def _sync_visibility(self) -> None:
        """Tell the pages that came and went, once per change of focus.

        A page polls and fetches only while its card is the focused one, so this
        is driven by the same notification as the journal's subject: a card the
        user is not working on must not spend an API budget. Visibility used to
        mean "the selected tab", which was the only way to tell; with every card
        on screen at once, *focus* is what replaces it.
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

    def _publish(self) -> None:
        """Sync the pages' visibility, then hand the focused one to the host."""
        self._sync_visibility()
        if self._on_activate is not None:
            self._on_activate(self.active)

    def _notify(self) -> None:
        """Publish the focused page, coalescing a request made inside one.

        A host callback that opens or closes a card from inside this one must
        not interleave with the sync — and its request is *not* dropped, because
        the page it just opened has to be shown itself: remembering it and
        answering once the dock is consistent again is both the only safe order
        and the only correct one.
        """
        if self._notifying:
            self._notify_again = True
            return
        self._notifying = True
        try:
            self._publish()
            if self._notify_again:
                self._notify_again = False
                self._publish()
        finally:
            self._notifying = False

    def _changed(self) -> None:
        """Report that the dock gained or lost a card, best effort."""
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change()


class Board:
    """The three columns, and the split that keeps them all usable.

    The board is what makes the launcher responsive: it knows how wide the window
    is and how much room each column needs, and it hands each one what is left
    after the others have taken theirs. Nothing below ever asks the *window* for
    more — a table that cannot be shown at its natural width is compressed to its
    minimums instead (see ``gui.layout``), and a column that cannot be shown at
    all is collapsed rather than left half-cut.

    The order of sacrifice is deliberate: the journal gives up first (a rail with
    its download icon still exports the whole history), then the list (a column of
    ellipsized names stays usable down to :data:`LIST_MINIMUM`), and the dock is
    last because it is the view the user just opened.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        list_factory: Callable[[tk.Misc], tk.Widget],
        journal_factory: Callable[[tk.Misc], tk.Widget],
        on_activate: Callable[[Page | None], None] | None = None,
        on_journal_toggle: Callable[[bool], None] | None = None,
        measure: Callable[[str], int] | None = None,
    ) -> None:
        """Build the paned window, its three columns and the dock between them.

        *list_factory* and *journal_factory* build the two permanent columns from
        the paned window itself, because Tk only accepts a **descendant** of the
        paned window as a pane and promotes it to the child frame it finds. The
        dock needs no factory: it is built empty and only added once a page opens.
        """
        self._paned = ttk.PanedWindow(parent, orient="horizontal")
        self._list = list_factory(self._paned)
        # Every pane carries no weight. A weighted pane is handed a share of the
        # room its content never asked for — which stretched the journal's heading
        # row past its columns — and a share of the squeeze when the window is
        # narrow, which cut them off. The split below is the only thing that sizes
        # a column, so a weight here would only fight it.
        self._paned.add(self._list, weight=0)
        self._dock = PageDock(self._paned, on_activate=on_activate, measure=measure)
        # A card opening or closing is a change of column count, so the dock
        # says so and the board re-decides the split straight away: the pane
        # appears (or goes) on the same event, not on the next window resize.
        self._dock.bind_changes(self._on_dock_changed)
        self._journal = journal_factory(self._paned)
        self._paned.add(self._journal, weight=0)
        # Whether the journal is on its rail, and whether the *user* has said so.
        # The board collapses it on its own while the room demands it, but never
        # against an explicit choice: a preference the user expressed outlives the
        # width that produced it.
        self._journal_rail = False
        self._journal_forced = False
        self._on_journal_toggle = on_journal_toggle
        self._room = 0
        self._after_id: str | None = None
        self._sashes: dict[str, int] = {}
        self._paned.bind("<Configure>", self._on_resize)

    # ------------------------------------------------------------- properties
    @property
    def pane(self) -> ttk.PanedWindow:
        """Return the paned window, for the app to pack."""
        return self._paned

    @property
    def list_card(self) -> tk.Widget:
        """Return the workspace-list column."""
        return self._list

    @property
    def dock(self) -> PageDock:
        """Return the page dock (the second space)."""
        return self._dock

    @property
    def journal_card(self) -> tk.Widget:
        """Return the journal column."""
        return self._journal

    @property
    def journal_rail(self) -> bool:
        """Whether the journal is collapsed to its rail."""
        return self._journal_rail

    @property
    def widths(self) -> dict[str, int]:
        """Return the last split applied, per column (for tests and the host)."""
        return dict(self._sashes)

    def subject(self) -> PageSubject | None:
        """Return the journal subject of the focused page, if there is one."""
        return self._dock.subject

    # ----------------------------------------------------------------- layout
    def reflow(self) -> None:
        """Re-decide the three widths from the room the window really has.

        Applied with ``sashpos``, which is the only lever a ``ttk.PanedWindow``
        offers: ``weight`` says who absorbs the leftover, and a ``weight=0`` pane
        is not given its request but frozen at the width its first layout gave
        it. A ``<Configure>`` therefore has to be answered by moving the sash
        ourselves, and the board is what answers it.
        """
        room = self._measured_room()
        if room <= 1:
            # Tk has not measured the board yet (a dialog, or the first layout):
            # the widths it gave are the ones to keep.
            return
        self._room = room
        plan = self._plan(room)
        if plan.journal_rail != self._journal_rail:
            self._journal_rail = plan.journal_rail
            self._notify_journal()
        self._apply(plan)

    def toggle_journal(self) -> None:
        """Collapse or expand the journal at the user's request.

        An explicit choice is remembered, so a later reflow at the width that
        prompted it will not undo it.
        """
        self._journal_rail = not self._journal_rail
        self._journal_forced = True
        self._notify_journal()
        self.reflow()

    def journal_pane_width(self) -> int:
        """Return the journal column's last width, or 0 when it is hidden."""
        return self._sashes.get("journal", 0)

    # ---------------------------------------------------------------- private
    def _on_resize(self, _event: object = None) -> None:
        """Schedule a reflow for the new room, coalescing a drag's burst."""
        if self._after_id is not None:
            return
        with contextlib.suppress(Exception):
            self._after_id = self._paned.after(REFLOW_DEBOUNCE_MS, self._reflow)

    def _reflow(self) -> None:
        """Run the debounced reflow."""
        self._after_id = None
        self.reflow()

    def _measured_room(self) -> int:
        """Return the board's own mapped width, or 0 when unmeasured."""
        with contextlib.suppress(Exception):
            return int(self._paned.winfo_width())
        return 0

    def _plan(self, room: int) -> Split:
        """Decide the three widths for *room* pixels.

        The journal is asked for :data:`JOURNAL_MINIMUM` and the dock for
        :data:`DOCK_MINIMUM`; whatever is left is the list's, which keeps at least
        :data:`LIST_MINIMUM`. When the three do not fit, the list is squeezed to
        its floor first and the journal then collapses to its rail — the dock only
        loses its minimum when the window is narrower than that, which the
        window's own floor is there to prevent: below
        ``LIST_MINIMUM + DOCK_MINIMUM + JOURNAL_RAIL + 2 * PANE_CHROME`` the three
        cannot be shown whole, and a column is collapsed, never squeezed past a
        minimum to make the arithmetic fit.
        """
        docked = not self._dock.empty
        docked_room = DOCK_MINIMUM if docked else 0
        rail = self._journal_rail and self._journal_forced
        # A forced preference is honoured as it stands; otherwise the rail is
        # only ever the *result* of a reflow, decided here and not in ``reflow``.
        journal = JOURNAL_RAIL if rail else JOURNAL_MINIMUM
        spare = room - 2 * PANE_CHROME - docked_room - journal
        if spare < LIST_MINIMUM and not rail and journal > JOURNAL_RAIL:
            rail = True
            journal = JOURNAL_RAIL
            spare = room - 2 * PANE_CHROME - docked_room - journal
        return Split(max(spare, LIST_MINIMUM), docked_room, journal, rail)

    def _apply(self, plan: Split) -> None:
        """Move the sashes to the widths :meth:`_plan` decided.

        A pane's width is the position of the sash *before* it, so the positions
        are cumulative from the left. The dock's pane is added and forgotten as
        pages open and close (Tk cannot re-parent it), and the index of every
        column is therefore resolved from ``panes()`` instead of being assumed.
        """
        with contextlib.suppress(Exception):
            self._sync_dock_pane(plan.dock > 0)
            index = self._index(self._list)
            if index is None:
                return
            self._paned.sashpos(index, plan.list)
            self._sashes["list"] = plan.list
            if plan.dock > 0:
                index = self._index(self._dock.pane)
                if index is not None:
                    self._paned.sashpos(index, plan.list + plan.dock + PANE_CHROME)
                    self._sashes["dock"] = plan.dock
            self._sashes["journal"] = plan.journal

    def _sync_dock_pane(self, wanted: bool) -> None:
        """Add or forget the dock's pane so an empty dock costs no width.

        ``insert`` rather than ``add`` keeps the column order fixed — list, dock,
        journal — whatever order the pages were opened in, so a sash index always
        means the same column. The position is resolved through :meth:`_index`
        because ``panes()`` hands back widget *paths* on a real paned window:
        comparing a widget to those strings with ``in`` is always false, which
        appended the dock *after* the journal and gave the journal the 1px
        remainder while the dock took the whole room.
        """
        index = self._index(self._dock.pane)
        if wanted and index is None:
            position = self._index(self._journal)
            self._paned.insert(
                len(self._paned.panes()) if position is None else position,
                self._dock.pane,
                weight=0,
            )
        elif not wanted and index is not None:
            self._paned.forget(self._dock.pane)

    def _index(self, pane: tk.Misc) -> int | None:
        """Return the position of *pane* in the paned window, looked up.

        A real ``ttk.PanedWindow`` answers ``panes()`` with widget *paths* while
        the fakes hand back the widgets themselves, so both are matched: by
        identity first, then by path.
        """
        for position, candidate in enumerate(self._paned.panes()):
            if candidate is pane or str(candidate) == str(pane):
                return position
        return None

    def _notify_journal(self) -> None:
        """Tell the host the journal changed shape, so it can show its rail."""
        if self._on_journal_toggle is not None:
            with contextlib.suppress(Exception):
                self._on_journal_toggle(self._journal_rail)

    def _on_dock_changed(self) -> None:
        """Re-decide the split after the dock gained or lost a card."""
        self._sync_dock_pane(not self._dock.empty)
        self.reflow()


__all__ = [
    "DOCK_MINIMUM",
    "JOURNAL_MINIMUM",
    "JOURNAL_RAIL",
    "LIST_MINIMUM",
    "PANE_CHROME",
    "REFLOW_DEBOUNCE_MS",
    "Board",
    "Card",
    "PageDock",
    "Split",
]
