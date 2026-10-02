"""The board window: the empty page, the card view and the selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox, QPushButton

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


# --- the card's own gestures ------------------------------------------------


def test_the_status_pill_stops_a_running_workspace(qt_app, monkeypatch) -> None:
    """The gesture that answers « how do I stop this? » without a button."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    stop = MagicMock()
    opened = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "stop", stop)
    monkeypatch.setattr(window.workspace_actions, "open", opened)

    window.board.pillActivated.emit(window.model.index(0, 0))

    stop.assert_called_once()
    opened.assert_not_called()


def test_the_status_pill_opens_a_stopped_workspace(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    opened = MagicMock()
    stopped = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "open", opened)
    monkeypatch.setattr(window.workspace_actions, "stop", stopped)

    window.board.pillActivated.emit(window.model.index(0, 0))

    opened.assert_called_once()
    stopped.assert_not_called()


def test_a_pill_click_is_ignored_while_a_task_runs(qt_app, monkeypatch) -> None:
    """A second lifecycle call would race the one already in flight."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    stop = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "stop", stop)
    window.workspace_actions.busyChanged.emit(True)

    window.board.pillActivated.emit(window.model.index(0, 0))

    stop.assert_not_called()


# --- the card menu ---------------------------------------------------------


def _build_menu(monkeypatch, window: MainWindow, workspace: Workspace) -> list[QAction | None]:
    """Run the window's menu builder against a stubbed, non-blocking ``QMenu``.

    The real :class:`QMenu` is stubbed because ``exec`` blocks. The returned list
    is the menu order, with ``None`` standing for a separator.
    """
    built: list[QAction | None] = []

    class _Menu:
        def __init__(self, *args, **kwargs) -> None:
            built.clear()

        def addAction(self, label: str) -> QAction:
            action = QAction(label)
            built.append(action)
            return action

        def addSeparator(self) -> None:
            built.append(None)

        def exec(self, position: QPoint) -> QAction | None:
            return None  # the user dismissed it

    monkeypatch.setattr("n8n_launcher.gui.window.QMenu", _Menu)
    window._show_card_menu(workspace, QPoint(0, 0))
    return built


def _menu_entries(monkeypatch, window: MainWindow, workspace: Workspace) -> list[QAction]:
    """The menu's actionable items, separators dropped."""
    return [item for item in _build_menu(monkeypatch, window, workspace) if item is not None]


def test_the_destructive_item_sits_below_a_separator(qt_app, monkeypatch) -> None:
    """The divider is what keeps *Supprimer…* from reading as a peer of *Arrêter*."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    layout = _build_menu(monkeypatch, window, _workspace(WorkspaceState.STOPPED))
    labels = [None if item is None else item.text() for item in layout]
    assert labels == ["Ouvrir", "Arrêter", None, "Supprimer…"]


def test_the_card_menu_offers_every_action_and_disables_what_cannot_run(
    qt_app, monkeypatch
) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    entries = _menu_entries(monkeypatch, window, _workspace(WorkspaceState.RUNNING))

    assert [action.text() for action in entries] == ["Ouvrir", "Arrêter", "Supprimer…"]
    assert [action.isEnabled() for action in entries] == [True, True, False]


def test_a_running_card_menu_refuses_to_delete_it(qt_app, monkeypatch) -> None:
    """``can_delete`` is the only thing standing between a menu and a lost stack."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    entries = _menu_entries(monkeypatch, window, _workspace(WorkspaceState.RUNNING))
    delete = next(a for a in entries if a.text() == "Supprimer…")
    assert delete.isEnabled() is False


def test_a_stopped_card_menu_allows_delete(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    entries = _menu_entries(monkeypatch, window, _workspace(WorkspaceState.STOPPED))
    delete = next(a for a in entries if a.text() == "Supprimer…")
    assert delete.isEnabled() is True


def test_choosing_delete_asks_first(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    deleted = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "delete", deleted)
    asked = MagicMock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr("n8n_launcher.gui.window.QMessageBox.question", asked)

    window._confirm_delete(_workspace(WorkspaceState.STOPPED))

    asked.assert_called_once()
    deleted.assert_not_called()


def test_confirming_delete_removes_the_workspace(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    deleted = MagicMock()
    monkeypatch.setattr(window.workspace_actions, "delete", deleted)
    monkeypatch.setattr(
        "n8n_launcher.gui.window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    window._confirm_delete(_workspace(WorkspaceState.STOPPED))

    deleted.assert_called_once()


def test_the_card_menu_is_not_offered_while_a_task_runs(qt_app, monkeypatch) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    menu = MagicMock()
    monkeypatch.setattr(window, "_show_card_menu", menu)
    window.workspace_actions.busyChanged.emit(True)

    window._on_context_requested(window.model.index(0, 0))

    menu.assert_not_called()


# --- the header action must not go stale -----------------------------------


def test_the_stop_button_follows_a_state_change_it_did_not_ask_for(qt_app, monkeypatch) -> None:
    """A reconcile can flip the selected card with no gesture of the user's."""
    manager = _Manager([_workspace(WorkspaceState.STOPPED)])
    window = MainWindow(manager)
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert _button(window, "stop").isEnabled() is False

    manager._workspaces = [_workspace(WorkspaceState.RUNNING)]
    window._on_workspace_changed(_workspace(WorkspaceState.RUNNING))

    assert _button(window, "stop").isEnabled() is True


def test_the_stop_button_is_re_derived_after_a_full_reload(qt_app) -> None:
    manager = _Manager([_workspace(WorkspaceState.STOPPED)])
    window = MainWindow(manager)
    window.board.setCurrentIndex(window.model.index(0, 0))

    manager._workspaces = [_workspace(WorkspaceState.RUNNING)]
    window._on_workspaces_changed()

    assert _button(window, "stop").isEnabled() is True


def test_the_stop_button_names_the_workspace_it_will_stop(qt_app) -> None:
    """A greyed button that does not say which card it means reads as broken."""
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert _button(window, "stop").toolTip() == "Arrêter « Mon workspace »"


def test_the_stop_button_has_no_target_tooltip_when_inapplicable(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.STOPPED)]))
    window.board.setCurrentIndex(window.model.index(0, 0))
    assert _button(window, "stop").toolTip() == ""


def test_busy_disables_the_stop_button_even_on_a_running_card(qt_app) -> None:
    window = MainWindow(_Manager([_workspace(WorkspaceState.RUNNING)]))
    window.board.setCurrentIndex(window.model.index(0, 0))

    window.workspace_actions.busyChanged.emit(True)

    assert _button(window, "stop").isEnabled() is False
