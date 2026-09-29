"""Unit tests for the state labels (:mod:`n8n_launcher.core.state_labels`).

The French *word* for a state is a domain fact, not a palette entry: a CLI, a log
line and a GUI must all name a state identically, and only the colour may differ
per front end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from n8n_launcher.core.models import WorkspaceState
from n8n_launcher.core.state_labels import STATE_LABELS, state_label


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (WorkspaceState.STOPPED, "Arrêté"),
        (WorkspaceState.STARTING, "Démarrage"),
        (WorkspaceState.RUNNING, "En cours"),
        (WorkspaceState.STOPPING, "Arrêt"),
        (WorkspaceState.ERROR, "Erreur"),
    ],
)
def test_state_label_names_every_state_in_french(state: WorkspaceState, label: str) -> None:
    assert state_label(state) == label


def test_every_state_is_named() -> None:
    # A state with no word would render as its raw enum name, so the table is
    # asserted to be total over the enum rather than left to the fallback.
    assert set(STATE_LABELS) == set(WorkspaceState)


@dataclass(frozen=True)
class _FutureState:
    """Stand-in for a state a newer version added and this build does not know."""

    value: str = "migrating"


def test_an_unknown_state_falls_back_to_its_own_value() -> None:
    # Called on whatever a workspace happens to carry, so naming a state beats
    # crashing the caller over one this build's table does not map.
    assert state_label(cast(WorkspaceState, _FutureState())) == "migrating"
