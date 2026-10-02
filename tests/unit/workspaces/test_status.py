"""The status rules are pure facts, so they are plain assertions."""

from __future__ import annotations

from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.workspaces.status import (
    StatusTone,
    WorkspaceAction,
    can_delete,
    can_open,
    can_start,
    can_stop,
    card_actions,
    primary_action,
    state_label,
    status_for,
    summary_line,
)


def _workspace(
    *,
    state: WorkspaceState = WorkspaceState.STOPPED,
    port: int = 5678,
    db: DbConfig | None = None,
    restart_required: bool = False,
) -> Workspace:
    return Workspace(
        id="demo123",
        name="Demo",
        workflows_dir=Path("/tmp/demo"),
        port=port,
        db=db or DbConfig(DbMode.NONE),
        state=state,
        restart_required=restart_required,
    )


def test_every_state_has_a_label_and_a_tone() -> None:
    for state in WorkspaceState:
        status = status_for(state)
        assert status.label
        assert isinstance(status.tone, StatusTone)


def test_can_start_is_true_only_for_stopped_and_error() -> None:
    for state in WorkspaceState:
        assert can_start(state) is (state in {WorkspaceState.STOPPED, WorkspaceState.ERROR})


def test_can_stop_is_refused_only_where_nothing_can_be_running() -> None:
    """A failed start leaves containers up, so ``ERROR`` must stay stoppable."""
    for state in WorkspaceState:
        assert can_stop(state) is (state not in {WorkspaceState.STOPPED, WorkspaceState.STOPPING})


def test_a_live_but_broken_workspace_is_still_stoppable() -> None:
    """The dead end this rule exists for: an errored card nobody could stop."""
    assert can_stop(WorkspaceState.ERROR) is True
    assert can_stop(WorkspaceState.STARTING) is True


def test_can_delete_is_true_only_for_stopped() -> None:
    for state in WorkspaceState:
        assert can_delete(state) is (state is WorkspaceState.STOPPED)


def test_can_open_covers_a_running_workspace_not_only_a_stopped_one() -> None:
    """A card you can open is not the same as a card you can start."""
    for state in WorkspaceState:
        assert can_open(state) is (
            state in {WorkspaceState.STOPPED, WorkspaceState.ERROR, WorkspaceState.RUNNING}
        )


def test_can_open_is_refused_while_a_transition_owns_the_outcome() -> None:
    assert can_open(WorkspaceState.STARTING) is False
    assert can_open(WorkspaceState.STOPPING) is False


def test_labels_are_the_french_ones() -> None:
    assert state_label(WorkspaceState.STOPPED) == "Arrêté"
    assert state_label(WorkspaceState.RUNNING) == "En marche"
    assert state_label(WorkspaceState.ERROR) == "Erreur"


def test_running_has_the_active_tone_and_error_has_the_error_tone() -> None:
    assert status_for(WorkspaceState.RUNNING).tone is StatusTone.ACTIVE
    assert status_for(WorkspaceState.ERROR).tone is StatusTone.ERROR


def test_summary_line_names_the_managed_database() -> None:
    line = summary_line(_workspace(port=5680, db=DbConfig(DbMode.MANAGED)))
    assert line == "Port 5680 · Postgres managé"


def test_summary_line_reports_a_missing_database() -> None:
    assert summary_line(_workspace(port=5681)) == "Port 5681 · sans base locale"


def test_summary_line_warns_about_a_required_restart() -> None:
    line = summary_line(_workspace(restart_required=True))
    assert line.endswith("redémarrage requis")


@pytest.mark.parametrize("state", list(WorkspaceState))
def test_status_for_round_trips_the_state(state: WorkspaceState) -> None:
    assert status_for(state).state is state


def test_the_pill_stops_a_running_workspace() -> None:
    """The one action that must not need a second gesture to reach."""
    assert primary_action(WorkspaceState.RUNNING) is WorkspaceAction.STOP


def test_the_pill_opens_a_card_that_is_not_running() -> None:
    assert primary_action(WorkspaceState.STOPPED) is WorkspaceAction.OPEN
    assert primary_action(WorkspaceState.ERROR) is WorkspaceAction.OPEN


def test_the_pill_is_a_label_only_while_a_transition_owns_the_outcome() -> None:
    assert primary_action(WorkspaceState.STARTING) is None
    assert primary_action(WorkspaceState.STOPPING) is None


@pytest.mark.parametrize("state", list(WorkspaceState))
def test_every_state_has_exactly_one_primary_action_or_none(state: WorkspaceState) -> None:
    action = primary_action(state)
    assert action is None or isinstance(action, WorkspaceAction)


@pytest.mark.parametrize("state", list(WorkspaceState))
def test_the_card_menu_always_offers_the_same_three_actions(state: WorkspaceState) -> None:
    """A missing item reads as a missing feature; a disabled one does not."""
    assert [entry.action for entry in card_actions(state)] == [
        WorkspaceAction.OPEN,
        WorkspaceAction.STOP,
        WorkspaceAction.DELETE,
    ]


def test_the_card_menu_labels_are_the_french_ones() -> None:
    labels = {entry.action: entry.label for entry in card_actions(WorkspaceState.RUNNING)}
    assert labels == {
        WorkspaceAction.OPEN: "Ouvrir",
        WorkspaceAction.STOP: "Arrêter",
        WorkspaceAction.DELETE: "Supprimer…",
    }


def test_only_delete_is_marked_destructive() -> None:
    entries = card_actions(WorkspaceState.RUNNING)
    assert [entry.action for entry in entries if entry.destructive] == [WorkspaceAction.DELETE]


def test_the_card_menu_agrees_with_the_capability_rules() -> None:
    for state in WorkspaceState:
        status = status_for(state)
        enabled = {entry.action: entry.enabled for entry in card_actions(state)}
        assert enabled[WorkspaceAction.OPEN] is status.can_open
        assert enabled[WorkspaceAction.STOP] is status.can_stop
        assert enabled[WorkspaceAction.DELETE] is status.can_delete


def test_a_running_card_can_be_opened_and_stopped_but_not_deleted() -> None:
    enabled = {e.action: e.enabled for e in card_actions(WorkspaceState.RUNNING)}
    assert enabled[WorkspaceAction.OPEN] is True
    assert enabled[WorkspaceAction.STOP] is True
    assert enabled[WorkspaceAction.DELETE] is False
