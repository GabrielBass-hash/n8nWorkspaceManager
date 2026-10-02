"""The board window: the empty page, the card view and the selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtWidgets import QPushButton

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.browser import WebAppLaunchError
from n8n_launcher.gui.card_delegate import CardDelegate
from n8n_launcher.gui.window import MainWindow
from n8n_launcher.workspaces.manager import Reachable


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


def test_the_header_does_not_duplicate_contextual_workspace_actions(qt_app) -> None:
    window = MainWindow(_Manager([_workspace()]))

    assert window.findChild(QPushButton, "new") is not None
    assert window.findChild(QPushButton, "start") is None
    assert window.findChild(QPushButton, "stop") is not None
    assert window.findChild(QPushButton, "stop").isEnabled() is False


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


def test_double_click_opens_a_stopped_workspace(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    open_workspace = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "open", open_workspace)

    window.board.doubleClicked.emit(window.model.index(0, 0))

    open_workspace.assert_called_once()


def test_double_click_opens_a_running_workspace_without_starting_it(qt_app, monkeypatch) -> None:
    """The gesture on a live card is *open*, never a second ``docker up``."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    open_workspace = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "open", open_workspace)

    window.board.doubleClicked.emit(window.model.index(0, 0))

    open_workspace.assert_called_once()


def test_double_click_is_inert_while_a_start_owns_the_outcome(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STARTING)]))
    open_workspace = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "open", open_workspace)

    window.board.doubleClicked.emit(window.model.index(0, 0))

    open_workspace.assert_not_called()


def _record_open(monkeypatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "n8n_launcher.gui.window.open_web_app",
        lambda url, *, reuse: calls.append((url, reuse)),
    )
    return calls


def test_a_ready_workspace_is_opened_as_a_web_app(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace()]))
    calls = _record_open(monkeypatch)

    window.workspace_actions.ready.emit(Reachable(workspace=_workspace(), started=True))

    assert calls == [("http://127.0.0.1:5678", False)]


def test_an_already_running_instance_may_reuse_its_window(qt_app, monkeypatch) -> None:
    """Nothing was started, so the window already showing it can be raised."""
    window = MainWindow(_Manager([_workspace()]))
    calls = _record_open(monkeypatch)

    window.workspace_actions.ready.emit(Reachable(workspace=_workspace(), started=False))

    assert calls == [("http://127.0.0.1:5678", True)]


def test_a_failed_open_is_surfaced(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace()]))

    def refuse(_url: str, *, reuse: bool) -> None:
        raise WebAppLaunchError("Aucun navigateur Chromium compatible")

    monkeypatch.setattr("n8n_launcher.gui.window.open_web_app", refuse)
    failures: list[tuple[str, str]] = []
    monkeypatch.setattr(
        window,
        "_on_failed",
        lambda title, message: failures.append((title, message)),
    )

    window.workspace_actions.ready.emit(Reachable(workspace=_workspace(), started=False))

    assert failures == [("Ouverture de n8n impossible", "Aucun navigateur Chromium compatible")]


def test_busy_locks_the_actions_then_restores_them(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    window.board.setCurrentIndex(window.model.index(0, 0))

    window.workspace_actions.busyChanged.emit(True)
    assert _button(window, "new").isEnabled() is False

    window.workspace_actions.busyChanged.emit(False)
    assert _button(window, "new").isEnabled() is True
