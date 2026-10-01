"""The status rules are pure facts, so they are plain assertions."""

from __future__ import annotations

from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.workspaces.status import (
    StatusTone,
    can_delete,
    can_start,
    can_stop,
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


def test_can_stop_is_true_only_for_running() -> None:
    for state in WorkspaceState:
        assert can_stop(state) is (state is WorkspaceState.RUNNING)


def test_can_delete_is_true_only_for_stopped() -> None:
    for state in WorkspaceState:
        assert can_delete(state) is (state is WorkspaceState.STOPPED)


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
