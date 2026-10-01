"""The board window: the empty page, the card view and the selection."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QPushButton

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.card_delegate import CardDelegate
from n8n_launcher.gui.window import MainWindow


class _Manager:
    """The manager surface the window touches: ``list`` plus the observer API."""

    def __init__(self, workspaces: list[Workspace]) -> None:
        self._workspaces = workspaces
        self.observers: list[object] = []

    def list(self) -> list[Workspace]:
        return list(self._workspaces)

    def add_observer(self, observer: object) -> None:
        self.observers.append(observer)

    def remove_observer(self, observer: object) -> None:
        if observer in self.observers:
            self.observers.remove(observer)


def _workspace(state: WorkspaceState = WorkspaceState.RUNNING) -> Workspace:
    return Workspace(
        id="ws",
        name="Mon workspace",
        workflows_dir=Path("/tmp/ws"),
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
        state=state,
    )


def test_an_empty_board_shows_the_empty_page(qt_app) -> None:
    window = MainWindow(_Manager([]))
    assert window.model.rowCount() == 0
    assert window.shows_empty_state is True
    assert window.selected_workspace() is None


def test_a_populated_board_shows_the_cards(qt_app) -> None:
    window = MainWindow(_Manager([_workspace()]))
    assert window.model.rowCount() == 1
    assert window.shows_empty_state is False
    assert isinstance(window.board.itemDelegate(), CardDelegate)


def test_the_board_selection_reports_the_workspace(qt_app) -> None:
    workspace = _workspace()
    window = MainWindow(_Manager([workspace]))
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert window.selected_workspace() is workspace


def test_the_window_unsubscribes_when_it_closes(qt_app) -> None:
    manager = _Manager([_workspace()])
    window = MainWindow(manager)
    assert len(manager.observers) == 1
    window.show()
    window.close()
    assert manager.observers == []


def _button(window: MainWindow, name: str) -> QPushButton:
    button = window.findChild(QPushButton, name)
    assert button is not None
    return button


def test_a_stopped_workspace_can_only_be_started(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert _button(window, "start").isEnabled() is True
    assert _button(window, "stop").isEnabled() is False


def test_a_running_workspace_can_only_be_stopped(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert _button(window, "start").isEnabled() is False
    assert _button(window, "stop").isEnabled() is True


def test_busy_locks_the_actions_then_restores_them(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    window.board.setCurrentIndex(window.model.index(0, 0))

    window.workspace_actions.busyChanged.emit(True)
    assert _button(window, "new").isEnabled() is False
    assert _button(window, "start").isEnabled() is False

    window.workspace_actions.busyChanged.emit(False)
    assert _button(window, "new").isEnabled() is True
    assert _button(window, "start").isEnabled() is True
