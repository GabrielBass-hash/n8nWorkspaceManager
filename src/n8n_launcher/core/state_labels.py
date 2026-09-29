"""Human-readable labels for workspace states.

Kept apart from any palette on purpose: the *word* a state is called in French is
a domain fact, the colour it is painted is a rendering decision, and only the
latter belongs to a view. Separating them is what lets a CLI, a log line and a
GUI all name a state identically without importing a toolkit.
"""

from __future__ import annotations

from .models import WorkspaceState

STATE_LABELS = {
    WorkspaceState.STOPPED: "Arrêté",
    WorkspaceState.STARTING: "Démarrage",
    WorkspaceState.RUNNING: "En cours",
    WorkspaceState.STOPPING: "Arrêt",
    WorkspaceState.ERROR: "Erreur",
}


def state_label(state: WorkspaceState) -> str:
    """Return the French display label for *state*.

    An unmapped state falls back to its own ``value`` rather than raising: this
    is called on whatever a workspace happens to carry, including a state a newer
    version added, and naming it is more useful than crashing the caller.
    """
    return STATE_LABELS.get(state, state.value)


__all__ = ["STATE_LABELS", "state_label"]
