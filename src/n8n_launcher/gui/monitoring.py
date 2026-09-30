"""Monitoring panel: an inline view over the persisted application events.

The panel is a *pure renderer* over the events already stored by
:mod:`n8n_launcher.monitoring`. It performs no I/O of its own: the host reads
the store (in a background worker, since the store is a SQLite file) and hands
the rows to :meth:`MonitoringPanel.apply`. That keeps the Tk main thread free
and makes the whole panel testable without a display.

Everything that decides *what* an event means lives outside this module, in
:mod:`n8n_launcher.monitoring.present` (the row values, the detail text, the
filters, the :class:`CriticalGate` anti-spam rule) and in
:mod:`n8n_launcher.workspaces.server_snapshot` (the server snapshot and its
text). They are pure logic and are shared with the headless CLI, which has to
render the same journal without a toolkit. What is left here is the *palette* —
this module's whole reason to exist — plus the two Tk widgets that render it.

Filtering is deliberately reduced to a single free-text field: the severity is
part of the searched text (see :func:`~n8n_launcher.monitoring.present.filter_events`),
so there is no level control to keep in sync with it. On top of that, one
*subject* filter follows the page the user is looking at
(:class:`~n8n_launcher.core.subjects.PageSubject`): it is shown as a chip so a
narrowed table never reads as lost rows, and the user can drop it with one click.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
import weakref
from collections.abc import Callable, Sequence
from tkinter import ttk
from typing import ClassVar

from ..core.models import Workspace
from ..core.subjects import PageSubject
from ..gui_utils.text import ellipsize
from ..monitoring.events import Event
from ..monitoring.present import (
    CriticalGate,
    event_detail,
    event_row,
    filter_events,
    level_tag,
    summary_text,
    workspace_events,
)
from ..monitoring.store import EventStore
from ..workspaces.server_snapshot import ServerSnapshot, server_snapshot_text
from .layout import (
    ColumnFitter,
    bind_ellipsize,
    bind_wraplength,
    wrap_at,
)
from .theme import (
    ACCENT,
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    ROW_DELETE_BG,
    SURFACE,
    SURFACE_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    text_measure,
)
from .tokens import (
    SPACE_2XL,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_TIGHT,
    SPACE_XL,
)

# Row colouring by severity. ERROR/CRITICAL reuse the destructive row colour
# already used for workspace deletion rows so the palette stays coherent.
# The keys are the *semantic* tags :func:`present.level_tag` returns, not the
# raw level names: deciding "how bad is this" is headless, deciding "what colour
# is that" is this module's job and its only reason to exist.
_TAG_COLORS = {
    "info": SURFACE,
    "warning": "#3a2f0f",
    "error": ROW_DELETE_BG,
    "critical": "#450a0a",
}

# Journal table: columns, headings and per-column bounds handed to the fitter.
# Nothing here is a width: the table is sized to whatever the rows hold (see
# ``gui.layout``), and these bounds only say how small a column may get before
# it stops being readable, and how large before it stops being a column. The
# maximums are what keep the *sum* — the width a hosting window is asked to open
# at — inside a display, so a long message is bounded rather than unbounded.
_JOURNAL_COLUMNS = ("time", "level", "name", "message")
_JOURNAL_HEADINGS = {
    "time": "Heure",
    "level": "Niveau",
    "name": "Source",
    "message": "Message",
}
_JOURNAL_MINIMUMS = {"time": 80, "level": 60, "name": 90, "message": 200}
_JOURNAL_MAXIMUMS = {"time": 120, "level": 90, "name": 220, "message": 760}

# The column that may absorb the room a wide pane leaves over: the message is
# the prose of a log line, so it is the only one that reads better wide. The
# others are a timestamp, a level and a logger name, and stretching them would
# invent space nothing uses.
_JOURNAL_FLEXIBLE = "message"

# The margin every child of the panel is packed with. One constant because the
# caption, the detail pane and the table must share it: their requests are what
# the card hands out, and they only add up when the margins match.
_PANEL_PADX = SPACE_XL

# The supervision window is a *reader* (logs, deploy history, executions), so it
# follows the screen the same way the main window does instead of asking for a
# fixed 1000x680 that overflows a small laptop.


# Geometry of the download glyph, in the 24x20 canvas of ``_download_icon``:
# a downward arrow (stem + head) dropping into an open tray. Drawing the lines
# instead of using a Unicode arrow keeps the icon identical on every platform —
# the UI font is not guaranteed to carry a given glyph (the same reason the
# checkbox markers are ASCII).
_ICON_SIZE = (24, 20)
_ICON_STROKES: tuple[tuple[int, int, int, int], ...] = (
    (12, 3, 12, 11),  # stem
    (7, 8, 12, 13),  # head, left
    (12, 13, 17, 8),  # head, right
    (4, 19, 20, 19),  # tray, base
    (4, 15, 4, 19),  # tray, left wall
    (20, 15, 20, 19),  # tray, right wall
)


def _copy_text(widget: tk.Misc, text: str) -> None:
    """Copy *text* to the clipboard of *widget* (best-effort for test fakes).

    Same contract as the CI credentials dialog's own copy helper: a clipboard
    that is unavailable (a stripped interpreter, a headless test) must not turn
    a keystroke into an error.
    """
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except Exception:
        pass


def _download_icon(
    parent: tk.Misc,
    *,
    command: Callable[[], None],
    tooltip: Callable[[tk.Misc, str], None] | None = None,
    label: str = "Exporter le journal (JSON)",
) -> tk.Canvas:
    """Build the discreet download button used instead of a labelled button.

    The icon only makes sense with a hint, so the hover also raises *label*
    through the host-provided *tooltip* helper (the panel itself never owns
    windows). ``takefocus`` keeps the action reachable from the keyboard.
    """
    canvas = tk.Canvas(
        parent,
        width=_ICON_SIZE[0],
        height=_ICON_SIZE[1],
        bg=APP_BACKGROUND,
        highlightthickness=0,
        cursor="hand2",
        takefocus=True,
    )
    strokes = [
        canvas.create_line(*points, fill=TEXT_MUTED, width=2, capstyle="round")
        for points in _ICON_STROKES
    ]

    def repaint(color: str) -> None:
        """Recolour every stroke of the glyph."""
        for stroke in strokes:
            canvas.itemconfigure(stroke, fill=color)

    canvas.bind("<Button-1>", lambda _event: command())
    canvas.bind("<Return>", lambda _event: command())
    canvas.bind("<Key-space>", lambda _event: command())
    canvas.bind("<Enter>", lambda _event: repaint(TEXT_PRIMARY))
    canvas.bind("<Leave>", lambda _event: repaint(TEXT_MUTED))
    if tooltip is not None:
        tooltip(canvas, label)
    return canvas


class MonitoringPanel(tk.Frame):
    """Read-only event table with a detail pane and a single search field.

    The header carries only the download icon, and the one filter is a
    free-text query whose placeholder doubles as the field's label: a level,
    a source, a message or a piece of traceback all narrow the table the same
    way. ``Ctrl+C`` copies the selected event whole — the detail pane's own text,
    so what is pasted into a bug report is exactly what is on screen.

    A *second* narrowing, the page subject, is driven by the host rather than by
    the user: opening a page filters the table on what that page is about. It
    gets its own chip in the header — a table that silently dropped rows would
    read as a journal that stopped recording — and the chip's cross brings the
    whole log back for as long as that page stays selected.
    """

    instances: ClassVar[weakref.WeakSet[MonitoringPanel]] = weakref.WeakSet()

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_export: Callable[[], None] | None = None,
        on_fitted: Callable[[], None] | None = None,
        on_expand: Callable[[], None] | None = None,
        tooltip: Callable[[tk.Misc, str], None] | None = None,
    ) -> None:
        """Build the integrated journal view and its optional host actions.

        ``on_fitted`` is called after every fit, so a host that owns the window
        can answer a table the pane cannot hold. ``on_expand`` is the rail's
        single control (see :meth:`set_rail`): a 46-pixel column cannot show a
        table, so the panel is either a journal or one button to get it back.
        The panel itself never resizes anything: it renders and calls back.
        """
        super().__init__(parent, bg=APP_BACKGROUND)
        self._events: list[Event] = []
        self._visible: dict[str, Event] = {}
        self._order: list[str] = []
        self._store: EventStore | None = None
        self._workspace: Workspace | None = None
        self._subject: PageSubject | None = None
        # The subject the user cleared, kept apart from ``_subject`` so a
        # journal poll re-applying the same subject does not undo their click.
        self._dismissed: PageSubject | None = None
        self._export_icon: tk.Canvas | None = None
        self._subject_chip: tk.Frame | None = None
        self._subject_text: tk.Label | None = None
        self._on_fitted = on_fitted
        self._on_expand = on_expand
        self._tooltip = tooltip
        # Whether the panel is currently a rail, and the strip that says so.
        self._rail = False
        self._rail_frame: tk.Frame | None = None
        MonitoringPanel.instances.add(self)

        header = tk.Frame(self, bg=APP_BACKGROUND)
        self._header = header
        header.pack(fill="x", padx=SPACE_XL, pady=(SPACE_LG, SPACE_TIGHT))
        title = tk.Label(
            header,
            text="Journal",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_ROWS,
            anchor="w",
        )
        title.pack(side="left")
        self._build_subject_chip(header)
        # Current scope, shown only when narrowed to a workspace: the global
        # scope needs no wording, it is the one you get back to by clicking the
        # already selected row in the workspace list.
        self._scope = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="e",
        )
        # The scope is a workspace name, so its width is whatever the user typed.
        # An unbounded label here would out-request the header, the panel and the
        # card behind it, and the launcher's window would grow to fit a name — so
        # the text is re-cut to the label's own width and the full name stays in
        # the row tooltip and the caption below.
        self._scope_text = ""
        bind_ellipsize(self._scope, lambda: self._scope_text, text_measure(self, FONT_META))
        self._scope.pack(side="left", fill="x", expand=True, padx=(SPACE_LG, SPACE_SM))
        if on_export is not None:
            self._export_icon = _download_icon(header, command=on_export, tooltip=tooltip)
            self._export_icon.pack(side="right")

        self._entry = tk.Entry(
            self,
            bg=SURFACE,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            selectbackground=ACCENT,
            selectforeground="#ffffff",
            relief="flat",
            font=FONT_META,
        )
        self._entry.pack(fill="x", padx=_PANEL_PADX, pady=(0, SPACE_TIGHT))
        self._entry.bind("<Return>", lambda _event: self._render())
        self._entry.bind("<KeyRelease>", lambda _event: self._render())
        # Tk has no native placeholder, so the hint is a label floating over the
        # empty field; it is removed as soon as the field holds text. Placed
        # relatively to the entry height so it stays centred whatever the font.
        self._placeholder = tk.Label(
            self,
            text="Rechercher…",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._placeholder.bind("<Button-1>", self._focus_search)
        self._placeholder.place(in_=self._entry, x=8, rely=0.5, relheight=1.0, anchor="w")

        self._summary_text = ""
        self._summary = tk.Label(
            self,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._summary.pack(fill="x", padx=_PANEL_PADX, pady=(0, SPACE_TIGHT))

        self.tree = ttk.Treeview(
            self,
            columns=_JOURNAL_COLUMNS,
            show="headings",
            height=14,
        )
        for name, heading in _JOURNAL_HEADINGS.items():
            self.tree.heading(name, text=heading)
        for name in _JOURNAL_COLUMNS:
            # The requested width starts at the column's minimum: the fitter
            # takes over as soon as the first snapshot is rendered, and a table
            # that asks for less up front leaves more room to the workspace list.
            self.tree.column(
                name,
                width=_JOURNAL_MINIMUMS[name],
                minwidth=_JOURNAL_MINIMUMS[name],
                stretch=False,
                anchor="w",
            )
        self._fitter = ColumnFitter(
            self.tree,
            columns=_JOURNAL_COLUMNS,
            headings=_JOURNAL_HEADINGS,
            minimums=_JOURNAL_MINIMUMS,
            maximums=_JOURNAL_MAXIMUMS,
            measure=text_measure(self, FONT_META),
            container=self,
            available=self._table_room,
            flexible=_JOURNAL_FLEXIBLE,
        )
        for tag, color in _TAG_COLORS.items():
            self.tree.tag_configure(tag, background=color)
        # No horizontal fill (the table is packed at the end of this method): the
        # table is exactly the sum of its columns, so the header row sits over
        # its cells, and ``container=self`` above asks the pane for that width
        # instead of letting it squeeze the columns.
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # Bound on the table only: Tk delivers a key to the focused widget, its
        # class and the toplevel, so a binding on the panel would never see it —
        # and one bound more widely would take the search field's own Ctrl+C away
        # from the user.
        self.tree.bind("<Control-c>", self._copy_selected)

        self._detail = tk.Label(
            self,
            text=event_detail(None),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            height=6,
            anchor="nw",
            justify="left",
            wraplength=480,
        )
        # A traceback is the longest text in the panel: it re-wraps at the pane's
        # real width instead of being cut at a hard-coded 520 pixels.
        bind_wraplength(self._detail, minimum=200, padding=2 * _PANEL_PADX)
        self._detail.pack(side="bottom", fill="both", padx=_PANEL_PADX, pady=(0, SPACE_LG))

        # The table is packed last, and that is deliberate: a container shorter
        # than the sum of its children takes the shortfall from the last ones
        # packed, and the table is the elastic part — it shows fewer rows in a
        # short pane, where a six-line detail pane would simply disappear.
        self.tree.pack(fill="y", anchor="nw", expand=True, padx=_PANEL_PADX, pady=(0, SPACE_SM))

    def set_rail(self, rail: bool) -> None:
        """Show the panel as a rail strip, or as the full journal.

        A 46-pixel column cannot show a table: the header, the search field, the
        summary and the detail pane would all be cut, and a table cut at its
        second column is worse than no table. So the rail is *not* the journal
        squeezed — it is one control that brings the journal back, and who decided
        on it (the room, or the user) is the board's business, not this panel's.

        Nothing is destroyed: the widgets are forgotten and shown again, so a
        collapse and an expand keep the events, the filter and the selection.
        """
        if rail == self._rail:
            return
        self._rail = rail
        if rail:
            self._hide_for_rail()
        else:
            self._show_after_rail()

    @property
    def rail(self) -> bool:
        """Whether the panel is currently collapsed to its rail."""
        return self._rail

    def _hide_for_rail(self) -> None:
        """Forget the journal's own widgets and show the strip that reopens it."""
        for widget in (self._header, self._entry, self._summary, self.tree, self._detail):
            with contextlib.suppress(Exception):
                widget.pack_forget()
        with contextlib.suppress(Exception):
            self._placeholder.place_forget()
        if self._subject_chip is not None:
            with contextlib.suppress(Exception):
                self._subject_chip.pack_forget()
        frame = tk.Frame(self, bg=APP_BACKGROUND)
        # The chevron is ASCII (» is not present in every UI font, and the
        # project's lint rejects the guillemet's ambiguous lookalikes anyway):
        # a ">" is the language's own "more to the right".
        button = tk.Label(
            frame,
            text=">",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            cursor="hand2",
        )
        button.pack(pady=(SPACE_LG, SPACE_TIGHT))
        caption = tk.Label(
            frame,
            text="Journal",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            # Three characters per line turns the word into a vertical one, which
            # is the only way to label a 46-pixel column in Tk.
            width=3,
            height=3,
            justify="center",
        )
        caption.pack()
        frame.pack(fill="y")
        self._rail_frame = frame

        def reopen(_event: object = None) -> None:
            if self._on_expand is not None:
                self._on_expand()

        for widget in (frame, button, caption):
            with contextlib.suppress(Exception):
                widget.bind("<Button-1>", reopen)
        if self._tooltip is not None:
            with contextlib.suppress(Exception):
                self._tooltip(button, "Afficher le journal")

    def _show_after_rail(self) -> None:
        """Put the journal's widgets back exactly where they were packed.

        The packing order is the layout rule, so it is written out again here
        rather than remembered: the detail pane is packed before the table (the
        elastic part goes last) and the table last of all.
        """
        if self._rail_frame is not None:
            with contextlib.suppress(Exception):
                self._rail_frame.destroy()
            self._rail_frame = None
        with contextlib.suppress(Exception):
            self._header.pack(fill="x", padx=SPACE_XL, pady=(SPACE_LG, SPACE_TIGHT))
        if self._subject_chip is not None and self._subject is not None:
            with contextlib.suppress(Exception):
                self._subject_chip.pack(side="left", padx=(SPACE_MD, 0))
        with contextlib.suppress(Exception):
            self._entry.pack(fill="x", padx=_PANEL_PADX, pady=(0, SPACE_TIGHT))
        with contextlib.suppress(Exception):
            self._summary.pack(fill="x", padx=_PANEL_PADX, pady=(0, SPACE_TIGHT))
        with contextlib.suppress(Exception):
            self._detail.pack(side="bottom", fill="both", padx=_PANEL_PADX, pady=(0, SPACE_LG))
        with contextlib.suppress(Exception):
            self.tree.pack(fill="y", anchor="nw", expand=True, padx=_PANEL_PADX, pady=(0, SPACE_SM))
        if not self._entry.get():
            with contextlib.suppress(Exception):
                self._placeholder.place(in_=self._entry, x=8, rely=0.5, relheight=1.0, anchor="w")

    def layout_key(self, event: Event) -> str:
        """Return the stable row id for *event* (``id`` when stored)."""
        return f"event-{event.id}" if event.id is not None else f"event-unsaved-{event.name}"

    def _build_subject_chip(self, header: tk.Misc) -> None:
        """Build the "which page am I filtering on" chip, hidden by default.

        The cross is a plain ASCII ``x`` and a ``tk.Label`` rather than a
        button: the project's lint rejects ambiguous glyphs (RUF001/002) and a
        label needs no themed style to stay legible on the chip's own background.
        """
        chip = tk.Frame(header, bg=SURFACE_HOVER)
        text = tk.Label(
            chip,
            text="",
            bg=SURFACE_HOVER,
            fg=TEXT_PRIMARY,
            font=FONT_META,
            anchor="w",
            padx=SPACE_SM,
        )
        text.pack(side="left")
        clear = tk.Label(
            chip, text="x", bg=SURFACE_HOVER, fg=TEXT_MUTED, font=FONT_META, padx=SPACE_SM
        )
        clear.pack(side="left")
        clear.bind("<Button-1>", lambda _event: self.dismiss_subject())
        with contextlib.suppress(Exception):
            clear.config(cursor="hand2")
        self._subject_chip = chip
        self._subject_text = text

    def set_subject(self, subject: PageSubject | None) -> None:
        """Narrow the table to the page of *subject*, showing the chip.

        Re-applying the same subject is a no-op, so the journal's periodic poll
        cannot undo the user's click on the chip's cross. Switching to another
        subject re-arms it: the cross dismisses the page in view, not the feature.
        """
        if self._update_subject(subject):
            self._render()

    def dismiss_subject(self) -> None:
        """Show the whole log again, dropping the current page's subject."""
        if self._subject is None or self._dismissed == self._subject:
            return
        self._dismissed = self._subject
        self._render()

    def visible_subject(self) -> PageSubject | None:
        """Return the subject narrowing the table, or ``None`` when not narrowed."""
        if self._subject is None or self._dismissed == self._subject:
            return None
        return self._subject

    def _update_subject(self, subject: PageSubject | None) -> bool:
        """Record *subject*, re-arming the chip; True when it changed."""
        if subject == self._subject:
            return False
        self._subject = subject
        self._dismissed = None
        return True

    def _update_chip(self, subject: PageSubject | None) -> None:
        """Show the chip exactly while the table is narrowed by a page."""
        chip, text = self._subject_chip, self._subject_text
        if chip is None or text is None:
            return
        if subject is None:
            chip.pack_forget()
            return
        text.config(text=f"sujet : {subject.label}")
        chip.pack(side="left", padx=(SPACE_LG, 0))

    def apply(
        self,
        events: Sequence[Event],
        *,
        store: EventStore | None = None,
        workspace: Workspace | None = None,
        query: str | None = None,
        subject: PageSubject | None = None,
    ) -> None:
        """Cache and render events for the current workspace scope and page."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._events = list(events)
        self._store = store
        self._workspace = workspace
        self._update_subject(subject)
        self._render(query=query)

    def selected_event(self) -> Event | None:
        """Return the event backing the selected row, if any."""
        selection = self.tree.selection()
        return self._visible.get(selection[0]) if selection else None

    def _focus_search(self, _event: object = None) -> None:
        """Put the caret in the search field when its placeholder is clicked."""
        self._entry.focus_set()

    def _update_placeholder(self) -> None:
        """Show the "Rechercher…" hint only while the search field is empty."""
        # A torn-down interpreter must never break a keystroke handler.
        with contextlib.suppress(Exception):
            if self._entry.get():
                self._placeholder.place_forget()
            else:
                self._placeholder.place(in_=self._entry, x=8, rely=0.5, relheight=1.0, anchor="w")

    def _render(self, *, query: str | None = None) -> None:
        """Render the cached events using the current search query."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._update_placeholder()
        subject = self.visible_subject()
        visible = filter_events(
            self._events,
            query=self._entry.get() if query is None else query,
            tokens=() if subject is None else subject.tokens,
        )
        if self._workspace is not None:
            visible = workspace_events(self._workspace, visible)
        selected = set(self.tree.selection())
        self._visible.clear()
        self._order.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for event in visible:
            row_id = self.layout_key(event)
            if row_id in self._visible:
                continue
            self._visible[row_id] = event
            self._order.append(row_id)
            self.tree.insert(
                "",
                "end",
                iid=row_id,
                values=event_row(event),
                tags=(level_tag(event),),
            )
        scope = self._workspace.name if self._workspace is not None else "Tous les workspaces"
        # The raw name is what the ellipsize binding re-cuts; setting the label
        # directly is what the fakes and the tests read back.
        self._scope_text = "" if self._workspace is None else scope
        self._scope.config(text=self._scope_text)
        self._summary_text = summary_text(
            visible,
            store=self._store,
            scope=scope,
            subject="" if subject is None else subject.label,
        )
        self._summary.config(text=self._summary_text)
        self._update_chip(subject)
        restored = next((row_id for row_id in self._order if row_id in selected), None)
        if restored is not None:
            self.tree.selection_set(restored)
        self._show_detail(self._visible.get(restored) if restored is not None else None)
        # The rows are in: size every column to them before the user looks, so a
        # filtered table shows as much of each line as its pane can hold.
        self._fitter.rows()
        self._bound_to_table()
        if self._on_fitted is not None:
            self._on_fitted()

    def _bound_to_table(self) -> None:
        """Keep the panel's caption and detail pane within the table's width.

        A label's requested width is its text width and a container's request is
        the largest of its children's, so a caption wider than the table would
        widen the card that holds it: the columns would stay exact but the table
        would stop meeting the card's outline, the slack landing on the right
        (the table is packed ``anchor="nw"``). The caption is therefore cut with
        an ellipsis and the detail pane re-wraps at the table's own width, which
        leaves the table the widest child and the card exactly as wide as it.
        """
        budget = self._fitter.total()
        self._summary.config(
            text=ellipsize(self._summary_text, budget, text_measure(self, FONT_META))
        )
        wrap_at(self._detail, budget, minimum=200, padding=2 * _PANEL_PADX)

    def _table_room(self) -> int:
        """Return the pixels the table actually has, margins deducted.

        The panel is the pane the board hands us, so its mapped width *is* the
        room the table may use — the dashboard sizes the pane, the table fits the
        pane, and neither has to ask the window for more. A panel that has not
        been mapped yet reports 1, which the fitter reads as "not measurable" and
        leaves the table on its pure content sizing.
        """
        with contextlib.suppress(Exception):
            return max(int(self.winfo_width()) - 2 * _PANEL_PADX, 1)
        return 1

    def table_widths(self) -> dict[str, int]:
        """Return the column widths the fitter last applied (for the host)."""
        return self._fitter.widths()

    def minimum_table_width(self) -> int:
        """Return the narrowest the table can be, for the board's breakpoints."""
        return self._fitter.minimum_total()

    def _on_select(self, _event: object = None) -> None:
        """Refresh the detail pane when the selection changes."""
        self._show_detail(self.selected_event())

    def _copy_selected(self, _event: object = None) -> str:
        """Copy the selected event, whole, to the clipboard (``Ctrl+C``).

        The copied text is exactly what the detail pane shows — level, source,
        timestamp, message, context and traceback — so one keystroke yields the
        one event a bug report needs, and nothing else. Nothing selected (or a
        row the search has filtered out) leaves the clipboard alone. Returns
        ``"break"`` so the keystroke does not travel any further.
        """
        event = self.selected_event()
        if event is not None:
            _copy_text(self, event_detail(event))
        return "break"

    def _show_detail(self, event: Event | None) -> None:
        """Render *event* (or the hint line) in the detail pane."""
        self._detail.config(text=event_detail(event))


class ServerPanel(tk.Frame):
    """Render a :class:`ServerSnapshot` in a single scrollable-free text block."""

    def __init__(self, parent: tk.Misc) -> None:
        """Build the snapshot view."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self._label = tk.Label(
            self,
            text=server_snapshot_text(ServerSnapshot()),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            anchor="nw",
            justify="left",
            wraplength=640,
        )
        # Container logs and deploy history are long lines: let them use the
        # window's real width instead of a fixed 940 pixels.
        bind_wraplength(self._label, minimum=200, padding=28)
        self._label.pack(fill="both", expand=True, padx=SPACE_2XL, pady=(SPACE_TIGHT, SPACE_XL))

    def apply(self, snapshot: ServerSnapshot) -> None:
        """Render *snapshot*, ignoring a read that landed after ``destroy``."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._last = snapshot
        self._label.config(
            text=server_snapshot_text(snapshot),
            fg=TEXT_PRIMARY if snapshot.healthy else TEXT_MUTED,
        )

    def text(self) -> str:
        """Return the text currently rendered (used by tests and the host)."""
        return str(self._label.cget("text"))


__all__ = [
    "CriticalGate",
    "MonitoringPanel",
    "ServerPanel",
    "ServerSnapshot",
    "event_detail",
    "event_row",
    "filter_events",
    "level_tag",
    "server_snapshot_text",
    "summary_text",
    "workspace_events",
]
