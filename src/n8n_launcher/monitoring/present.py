"""Presentation of persisted events: severity, rows, filtering and surfacing.

This module is the pure half of what used to be the Tk journal panel. It holds
three decisions that have nothing to do with widgets and must stay identical
whatever renders them:

* :func:`level_tag` / :func:`event_row` decide how an event is *described*,
  and :func:`event_detail` renders the context of one event.
* :func:`filter_events` is the single definition of what a filter means —
  severity is part of the searched text, so there is no level control that
  could drift from it — and :func:`matches_tokens` adds the second, AND-combined
  filter a focused view installs.
* :class:`CriticalGate` implements the "surface critical incidents" rule: an
  ``ERROR``/``CRITICAL`` event surfaces, while repeats of the same failure
  signature are grouped so a retry loop cannot flood the user.

Deliberately free of any toolkit import: :func:`level_tag` returns a *semantic*
name (``"error"``) rather than a colour, so the mapping from a name to a palette
belongs to the renderer. That is what lets this module be imported — and tested —
on a machine with no display at all.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from ..core.models import Workspace
from .events import Event
from .store import RETENTION_DAYS, EventStore

CRITICAL_LEVELS = frozenset({"ERROR", "CRITICAL"})

# One incident status must not be raised more than once per this many seconds
# for the same failure signature; the very first occurrence always surfaces.
DEFAULT_GROUP_WINDOW_SECONDS = 60.0

# Severity to semantic name. The renderer maps each name to a colour; keeping
# the map here free of hex is what makes the module toolkit-agnostic. Unknown
# levels fall back to "info" so an unexpected value is still renderable.
_LEVEL_TAGS = {
    "DEBUG": "info",
    "INFO": "info",
    "WARNING": "warning",
    "WARN": "warning",
    "ERROR": "error",
    "CRITICAL": "critical",
}


def level_tag(event: Event) -> str:
    """Return the semantic severity name of *event* (e.g. ``"error"``)."""
    return _LEVEL_TAGS.get(event.level.upper(), "info")


def is_critical(event: Event) -> bool:
    """Return whether *event* counts as a critical incident."""
    return event.level.upper() in CRITICAL_LEVELS


def _format_time(value: datetime) -> str:
    """Render an event timestamp as local wall-clock time."""
    return value.astimezone().strftime("%d/%m %H:%M:%S")


def event_row(event: Event) -> tuple[str, str, str, str]:
    """Return the columns of *event* (time, level, source, message)."""
    return (_format_time(event.timestamp), event.level, event.name, event.message)


def event_detail(event: Event | None) -> str:
    """Render the context and traceback of one event.

    ``None`` (nothing selected) yields the hint line, so a detail view is never
    blank and the user knows the row is clickable. The line is also what a
    "copy" action puts on the clipboard: one event, whole, as a bug report needs
    it.
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
    subject: str = "",
) -> str:
    """Return the line describing *scope*, the visible rows and the retention.

    ``store`` is only used to know whether a journal exists at all: the summary
    stays short and the full path lives in the export target instead. *subject*
    names the view the rows are filtered on, so a narrowed table never reads as a
    log that has stopped coming.
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
    if subject:
        parts.append(f"sujet : {subject}")
    return " · ".join(parts)


def event_text(event: Event) -> str:
    """Return every text of *event* a filter may look at, as one string.

    The context is searched as its JSON dump rather than key by key, so a query
    matches a value whatever key it is filed under.
    """
    parts = [event.name, event.message, event.level]
    if event.context:
        parts.append(json.dumps(event.context, ensure_ascii=False, sort_keys=True))
    if event.exception:
        parts.append(event.exception)
    return " ".join(parts)


def _haystack(event: Event) -> str:
    """Return the case-folded text a free-text query is matched against."""
    return event_text(event).casefold()


def matches_tokens(event: Event, tokens: Sequence[str]) -> bool:
    """Return whether *event* carries any of *tokens*.

    Unlike the free-text query, the match is **case-sensitive**: a view's subject
    tokens are chosen to be distinctive ("CI", "Published"), and a
    case-insensitive match on a two-letter token would sweep in every event
    carrying "credential", "specifique" or "spécifique". No token means no
    narrowing at all.
    """
    wanted = [token for token in tokens if token]
    if not wanted:
        return True
    text = event_text(event)
    return any(token in text for token in wanted)


def filter_events(
    events: Sequence[Event],
    *,
    query: str | None = None,
    tokens: Sequence[str] = (),
) -> list[Event]:
    """Return the events matching a case-insensitive *query* and *tokens*.

    The two filters combine with AND and answer two different questions: the
    *query* is what the user typed, the *tokens* are the subject of the view they
    are looking at. They run here rather than in SQL so a renderer and an
    in-memory snapshot can never disagree about what a filter means. Severity
    needs no dedicated control because :func:`_haystack` includes the level:
    typing ``ERROR`` (or ``WARNING``…) narrows the rows to that severity.
    """
    needle = query.strip().casefold() if query else ""
    wanted = [token for token in tokens if token]
    if not needle and not wanted:
        return list(events)
    return [
        event
        for event in events
        if (not wanted or matches_tokens(event, wanted))
        and (not needle or needle in _haystack(event))
    ]


class CriticalGate:
    """Decide when a critical event must surface, grouping repeats.

    The first ``ERROR``/``CRITICAL`` event always surfaces. The same signature
    (``name`` + ``level``) then stays quiet for :attr:`window_seconds`, which
    keeps one failing operation — retried by a timer — from replacing the status
    message per attempt while a *different* failure still gets through
    immediately.
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
        """Return whether *event* should surface now."""
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
    "event_detail",
    "event_row",
    "event_text",
    "filter_events",
    "is_critical",
    "level_tag",
    "matches_tokens",
    "summary_text",
    "workspace_events",
]
