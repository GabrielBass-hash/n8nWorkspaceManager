"""Pure presentation facts about a workspace's lifecycle.

What a row shows and which actions it offers are *decisions*, so they live here
as functions over plain values — no Qt, no widgets, no I/O — exactly like the
sizing rules in :mod:`n8n_launcher.gui_utils.text`. A view renders this; it does
not re-derive it. The test is a plain assertion, not a widget walk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.models import DbMode, Workspace, WorkspaceState


class StatusTone(StrEnum):
    """The colour family a status belongs to; the view maps it to a palette."""

    NEUTRAL = "neutral"
    ACTIVE = "active"
    PENDING = "pending"
    ERROR = "error"


#: French label for each persisted state.
_LABELS: dict[WorkspaceState, str] = {
    WorkspaceState.STOPPED: "Arrêté",
    WorkspaceState.STARTING: "Démarrage…",
    WorkspaceState.RUNNING: "En marche",
    WorkspaceState.STOPPING: "Arrêt…",
    WorkspaceState.ERROR: "Erreur",
}

_TONES: dict[WorkspaceState, StatusTone] = {
    WorkspaceState.STOPPED: StatusTone.NEUTRAL,
    WorkspaceState.STARTING: StatusTone.PENDING,
    WorkspaceState.RUNNING: StatusTone.ACTIVE,
    WorkspaceState.STOPPING: StatusTone.PENDING,
    WorkspaceState.ERROR: StatusTone.ERROR,
}


@dataclass(frozen=True)
class WorkspaceStatus:
    """Everything a row needs to describe and act on one lifecycle state."""

    state: WorkspaceState
    label: str
    tone: StatusTone
    can_start: bool
    can_stop: bool
    can_delete: bool
    can_open: bool


def state_label(state: WorkspaceState) -> str:
    """Return the human-readable French label for *state*."""
    return _LABELS[state]


def can_start(state: WorkspaceState) -> bool:
    """Return True when a workspace in *state* may be started.

    An ``ERROR`` workspace can be retried; one that is already starting or
    running cannot (a second ``docker up`` would race the first).
    """
    return state in (WorkspaceState.STOPPED, WorkspaceState.ERROR)


def can_stop(state: WorkspaceState) -> bool:
    """Return True only for a fully running workspace.

    A workspace mid-start is not offered a stop button: the start is already
    holding the git/DB work and a concurrent teardown would fight it.
    """
    return state is WorkspaceState.RUNNING


def can_delete(state: WorkspaceState) -> bool:
    """Return True when a workspace may be removed (only when stopped)."""
    return state is WorkspaceState.STOPPED


def can_open(state: WorkspaceState) -> bool:
    """Return True when opening the workspace's n8n from its card makes sense.

    A **running** workspace is the interesting case: opening it is not a start,
    so it must never be gated behind ``can_start`` — Docker already holds a
    running stack and a second ``docker up`` would only race the first. A
    ``STARTING`` workspace is left alone because the start in flight owns the
    outcome (it opens the instance itself when it completes), and a
    ``STOPPING`` one because it is on its way out.
    """
    return state in (
        WorkspaceState.STOPPED,
        WorkspaceState.ERROR,
        WorkspaceState.RUNNING,
    )


def status_for(state: WorkspaceState) -> WorkspaceStatus:
    """Build the full :class:`WorkspaceStatus` for *state*."""
    return WorkspaceStatus(
        state=state,
        label=_LABELS[state],
        tone=_TONES[state],
        can_start=can_start(state),
        can_stop=can_stop(state),
        can_delete=can_delete(state),
        can_open=can_open(state),
    )


def summary_line(workspace: Workspace) -> str:
    """Return the one-line sub-title shown under a workspace's name."""
    database = "Postgres managé" if workspace.db.mode is DbMode.MANAGED else "sans base locale"
    line = f"Port {workspace.port} · {database}"
    if workspace.restart_required:
        line += " · redémarrage requis"
    return line


__all__ = [
    "StatusTone",
    "WorkspaceStatus",
    "can_delete",
    "can_open",
    "can_start",
    "can_stop",
    "state_label",
    "status_for",
    "summary_line",
]
