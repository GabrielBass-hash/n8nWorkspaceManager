"""Monitoring console: one window over the persisted application events.

The console is a *pure renderer* over the events already stored by
:mod:`n8n_launcher.monitoring`. It performs no I/O of its own: the host reads
the store (in a background worker, since the store is a SQLite file) and hands
the rows to :meth:`MonitoringPanel.apply`. That keeps the Tk main thread free
and makes the whole panel testable without a display.

Two behaviours deserve their own home here because they are pure logic:

* :func:`level_tag` / :func:`event_row` decide how an event is drawn, and
  :func:`event_detail` renders the context of the selected row.
* :class:`CriticalGate` implements the "surface critical incidents" rule: an
  ``ERROR``/``CRITICAL`` event re-opens (or raises) the console, but repeating
  failures of the same operation are grouped so a retry loop cannot flood the
  user with a hundred identical windows.
"""

from __future__ import annotations

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
from .display import db_label, git_label
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    ROW_DELETE_BG,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

# Row colouring by severity. ERROR/CRITICAL reuse the destructive row colour
# already used for workspace deletion rows so the palette stays coherent.
_TAG_INFO = SURFACE
_TAG_WARNING = "#3a2f0f"
_TAG_ERROR = ROW_DELETE_BG
_TAG_CRITICAL = "#450a0a"

CRITICAL_LEVELS = frozenset({"ERROR", "CRITICAL"})

# One console window must not be raised more than once per this many seconds
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
    blank and the user knows the row is clickable.
    """
    if event is None:
        return "Sélectionnez une ligne pour voir le détail (contexte, exception)."
    lines = [f"{event.level} · {event.name} · {_format_time(event.timestamp)}", event.message]
    if event.context:
        lines.append("")
        lines.append(json.dumps(event.context, ensure_ascii=False, indent=2, sort_keys=True))
    if event.exception:
        lines.append("")
        lines.append(event.exception.rstrip())
    return "\n".join(lines)


def summary_text(events: Sequence[Event], store: EventStore | None) -> str:
    """Return the header describing the visible rows and the journal location."""
    location = str(store.path) if store is not None else "journal indisponible"
    if not events:
        return f"Aucun événement sur les {RETENTION_DAYS} derniers jours — {location}"
    errors = sum(1 for event in events if event.level.upper() == "ERROR")
    criticals = sum(1 for event in events if event.level.upper() == "CRITICAL")
    warnings = sum(1 for event in events if event.level.upper() in ("WARNING", "WARN"))
    parts = [f"{len(events)} événement(s)"]
    if criticals:
        parts.append(f"{criticals} critique(s)")
    if errors:
        parts.append(f"{errors} erreur(s)")
    if warnings:
        parts.append(f"{warnings} avertissement(s)")
    parts.append(f"conservation {RETENTION_DAYS} j — {location}")
    return " · ".join(parts)


def filter_events(
    events: Sequence[Event],
    *,
    level: str | None = None,
    query: str | None = None,
) -> list[Event]:
    """Return the events matching a level filter and a case-insensitive *query*.

    The text filter runs here rather than in SQL so the console and the
    in-memory snapshot can never disagree about what a query means.
    """
    selected = level.upper() if level else None
    needle = query.strip().casefold() if query else ""
    matches = []
    for event in events:
        if selected and event.level.upper() != selected:
            continue
        if needle and needle not in _haystack(event):
            continue
        matches.append(event)
    return matches


def _haystack(event: Event) -> str:
    """Return the case-folded text a free-text query is matched against."""
    parts = [event.name, event.message, event.level]
    if event.context:
        parts.append(json.dumps(event.context, ensure_ascii=False, sort_keys=True))
    if event.exception:
        parts.append(event.exception)
    return " ".join(parts).casefold()


def filter_options() -> tuple[tuple[str, str], ...]:
    """Return the (label, level) pairs offered by the level selector."""
    return (
        ("Tous", ""),
        ("Critique", "CRITICAL"),
        ("Erreur", "ERROR"),
        ("Avertissement", "WARNING"),
        ("Info", "INFO"),
    )


class CriticalGate:
    """Decide when a critical event must surface, grouping repeats.

    The first ``ERROR``/``CRITICAL`` event always surfaces. The same signature
    (``name`` + ``level``) then stays quiet for
    :attr:`window_seconds`, which keeps one failing operation — retried by a
    timer — from raising a window per attempt while a *different* failure still
    gets through immediately.
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
        """Return whether *event* should open/raise the console now."""
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
        """Drop the grouping state of *signature* (used when a window closes)."""
        self._last_surfaced.pop(signature, None)


class MonitoringPanel(tk.Frame):
    """Read-only event table with a detail pane, fed by :meth:`apply`.

    The panel holds no store and starts no thread: the host reads the events
    and applies them, exactly like :class:`~n8n_launcher.gui.ci_runs.RunsPanel`.
    Selection survives refreshes, and the detail pane always describes the
    currently selected row.
    """

    instances: ClassVar[weakref.WeakSet[MonitoringPanel]] = weakref.WeakSet()

    def __init__(self, parent: tk.Misc, *, on_refresh: Callable[[], None] | None = None) -> None:
        """Build the table, the detail pane and the optional refresh button."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self.on_refresh = on_refresh
        self._events: dict[str, Event] = {}
        self._order: list[str] = []
        self._store: EventStore | None = None
        MonitoringPanel.instances.add(self)

        header = tk.Frame(self, bg=APP_BACKGROUND)
        header.pack(fill="x", padx=14, pady=(10, 4))
        self._summary = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._summary.pack(side="left", expand=True, fill="x")
        if on_refresh is not None:
            ttk.Button(
                header,
                text="Actualiser",
                style="Secondary.TButton",
                command=on_refresh,
            ).pack(side="right", padx=(6, 0))

        self.tree = ttk.Treeview(
            self,
            columns=("time", "level", "name", "message"),
            show="headings",
            height=16,
        )
        self.tree.heading("time", text="Heure")
        self.tree.heading("level", text="Niveau")
        self.tree.heading("name", text="Source")
        self.tree.heading("message", text="Message")
        self.tree.column("time", width=140, stretch=False, anchor="w")
        self.tree.column("level", width=90, stretch=False, anchor="w")
        self.tree.column("name", width=220, stretch=False, anchor="w")
        self.tree.column("message", width=620, stretch=True, anchor="w")
        for tag, color in (
            ("info", _TAG_INFO),
            ("warning", _TAG_WARNING),
            ("error", _TAG_ERROR),
            ("critical", _TAG_CRITICAL),
        ):
            self.tree.tag_configure(tag, background=color)
        self.tree.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self._detail = tk.Label(
            self,
            text=event_detail(None),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            anchor="nw",
            justify="left",
            wraplength=980,
        )
        self._detail.pack(fill="both", expand=True, padx=14, pady=(0, 10))

    def layout_key(self, event: Event) -> str:
        """Return the stable row id for *event* (``id`` when stored)."""
        return f"event-{event.id}" if event.id is not None else f"event-unsaved-{event.name}"

    def apply(
        self,
        events: Sequence[Event],
        *,
        store: EventStore | None = None,
        level: str | None = None,
        query: str | None = None,
    ) -> None:
        """Render the events matching the filters, keeping the selection.

        A snapshot landing after the window closed is ignored instead of
        raising against a destroyed widget.
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        selected = set(self.tree.selection())
        self._store = store
        self._events.clear()
        self._order.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

        visible = filter_events(events, level=level, query=query)
        for event in visible:
            row_id = self.layout_key(event)
            if row_id in self._events:
                continue
            self._events[row_id] = event
            self._order.append(row_id)
            self.tree.insert(
                "",
                "end",
                iid=row_id,
                values=event_row(event),
                tags=(level_tag(event),),
            )

        self._summary.config(text=summary_text(visible, store))
        restored = next((row_id for row_id in self._order if row_id in selected), None)
        if restored is not None:
            self.tree.selection_set(restored)
        self._show_detail(self._events.get(restored) if restored is not None else None)

    def selected_event(self) -> Event | None:
        """Return the event backing the selected row, if any."""
        selection = self.tree.selection()
        return self._events.get(selection[0]) if selection else None

    def _on_select(self, _event: object = None) -> None:
        """Refresh the detail pane when the selection changes."""
        self._show_detail(self.selected_event())

    def _show_detail(self, event: Event | None) -> None:
        """Render *event* (or the hint line) in the detail pane."""
        self._detail.config(text=event_detail(event))

    def refresh_summary(self) -> None:
        """Recompute the header without touching the rows."""
        self._summary.config(
            text=summary_text([self._events[row_id] for row_id in self._order], self._store)
        )


def prompt_monitoring(
    root: tk.Tk,
    store: EventStore | None,
    *,
    level: str | None = None,
    query: str | None = None,
    read: Callable[[], list[Event]] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> tk.Toplevel:
    """Open the monitoring console and return its window.

    ``read`` supplies the events (the host calls it in a background worker and
    re-applies through :meth:`MonitoringPanel.apply`); when it is omitted the
    console reads the store inline, which is only acceptable for the small,
    bounded queries this window issues. ``store`` may be ``None`` when the log
    directory is unwritable — the console then explains the situation instead of
    failing to open.
    """

    def load() -> list[Event]:
        if read is not None:
            return read()
        if store is None:
            return []
        return store.search_events(query, level=level, limit=500)

    window = tk.Toplevel(root)
    window.title("Journal de bord")
    window.geometry("1180x720")
    window.minsize(900, 560)
    window.transient(root)
    window.configure(bg=APP_BACKGROUND)

    filters = tk.Frame(window, bg=APP_BACKGROUND)
    filters.pack(fill="x", padx=14, pady=(10, 0))
    tk.Label(
        filters,
        text="Recherche",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
    ).pack(side="left")
    entry = tk.Entry(filters, width=32)
    entry.insert(0, query or "")
    entry.pack(side="left", padx=(6, 12))

    level_var = tk.StringVar(value=level or "")

    def chosen_level() -> str | None:
        """Return the selected level filter, or ``None`` for every level."""
        return level_var.get() or None

    for label, value in filter_options():
        ttk.Radiobutton(
            filters,
            text=label,
            value=value,
            variable=level_var,
            command=lambda: panel.apply(load(), store=store, level=chosen_level()),
        ).pack(side="left", padx=(0, 8))

    def refresh() -> None:
        """Re-read the events with the current filters and note the outcome."""
        text = entry.get()
        events = load()
        panel.apply(events, store=store, level=chosen_level(), query=text)
        if set_status is not None:
            set_status(f"Journal de bord — {len(events)} événement(s)")

    def export() -> None:
        """Write the visible events next to the journal as JSON."""
        if store is None:
            return
        target = store.path.with_name("events-export.json")
        count = store.export_events(target)
        if set_status is not None:
            set_status(f"{count} événement(s) exportés vers {target}")

    ttk.Button(filters, text="Actualiser", style="Secondary.TButton", command=refresh).pack(
        side="right", padx=(6, 0)
    )
    ttk.Button(filters, text="Exporter (JSON)", style="Secondary.TButton", command=export).pack(
        side="right"
    )

    panel = MonitoringPanel(window)
    panel.pack(fill="both", expand=True)
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh()
    return window


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
            wraplength=940,
        )
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
    window.geometry("1000x680")
    window.minsize(820, 520)
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
    """Return the events that belong to *workspace*, newest first.

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
    return list(reversed(selected))


def workspace_summary(workspace: Workspace, events: Sequence[Event]) -> str:
    """Render the workspace facts a triage session needs, plus incident counts."""
    lines = [
        f"{workspace.name} · état {workspace.state.value} · port {workspace.port}",
        f"DB {db_label(workspace)} · git {git_label(workspace)}",
        f"CI {'activée' if workspace.git.ci_enabled else 'désactivée'}",
    ]
    if workspace.server.enabled:
        deploy = "aucun déploiement enregistré"
        if workspace.server_last_error:
            deploy = f"dernier déploiement en échec : {workspace.server_last_error}"
        elif workspace.server_last_deploy:
            deploy = "dernier déploiement réussi"
        lines.append(f"Serveur {workspace.server.user}@{workspace.server.host} — {deploy}")
    else:
        lines.append("Serveur : non configuré")
    criticals = sum(1 for event in events if event.level.upper() == "CRITICAL")
    errors = sum(1 for event in events if event.level.upper() == "ERROR")
    warnings = sum(1 for event in events if event.level.upper() in ("WARNING", "WARN"))
    lines.append(
        f"Journal : {len(events)} événement(s) · {criticals} critique(s) · "
        f"{errors} erreur(s) · {warnings} avertissement(s)"
    )
    if not events:
        lines.append("Aucun événement enregistré pour ce workspace sur la période.")
    return "\n".join(lines)


class WorkspacePanel(tk.Frame):
    """Per-workspace triage view: facts on top, matching events below."""

    def __init__(
        self,
        parent: tk.Misc,
        workspace: Workspace,
        *,
        on_open_server: Callable[[], None] | None = None,
    ) -> None:
        """Build the summary block, the event table and the optional server action."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self._workspace = workspace
        self._summary = tk.Label(
            self,
            text=workspace_summary(workspace, []),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
            justify="left",
        )
        self._summary.pack(fill="x", padx=14, pady=(8, 6))
        if on_open_server is not None:
            ttk.Button(
                self,
                text="Superviser le serveur…",
                style="Secondary.TButton",
                command=on_open_server,
            ).pack(anchor="w", padx=14, pady=(0, 6))
        self.tree = ttk.Treeview(
            self,
            columns=("time", "level", "message"),
            show="headings",
            height=12,
        )
        self.tree.heading("time", text="Heure")
        self.tree.heading("level", text="Niveau")
        self.tree.heading("message", text="Message")
        self.tree.column("time", width=140, stretch=False, anchor="w")
        self.tree.column("level", width=90, stretch=False, anchor="w")
        self.tree.column("message", width=760, stretch=True, anchor="w")
        for tag, color in (
            ("info", _TAG_INFO),
            ("warning", _TAG_WARNING),
            ("error", _TAG_ERROR),
            ("critical", _TAG_CRITICAL),
        ):
            self.tree.tag_configure(tag, background=color)
        self.tree.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self._detail = tk.Label(
            self,
            text=event_detail(None),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            anchor="nw",
            justify="left",
            wraplength=980,
        )
        self._detail.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self._events: dict[str, Event] = {}

    def apply(self, events: Sequence[Event]) -> None:
        """Render *events*, keeping the selection.

        The caller filters with :func:`workspace_events` first: the panel is a
        pure renderer, so both the window refresh and the host's worker push
        exactly the rows that belong to this workspace.
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        selected = set(self.tree.selection())
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._events.clear()
        for event in events:
            row_id = f"event-{event.id}" if event.id is not None else f"event-unsaved-{event.name}"
            if row_id in self._events:
                continue
            self._events[row_id] = event
            time_text, level, _source, message = event_row(event)
            self.tree.insert(
                "",
                "end",
                iid=row_id,
                values=(time_text, level, message),
                tags=(level_tag(event),),
            )
        self._summary.config(text=workspace_summary(self._workspace, events))
        restored = next((row for row in self._events if row in selected), None)
        if restored is not None:
            self.tree.selection_set(restored)
        self._detail.config(text=event_detail(self._events.get(restored) if restored else None))

    def _on_select(self, _event: object = None) -> None:
        """Show the selected event's context and traceback."""
        selection = self.tree.selection()
        event = self._events.get(selection[0]) if selection else None
        self._detail.config(text=event_detail(event))


def prompt_workspace_monitoring(
    root: tk.Tk,
    workspace: Workspace,
    read: Callable[[], list[Event]],
    *,
    on_open_server: Callable[[], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> tk.Toplevel:
    """Open the per-workspace monitoring window and fill it from *read*.

    ``read`` returns the journal rows; the window keeps only the ones belonging
    to *workspace*, so the caller can hand over a single bounded snapshot.
    """
    window = tk.Toplevel(root)
    window.title(f"Supervision — {workspace.name}")
    window.geometry("1060x700")
    window.minsize(860, 560)
    window.transient(root)
    window.configure(bg=APP_BACKGROUND)

    panel = WorkspacePanel(window, workspace, on_open_server=on_open_server)
    panel.pack(fill="both", expand=True)
    # Same hook as ``server_panel``: the host pushes fresh rows from its worker.
    window.workspace_panel = panel  # type: ignore[attr-defined]

    def refresh() -> None:
        """Re-read the journal and render this workspace's events."""
        events = workspace_events(workspace, read())
        panel.apply(events)
        if set_status is not None:
            set_status(f"Supervision « {workspace.name} » — {len(events)} événement(s)")

    ttk.Button(window, text="Actualiser", style="Secondary.TButton", command=refresh).pack(
        anchor="e", padx=14, pady=(0, 8)
    )
    window.bind("<Escape>", lambda _event: window.destroy())
    refresh()
    return window


__all__ = [
    "CRITICAL_LEVELS",
    "DEFAULT_GROUP_WINDOW_SECONDS",
    "CriticalGate",
    "MonitoringPanel",
    "ServerPanel",
    "ServerSnapshot",
    "WorkspacePanel",
    "event_detail",
    "event_row",
    "filter_events",
    "filter_options",
    "is_critical",
    "level_tag",
    "prompt_monitoring",
    "prompt_server_supervision",
    "prompt_workspace_monitoring",
    "server_snapshot_text",
    "summary_text",
    "workspace_events",
    "workspace_summary",
]
