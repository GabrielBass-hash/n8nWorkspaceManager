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


class WorkspaceAction(StrEnum):
    """What a card can be asked to do; the view binds it to a gesture."""

    OPEN = "open"
    STOP = "stop"
    DELETE = "delete"


@dataclass(frozen=True)
class CardAction:
    """One offered action: its label, and whether it currently applies.

    The label lives here rather than in a menu builder so that what the user
    reads and what the tests assert are the same string. ``destructive`` marks
    the action a view must confirm before running.
    """

    action: WorkspaceAction
    label: str
    enabled: bool
    destructive: bool = False


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
    """Return True whenever something is still up that a teardown can remove.

    Deliberately wider than ``state is RUNNING``: a failed start leaves the
    stack running (the error lands *after* ``docker up``) and a crash-looping
    container is reported as ``STARTING``, so both are states where the user is
    looking at a live workspace with no way to bring it down. What is left out
    is only what cannot be running: ``STOPPED`` and the transient ``STOPPING``.

    The guard against stopping a start *this launcher* is in flight is not here
    — it has no state to look at — it is the actions layer's busy lock, which
    blocks the gesture for the whole duration of the task.
    """
    return state not in (WorkspaceState.STOPPED, WorkspaceState.STOPPING)


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


#: The action a card's status pill performs, per state. ``None`` means the pill
#: is a label only: a start or a stop already in flight owns the outcome, and a
#: second gesture would only race it.
_PRIMARY_ACTIONS: dict[WorkspaceState, WorkspaceAction | None] = {
    WorkspaceState.STOPPED: WorkspaceAction.OPEN,
    WorkspaceState.STARTING: None,
    WorkspaceState.RUNNING: WorkspaceAction.STOP,
    WorkspaceState.STOPPING: None,
    # An errored workspace is offered *open* to stay consistent with what a
    # double-click already does on it; ``Arrêter`` stays one gesture away in the
    # card menu, where :func:`can_stop` enables it.
    WorkspaceState.ERROR: WorkspaceAction.OPEN,
}


def primary_action(state: WorkspaceState) -> WorkspaceAction | None:
    """Return what a click on *state*'s status pill should do, or ``None``.

    The pill is the one control a card already wears, so it carries the action
    that needs no second gesture to reach. It is **stop** for a live workspace —
    the teardown a user cannot reach any other way — and **open** otherwise,
    matching the double-click rather than inventing a third rule.
    """
    return _PRIMARY_ACTIONS[state]


def card_actions(state: WorkspaceState) -> tuple[CardAction, ...]:
    """Return the actions offered on *state*'s card, in display order.

    Every entry is returned whether or not it applies: a disabled item tells the
    user the action exists and why it is not available right now, which a
    missing item cannot. There is no separate "Démarrer" entry because opening
    already starts a workspace Docker is not holding.
    """
    status = status_for(state)
    return (
        CardAction(
            action=WorkspaceAction.OPEN,
            label="Ouvrir",
            enabled=status.can_open,
        ),
        CardAction(
            action=WorkspaceAction.STOP,
            label="Arrêter",
            enabled=status.can_stop,
        ),
        CardAction(
            action=WorkspaceAction.DELETE,
            label="Supprimer…",
            enabled=status.can_delete,
            destructive=True,
        ),
    )


def summary_line(workspace: Workspace) -> str:
    """Return the one-line sub-title shown under a workspace's name."""
    database = "Postgres managé" if workspace.db.mode is DbMode.MANAGED else "sans base locale"
    line = f"Port {workspace.port} · {database}"
    if workspace.restart_required:
        line += " · redémarrage requis"
    return line


__all__ = [
    "CardAction",
    "StatusTone",
    "WorkspaceAction",
    "WorkspaceStatus",
    "can_delete",
    "can_open",
    "can_start",
    "can_stop",
    "card_actions",
    "primary_action",
    "state_label",
    "status_for",
    "summary_line",
]
