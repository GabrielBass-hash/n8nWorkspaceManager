"""Monitoring panel: an inline view over the persisted application events.

The panel is a *pure renderer* over the events already stored by
:mod:`n8n_launcher.monitoring`. It performs no I/O of its own: the host reads
the store (in a background worker, since the store is a SQLite file) and hands
the rows to :meth:`MonitoringPanel.apply`. That keeps the Tk main thread free
and makes the whole panel testable without a display.

Two behaviours deserve their own home here because they are pure logic:

* :func:`level_tag` / :func:`event_row` decide how an event is drawn, and
  :func:`event_detail` renders the context of the selected row.
* :class:`CriticalGate` implements the "surface critical incidents" rule: an
  ``ERROR``/``CRITICAL`` event updates the status bar, while repeating failures
  of the same operation are grouped so a retry loop cannot flood the user.

Filtering is deliberately reduced to a single free-text field: the severity is
part of the searched text (see :func:`filter_events`), so there is no level
control to keep in sync with it.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import tkinter as tk
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk
from typing import ClassVar

from ..core.models import Workspace
from ..monitoring.events import Event
from ..monitoring.store import RETENTION_DAYS, EventStore
from ..remote import RemoteExecutionStatus, RemoteHealth
from .layout import ColumnFitter, bind_wraplength, screen_fraction_size, screen_size
from .theme import (
    ACCENT,
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    ROW_DELETE_BG,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    text_measure,
)

# Row colouring by severity. ERROR/CRITICAL reuse the destructive row colour
# already used for workspace deletion rows so the palette stays coherent.
_TAG_INFO = SURFACE
_TAG_WARNING = "#3a2f0f"
_TAG_ERROR = ROW_DELETE_BG
_TAG_CRITICAL = "#450a0a"

CRITICAL_LEVELS = frozenset({"ERROR", "CRITICAL"})

# One incident status must not be raised more than once per this many seconds
# for the same failure signature; the very first occurrence always surfaces.
DEFAULT_GROUP_WINDOW_SECONDS = 60.0

_LEVEL_TAGS = {
    "DEBUG": _TAG_INFO,
    "INFO": _TAG_INFO,
    "WARNING": _TAG_WARNING,
    "WARN": _TAG_WARNING,
    "ERROR": _TAG_ERROR,
    "CRITICAL": _TAG_CRITICAL,
}

# Journal table: columns, headings and per-column bounds handed to the fitter.
# Nothing here is a width: the table is sized to whatever the rows hold (see
# ``gui.layout``), and these bounds only say how small a column may get before
# it stops being readable, and how large before it stops being a column.
_JOURNAL_COLUMNS = ("time", "level", "name", "message")
_JOURNAL_HEADINGS = {
    "time": "Heure",
    "level": "Niveau",
    "name": "Source",
    "message": "Message",
}
_JOURNAL_MINIMUMS = {"time": 80, "level": 60, "name": 90, "message": 200}
_JOURNAL_MAXIMUMS = {"time": 140, "level": 96, "name": 280, "message": 4000}

# The supervision window is a *reader* (logs, deploy history, executions), so it
# follows the screen the same way the main window does instead of asking for a
# fixed 1000x680 that overflows a small laptop.
_SERVER_WINDOW_FRACTION = (0.8, 0.8)
_SERVER_WINDOW_MINIMUM = (820, 520)
_SERVER_WINDOW_MAXIMUM = (1400, 900)
_SERVER_WINDOW_DEFAULT = (1000, 680)


def level_tag(event: Event) -> str:
    """Return the tree tag encoding *event*'s severity."""
    return _LEVEL_TAGS.get(event.level.upper(), _TAG_INFO)


def is_critical(event: Event) -> bool:
    """Return whether *event* counts as a critical incident."""
    return event.level.upper() in CRITICAL_LEVELS


def _format_time(value: datetime) -> str:
    """Render an event timestamp as local wall-clock time."""
    return value.astimezone().strftime("%d/%m %H:%M:%S")


def event_row(event: Event) -> tuple[str, str, str, str]:
    """Return the tree columns for *event* (time, level, source, message)."""
    return (_format_time(event.timestamp), event.level, event.name, event.message)


def event_detail(event: Event | None) -> str:
    """Render the context and traceback of the selected *event*.

    ``None`` (nothing selected) yields the hint line, so the pane is never
    blank and the user knows the row is clickable. The line is also what
    ``Ctrl+C`` puts on the clipboard: one event, whole, as a bug report needs it.
    """
    if event is None:
        return "Sélectionnez une ligne pour voir le détail (contexte, exception). Ctrl+C la copie."
    lines = [f"{event.level} · {event.name} · {_format_time(event.timestamp)}", event.message]
    if event.context:
        lines.append("")
        lines.append(json.dumps(event.context, ensure_ascii=False, indent=2, sort_keys=True))
    if event.exception:
        lines.append("")
        lines.append(event.exception.rstrip())
    return "\n".join(lines)


def summary_text(
    events: Sequence[Event],
    *,
    store: EventStore | None,
    scope: str = "Tous les workspaces",
) -> str:
    """Return the line describing *scope*, the visible rows and the retention.

    ``store`` is only used to know whether a journal exists at all: the panel is
    embedded in a narrow pane, so the line stays short and the full path lives in
    the "Exporter" target instead.
    """
    parts = [scope, f"{len(events)} événement(s)" if events else "aucun événement"]
    criticals = sum(1 for event in events if event.level.upper() == "CRITICAL")
    errors = sum(1 for event in events if event.level.upper() == "ERROR")
    warnings = sum(1 for event in events if event.level.upper() in ("WARNING", "WARN"))
    if criticals:
        parts.append(f"{criticals} critique(s)")
    if errors:
        parts.append(f"{errors} erreur(s)")
    if warnings:
        parts.append(f"{warnings} avertissement(s)")
    parts.append(
        f"conservation {RETENTION_DAYS} j" if store is not None else "journal indisponible"
    )
    return " · ".join(parts)


def filter_events(events: Sequence[Event], *, query: str | None = None) -> list[Event]:
    """Return the events matching a case-insensitive *query*.

    The text filter runs here rather than in SQL so the panel and the
    in-memory snapshot can never disagree about what a query means. Severity
    needs no dedicated control because :func:`_haystack` includes the level:
    typing ``ERROR`` (or ``WARNING``…) narrows the table to that severity.
    """
    needle = query.strip().casefold() if query else ""
    if not needle:
        return list(events)
    return [event for event in events if needle in _haystack(event)]


def _haystack(event: Event) -> str:
    """Return the case-folded text a free-text query is matched against."""
    parts = [event.name, event.message, event.level]
    if event.context:
        parts.append(json.dumps(event.context, ensure_ascii=False, sort_keys=True))
    if event.exception:
        parts.append(event.exception)
    return " ".join(parts).casefold()


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


class CriticalGate:
    """Decide when a critical event must surface, grouping repeats.

    The first ``ERROR``/``CRITICAL`` event always surfaces. The same signature
    (``name`` + ``level``) then stays quiet for
    :attr:`window_seconds`, which keeps one failing operation — retried by a
    timer — from replacing the status message per attempt while a *different*
    failure still gets through immediately.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_GROUP_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a gate with a grouping window and an injectable clock."""
        self.window_seconds = window_seconds
        self._clock = clock
        self._last_surfaced: dict[str, float] = {}

    def accept(self, event: Event) -> bool:
        """Return whether *event* should surface in the status bar now."""
        if not is_critical(event):
            return False
        signature = f"{event.level.upper()}|{event.name}"
        now = self._clock()
        previous = self._last_surfaced.get(signature)
        if previous is not None and now - previous < self.window_seconds:
            return False
        self._last_surfaced[signature] = now
        return True

    def forget(self, signature: str) -> None:
        """Drop the grouping state of *signature*."""
        self._last_surfaced.pop(signature, None)


class MonitoringPanel(tk.Frame):
    """Read-only event table with a detail pane and a single search field.

    The header carries only the download icon, and the one filter is a
    free-text query whose placeholder doubles as the field's label: a level,
    a source, a message or a piece of traceback all narrow the table the same
    way. ``Ctrl+C`` copies the selected event whole — the detail pane's own text,
    so what is pasted into a bug report is exactly what is on screen.
    """

    instances: ClassVar[weakref.WeakSet[MonitoringPanel]] = weakref.WeakSet()

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_export: Callable[[], None] | None = None,
        tooltip: Callable[[tk.Misc, str], None] | None = None,
    ) -> None:
        """Build the integrated journal view and its optional host actions."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self._events: list[Event] = []
        self._visible: dict[str, Event] = {}
        self._order: list[str] = []
        self._store: EventStore | None = None
        self._workspace: Workspace | None = None
        self._export_icon: tk.Canvas | None = None
        MonitoringPanel.instances.add(self)

        header = tk.Frame(self, bg=APP_BACKGROUND)
        header.pack(fill="x", padx=12, pady=(10, 4))
        title = tk.Label(
            header,
            text="Journal",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_ROWS,
            anchor="w",
        )
        title.pack(side="left")
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
        self._scope.pack(side="left", fill="x", expand=True, padx=(10, 6))
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
        self._entry.pack(fill="x", padx=12, pady=(0, 4))
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

        self._summary = tk.Label(
            self,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._summary.pack(fill="x", padx=12, pady=(0, 4))

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
                stretch=name == "message",
                anchor="w",
            )
        self._fitter = ColumnFitter(
            self.tree,
            columns=_JOURNAL_COLUMNS,
            headings=_JOURNAL_HEADINGS,
            minimums=_JOURNAL_MINIMUMS,
            maximums=_JOURNAL_MAXIMUMS,
            flexible="message",
            measure=text_measure(self, FONT_META),
        )
        for tag, color in (
            ("info", _TAG_INFO),
            ("warning", _TAG_WARNING),
            ("error", _TAG_ERROR),
            ("critical", _TAG_CRITICAL),
        ):
            self.tree.tag_configure(tag, background=color)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 6))
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
        bind_wraplength(self._detail, minimum=200, padding=24)
        self._detail.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def layout_key(self, event: Event) -> str:
        """Return the stable row id for *event* (``id`` when stored)."""
        return f"event-{event.id}" if event.id is not None else f"event-unsaved-{event.name}"

    def apply(
        self,
        events: Sequence[Event],
        *,
        store: EventStore | None = None,
        workspace: Workspace | None = None,
        query: str | None = None,
    ) -> None:
        """Cache and render events for the current workspace scope."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._events = list(events)
        self._store = store
        self._workspace = workspace
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
        visible = filter_events(self._events, query=self._entry.get() if query is None else query)
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
        self._scope.config(text="" if self._workspace is None else scope)
        self._summary.config(text=summary_text(visible, store=self._store, scope=scope))
        restored = next((row_id for row_id in self._order if row_id in selected), None)
        if restored is not None:
            self.tree.selection_set(restored)
        self._show_detail(self._visible.get(restored) if restored is not None else None)
        # The rows are in: size every column to them before the user looks, so a
        # filtered table shows as much of each line as its pane can hold.
        self._fitter.rows()

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


# ------------------------------------------------------ server supervision
@dataclass(frozen=True)
class ServerSnapshot:
    """One read-only view of a deployed workspace on the remote server.

    Every field is already redacted and bounded by the ``remote`` layer, so the
    panel only has to render it. ``error`` carries a whole failed read (SSH
    unreachable, deployment not confirmed yet) while the other fields keep
    whatever was collected before the failure.
    """

    health: RemoteHealth | None = None
    logs: str = ""
    marker: dict[str, object] | None = None
    history: tuple[dict[str, object], ...] = ()
    executions: RemoteExecutionStatus | None = None
    error: str | None = None

    @property
    def healthy(self) -> bool:
        """Return whether the remote stack reported itself healthy."""
        return self.health is not None and self.health.healthy


def _deploy_history_text(history: Sequence[dict[str, object]]) -> str:
    """Render the newest deploy entries, one compact line each."""
    if not history:
        return "Aucun déploiement enregistré sur le serveur."
    lines = []
    for entry in reversed(history):
        sha = str(entry.get("sha", ""))[:7]
        status = entry.get("status", "?")
        at = entry.get("at", "?")
        detail = entry.get("error")
        line = f"{at} · {status} · {sha or '—'}"
        if detail:
            line = f"{line} · {detail}"
        lines.append(line)
    return "\n".join(lines)


def _executions_text(status: RemoteExecutionStatus) -> str:
    """Render the remote executions, newest first.

    The remote script answers in n8n's own order and the contract only promises
    "up to *limit* entries", so the display sorts on ``startedAt`` itself rather
    than trusting the order of the wire payload.
    """
    if not status.supported:
        return "Exécutions n8n : non supporté par ce déploiement. " + (status.error or "")
    if not status.executions:
        return "Exécutions n8n : aucune exécution enregistrée."
    ordered = sorted(status.executions, key=lambda item: item.started_at or "", reverse=True)
    lines = []
    for item in ordered:
        # ``finished`` is derived from the status server-side; None means the
        # status was one the launcher does not know, so say so instead of lying.
        if item.finished is True:
            state = "terminé"
        elif item.finished is False:
            state = "en cours"
        else:
            state = "état inconnu"
        name = item.workflow_name or "—"
        lines.append(
            f"{item.started_at or '—'} · {item.status} · {name} · #{item.execution_id} · {state}"
        )
    return "Exécutions n8n :\n" + "\n".join(lines)


def server_snapshot_text(snapshot: ServerSnapshot) -> str:
    """Render the whole server snapshot as plain text (health, deploy, logs)."""
    parts: list[str] = []
    health = snapshot.health
    if health is None:
        parts.append("Santé : inconnue.")
    else:
        headline = (
            "Santé : serveur injoignable."
            if not health.available
            else f"Santé : {'ok' if health.healthy else 'dégradée'}."
        )
        parts.append(headline)
        if health.services:
            services = " · ".join(
                f"{name} {value}{'/' + health.health[name] if name in health.health else ''}"
                for name, value in health.services.items()
            )
            parts.append(f"Services : {services}")
        if health.error:
            parts.append(f"Diagnostic : {health.error}")
    parts.append("Dernier déploiement :\n" + _deploy_history_text(snapshot.history[-1:]))
    if len(snapshot.history) > 1:
        parts.append("Historique :\n" + _deploy_history_text(snapshot.history))
    if snapshot.executions is not None:
        parts.append(_executions_text(snapshot.executions))
    parts.append("Logs n8n (dernières lignes) :\n" + (snapshot.logs.strip() or "—"))
    if snapshot.error:
        parts.append(f"Lecture partielle : {snapshot.error}")
    return "\n\n".join(parts)


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
        self._label.pack(fill="both", expand=True, padx=14, pady=(4, 12))

    def apply(self, snapshot: ServerSnapshot) -> None:
        """Render *snapshot*, ignoring a read that landed after ``destroy``."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._label.config(
            text=server_snapshot_text(snapshot),
            fg=TEXT_PRIMARY if snapshot.healthy else TEXT_MUTED,
        )


def prompt_server_supervision(
    root: tk.Tk,
    workspace_name: str,
    host: str,
    read: Callable[[], ServerSnapshot | None],
    *,
    set_status: Callable[[str], None] | None = None,
) -> tk.Toplevel:
    """Open the read-only server supervision window for one workspace.

    ``read`` triggers the SSH queries. Returning a snapshot renders it right
    away (the standalone, synchronous case); returning ``None`` means the host
    fetches off the Tk thread and pushes the result to ``window.server_panel``
    itself. A raising ``read`` is rendered as a partial read, so an unreachable
    server never breaks the UI.
    """
    window = tk.Toplevel(root)
    window.title(f"Supervision du serveur — {workspace_name}")
    # Sized against the screen rather than a fixed box: the snapshot is mostly
    # text, so the reader wants every pixel the display can spare, while a small
    # laptop still gets a window that fits on it.
    width, height = screen_fraction_size(
        *screen_size(root),
        fraction=_SERVER_WINDOW_FRACTION,
        minimum=_SERVER_WINDOW_MINIMUM,
        maximum=_SERVER_WINDOW_MAXIMUM,
        default=_SERVER_WINDOW_DEFAULT,
    )
    window.geometry(f"{width}x{height}")
    # Never a floor larger than the box itself: a minsize above the geometry
    # would make Tk grow the window on the first map.
    window.minsize(min(_SERVER_WINDOW_MINIMUM[0], width), min(_SERVER_WINDOW_MINIMUM[1], height))
    window.transient(root)
    window.configure(bg=APP_BACKGROUND)

    header = tk.Frame(window, bg=APP_BACKGROUND)
    header.pack(fill="x", padx=14, pady=(10, 0))
    tk.Label(
        header,
        text=f"{workspace_name} · {host}",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
    ).pack(side="left", expand=True, fill="x")

    def refresh() -> None:
        """Re-read the server snapshot and note the outcome in the status bar."""
        try:
            snapshot = read()
        except Exception as exc:
            snapshot = ServerSnapshot(error=str(exc))
        if snapshot is None:
            # The host applies the snapshot itself once its worker returns.
            return
        panel.apply(snapshot)
        if set_status is not None:
            state = "sain" if snapshot.healthy else "à vérifier"
            set_status(f"Supervision « {workspace_name} » — {state}")

    ttk.Button(header, text="Actualiser", style="Secondary.TButton", command=refresh).pack(
        side="right"
    )

    panel = ServerPanel(window)
    panel.pack(fill="both", expand=True)
    # Exposed so the host can push a snapshot it fetched in a background worker
    # (SSH reads are far too slow to run on the Tk thread).
    window.server_panel = panel  # type: ignore[attr-defined]
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh()
    return window


# ------------------------------------------------------ workspace detail
def workspace_events(workspace: Workspace, events: Sequence[Event]) -> list[Event]:
    """Return the events that belong to *workspace*, preserving input order.

    Two signals are used because not every call site tags its records with a
    structured ``workspace_id``: the exact context field when present, and the
    workspace name in the message otherwise. A name that is a prefix of another
    one is disambiguated by requiring a word boundary.
    """
    identifier = workspace.id.casefold()
    name = workspace.name.casefold()
    pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
    selected: list[Event] = []
    for event in events:
        context_id = event.context.get("workspace_id")
        if isinstance(context_id, str) and context_id.casefold() == identifier:
            selected.append(event)
            continue
        if pattern.search(f"{event.name} {event.message}".casefold()):
            selected.append(event)
    return selected


__all__ = [
    "CRITICAL_LEVELS",
    "DEFAULT_GROUP_WINDOW_SECONDS",
    "CriticalGate",
    "MonitoringPanel",
    "ServerPanel",
    "ServerSnapshot",
    "event_detail",
    "event_row",
    "filter_events",
    "is_critical",
    "level_tag",
    "prompt_server_supervision",
    "server_snapshot_text",
    "summary_text",
    "workspace_events",
]
