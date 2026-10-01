"""The list model caches rows and answers the roles a delegate asks for."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.workspace_model import WorkspaceListModel
from n8n_launcher.workspaces.status import StatusTone


def _workspace(
    workspace_id: str,
    name: str,
    state: WorkspaceState = WorkspaceState.STOPPED,
) -> Workspace:
    return Workspace(
        id=workspace_id,
        name=name,
        workflows_dir=Path("/tmp") / workspace_id,
        port=5678,
        db=DbConfig(DbMode.NONE),
        state=state,
    )


class _FakeManager:
    def __init__(self, rows: list[Workspace]) -> None:
        self.rows = list(rows)
        self.list_calls = 0

    def list(self) -> list[Workspace]:
        self.list_calls += 1
        return list(self.rows)


def _model(*rows: Workspace) -> tuple[WorkspaceListModel, _FakeManager]:
    manager = _FakeManager(list(rows))
    model = WorkspaceListModel(manager)  # type: ignore[arg-type]
    model.refresh()
    return model, manager


def test_refresh_reads_the_manager_and_sets_the_row_count(qtbot) -> None:
    model, manager = _model(_workspace("a", "Alpha"), _workspace("b", "Beta"))
    assert model.rowCount() == 2
    assert manager.list_calls == 1


def test_display_role_returns_the_name(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"))
    index = model.index(0, 0)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "Alpha"


def test_workspace_role_returns_the_object(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"))
    got = model.data(model.index(0, 0), WorkspaceListModel.WorkspaceRole)
    assert isinstance(got, Workspace)
    assert got.id == "a"


def test_status_role_returns_the_status(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha", WorkspaceState.RUNNING))
    got = model.data(model.index(0, 0), WorkspaceListModel.StatusRole)
    assert got.tone is StatusTone.ACTIVE
    assert got.can_stop is True


def test_an_invalid_index_has_no_data_or_workspace(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"))
    invalid = QModelIndex()
    assert model.data(invalid) is None
    assert model.workspace_at(invalid) is None
    assert model.status_at(invalid) is None


def test_a_child_of_a_child_has_no_rows(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"))
    assert model.rowCount(model.index(0, 0)) == 0


def test_index_of_finds_a_row_or_returns_an_invalid_index(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"), _workspace("b", "Beta"))
    assert model.index_of("b").row() == 1
    assert not model.index_of("missing").isValid()


def test_apply_workspace_replaces_a_row_and_emits(qtbot) -> None:
    model, _ = _model(_workspace("a", "Alpha"))
    updated = _workspace("a", "Renamed", WorkspaceState.RUNNING)

    with qtbot.waitSignal(model.dataChanged):
        model.apply_workspace(updated)

    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "Renamed"
    assert model.data(model.index(0, 0), WorkspaceListModel.StatusRole).can_stop is True


def test_apply_workspace_reloads_when_it_is_unknown(qtbot) -> None:
    model, manager = _model(_workspace("a", "Alpha"))
    manager.rows.append(_workspace("b", "Beta"))

    model.apply_workspace(_workspace("b", "Beta"))

    assert model.rowCount() == 2
