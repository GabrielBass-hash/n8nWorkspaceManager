"""GUI shell tests: row rendering, selection, launch, poll and delete."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, WorkspaceState
from n8n_launcher.gui.display import GitRowStatus

from helpers import (  # noqa: E402
    ACTIVE_CHIP,
    FakeRoot,
    FakeTk,
    HoldingThread,
    INACTIVE_CHIP,
    WARN_CHIP,
    _drain_queue,
    make_workspace,
    row_action_button,
    row_action_text,
    row_chip,
    row_chip_colors,
    row_chip_text,
    row_delete_button,
    row_label,
    row_status_label,
    row_status_text,
    row_text,
)
from n8n_launcher.gui import LauncherApp


def test_refresh_renders_workflow_rows_with_indicators(app, tmp_path) -> None:
    git_dir = tmp_path / "GitWs"
    (git_dir / ".git").mkdir(parents=True)
    (git_dir / "db" / "migrations").mkdir(parents=True)
    (git_dir / "db" / "migrations" / "001.sql").write_text("select 1;")
    (git_dir / "n8nPipelines").mkdir(parents=True)
    (git_dir / "n8nPipelines" / "flow.json").write_text("{}")
    rich = make_workspace(tmp_path, "GitWs", 5700)
    rich.workflows_dir = git_dir
    rich.state = WorkspaceState.RUNNING
    plain = make_workspace(tmp_path, "Plain", 5680)
    app.manager.list.return_value = [rich, plain]

    def rich_git_status() -> GitRowStatus:
        return GitRowStatus(is_repo=True, remote_url="https://example.test/r.git")

    def plain_git_status() -> GitRowStatus:
        return GitRowStatus()

    statuses = {rich.id: rich_git_status, plain.id: plain_git_status}

    def pick_status(_workspace):
        return statuses[_workspace.id]()

    with patch("n8n_launcher.gui.display.git_row_status", side_effect=pick_status):
        app.app.refresh()

    assert row_text(app, "ws-gitws") == "GitWs"
    assert row_status_text(app, "ws-gitws") == "En cours"
    assert row_chip_text(app, "ws-gitws", "port_chip") == ":5700"
    assert row_chip_text(app, "ws-gitws", "db_chip") == "locale"
    assert row_chip_colors(app, "ws-gitws", "db_chip") == ACTIVE_CHIP
    assert row_chip_text(app, "ws-gitws", "git_chip") == "git"
    assert row_chip_colors(app, "ws-gitws", "git_chip") == ACTIVE_CHIP
    assert row_chip_text(app, "ws-gitws", "pipelines_chip") == "1"
    assert row_action_text(app, "ws-gitws") == "Arrêter"
    assert row_text(app, "ws-plain") == "Plain"
    assert row_status_text(app, "ws-plain") == "Arrêté"
    assert row_chip_text(app, "ws-plain", "port_chip") == ":5680"
    assert row_chip_text(app, "ws-plain", "db_chip") == "locale"
    assert row_chip_colors(app, "ws-plain", "db_chip") == INACTIVE_CHIP
    assert row_chip_colors(app, "ws-plain", "git_chip") == INACTIVE_CHIP
    assert row_chip_text(app, "ws-plain", "pipelines_chip") == "0"
    assert row_action_text(app, "ws-plain") == "Démarrer"
    ws_ws = app.app._rows["ws-gitws"][0]
    assert "<Enter>" in ws_ws.git_chip._bindings
    assert "<Leave>" in ws_ws.git_chip._bindings


def test_git_row_status_chips(app, tmp_path) -> None:
    cases = [
        (GitRowStatus(), ("git", INACTIVE_CHIP, "")),
        (GitRowStatus(is_repo=True), ("git", ACTIVE_CHIP, "")),
        (GitRowStatus(is_repo=True, dirty=True), ("git", WARN_CHIP, "●")),
        (GitRowStatus(is_repo=True, dirty=True, diverged=True), ("git ⇅", WARN_CHIP, "●")),
        (GitRowStatus(is_repo=True, diverged=True), ("git ⇅", WARN_CHIP, "")),
        (GitRowStatus(is_repo=True, push_failed=True), ("git ✗", INACTIVE_CHIP, "")),
        (GitRowStatus(is_repo=True, push_failed=True, dirty=True), ("git ✗", INACTIVE_CHIP, "●")),
    ]
    folder = tmp_path / "g"
    folder.mkdir()
    ws = make_workspace(tmp_path, "G", 5700)
    ws.workflows_dir = folder
    app.manager.list.return_value = [ws]

    for status, (expected_label, expected_palette, expected_dot) in cases:
        with patch("n8n_launcher.gui.display.git_row_status", return_value=status):
            app.app.refresh()
        assert row_chip_text(app, "ws-g", "git_chip") == expected_label, status
        assert row_chip_colors(app, "ws-g", "git_chip") == expected_palette, status
        dot = row_chip(app, "ws-g", "dirty_dot")
        assert dot._options["text"] == expected_dot, status


def test_each_row_has_its_own_delete_button(app) -> None:
    for workspace_id in ("ws-running", "ws-stopped"):
        button = row_delete_button(app, workspace_id)
        assert button.packed
        assert callable(button.command)


def test_return_binding_launches_on_workspace_list(app) -> None:
    assert "<Return>" in app.app.workspace_list._bindings


def test_double_click_launches_selected(app) -> None:
    app.app._handle_double("ws-stopped")
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with(
        "ws-stopped", on_ready=app.app._wait_until_healthy
    )
    app.browser.assert_called_once_with("http://127.0.0.1:5680")


def test_repeated_launch_is_ignored_while_first_runs(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    held = make_workspace(tmp_path, "Hold", 5690)
    manager.list.return_value = [held]
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )

    launcher._select_row("ws-hold")
    HoldingThread.instances.clear()
    with patch("n8n_launcher.gui.app.threading.Thread", HoldingThread):
        launcher.launch_selected()
        assert launcher._launching == "ws-hold"
        launcher.launch_selected()

    assert len(HoldingThread.instances) == 1
    HoldingThread.instances[0].target()
    assert launcher._launching is None
    manager.ensure_running.assert_called_once_with("ws-hold", on_ready=launcher._wait_until_healthy)


def test_launch_dispatches_to_manager(app) -> None:
    app.app._select_row("ws-stopped")
    app.app.launch_selected()

    app.manager.ensure_running.assert_called_once_with(
        "ws-stopped", on_ready=app.app._wait_until_healthy
    )
    app.manager.stop.assert_not_called()


def test_async_error_surfaces_in_messagebox(app) -> None:
    app.manager.ensure_running.side_effect = RuntimeError("boom")
    app.app._select_row("ws-running")

    app.app.launch_selected()
    app.app._drain_events()

    assert app.app.root.after_callbacks
    assert app.mocks.messagebox.errors == ["boom"]


def test_raising_callback_does_not_kill_event_loop(app) -> None:
    def exploding_callback() -> None:
        raise RuntimeError("browser failed")

    app.app.events.put((exploding_callback, None))
    app.app._drain_events()

    assert app.mocks.messagebox.errors == ["browser failed"]
    assert any(delay == 100 for delay, _ in app.app.root.after_callbacks)


def test_launch_does_not_restart_when_running(app) -> None:
    app.app._select_row("ws-running")
    app.app.launch_selected()
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with(
        "ws-running", on_ready=app.app._wait_until_healthy
    )
    app.manager.start.assert_not_called()
    app.browser.assert_called_once_with("http://127.0.0.1:5678")


def test_launch_reports_health_timeout(app, gui_mocks) -> None:
    def ensure_running(workspace_id, *, on_ready=None):
        if on_ready:
            on_ready(5678)

    app.manager.ensure_running.side_effect = ensure_running
    gui_mocks.health_ok = SimpleNamespace(ok=False)
    with patch(
        "n8n_launcher.gui.app.requests.get",
        side_effect=[SimpleNamespace(ok=False)] * 5,
    ), patch(
        "n8n_launcher.gui.app.time.monotonic", side_effect=[0, 1, 2, 300, 301, 302]
    ):
        app.app._select_row("ws-stopped")
        app.app.launch_selected()
        app.app._drain_events()

    app.browser.assert_not_called()
    assert "did not become ready" in app.mocks.messagebox.errors[0]


def test_open_workflows_uses_xdg_open_on_linux(app, tmp_path) -> None:
    app.app._select_row("ws-stopped")

    with patch("n8n_launcher.gui.app.platform.system", return_value="Linux"), patch(
        "n8n_launcher.gui.app.subprocess.Popen"
    ) as popen:
        app.app.open_workflows()

    popen.assert_called_once_with(["xdg-open", str(tmp_path / "Stopped")])


def test_action_without_selection_warns_instead_of_crashing(app) -> None:
    app.app.launch_selected()
    app.app.open_workflows()

    assert app.mocks.messagebox.warnings == ["Select a workflow first"] * 2
    app.manager.ensure_running.assert_not_called()
    app.manager.stop.assert_not_called()


def test_watermark_plus_is_centered(app) -> None:
    watermark = app.app._watermark
    assert watermark._options["text"] == "+"
    assert watermark._place_options == {"relx": 0.5, "rely": 0.5, "anchor": "center"}


def test_context_menu_has_launch_folder_and_delete(app) -> None:
    labels = [label for label, _ in app.app._menu._items if label]
    assert labels == ["Ouvrir n8n", "Ouvrir le dossier", "Configurer Git…", "Supprimer"]


def test_delete_per_row_confirms_then_removes_workspace(app) -> None:
    app.mocks.messagebox._yesno = True

    row_delete_button(app, "ws-stopped").command()
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_called_once_with("ws-stopped")


def test_delete_stops_running_workspace_then_removes(app) -> None:
    app.mocks.messagebox._yesno = True

    row_delete_button(app, "ws-running").command()
    app.app._drain_events()

    app.manager.stop.assert_called_once_with("ws-running")
    app.manager.delete.assert_called_once_with("ws-running")


def test_delete_cancelled_when_user_declines(app) -> None:
    app.mocks.messagebox._yesno = False

    row_delete_button(app, "ws-running").command()
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_not_called()


def test_delete_unknown_workspace_is_noop(app) -> None:
    app.mocks.messagebox._yesno = True
    app.app.delete_workspace("ws-ghost")
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_not_called()


def test_no_auto_prompt_on_empty_list(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    root = FakeRoot()

    LauncherApp(store, manager, MagicMock(), root=root, browser_opener=MagicMock())

    assert not any(delay == 150 for delay, _ in root.after_callbacks)


def test_state_poll_runs_reconcile_and_reschedules(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    root = FakeRoot()

    LauncherApp(store, manager, MagicMock(), root=root, browser_opener=MagicMock())

    manager.reconcile_all.assert_called_once()
    assert any(delay == 5000 for delay, _ in root.after_callbacks)


def test_poll_skips_refresh_when_state_unchanged(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    manager.reconcile_all.return_value = 0
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )
    _drain_queue(launcher)

    launcher._poll_states()

    assert launcher.events.empty()
    assert manager.reconcile_all.call_count == 2


def test_poll_refreshes_only_when_state_changed(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    manager.reconcile_all.return_value = 1
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )
    _drain_queue(launcher)

    launcher._poll_states()

    callback, error = launcher.events.get_nowait()
    assert error is None
    assert launcher.events.empty()


def test_poll_skips_overlapping_reconcile(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    manager.reconcile_all.return_value = 0
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )
    _drain_queue(launcher)

    launcher._poll_in_flight = True
    calls_before = manager.reconcile_all.call_count
    launcher._poll_states()

    assert manager.reconcile_all.call_count == calls_before
    assert launcher.events.empty()


def test_empty_list_has_empty_space_click_binding(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    assert "<Button-1>" in launcher.workspace_list._bindings


def test_empty_list_subtitle_hints_create(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    assert launcher._subtitle._options["text"] == (
        "Aucun workflow — cliquez pour en créer un"
    )


def test_empty_state_hint_disappears_when_workspaces_exist(app) -> None:
    app.app.refresh()

    assert app.app._subtitle._options["text"] == "2 workflows"


def test_row_stop_button_stops_workspace(app) -> None:
    row_action_button(app, "ws-running").command()
    app.app._drain_events()

    app.manager.stop.assert_called_once_with("ws-running")
    app.manager.ensure_running.assert_not_called()


def test_row_start_button_launches_and_opens(app) -> None:
    app.mocks.messagebox._yesno = True

    row_action_button(app, "ws-stopped").command()
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with(
        "ws-stopped", on_ready=app.app._wait_until_healthy
    )
    app.browser.assert_called_once_with("http://127.0.0.1:5680")


def test_toggle_from_row_ignores_unknown_workspace(app) -> None:
    app.app.toggle_from_row("ws-ghost")
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.ensure_running.assert_not_called()


def test_toggle_from_row_blocks_while_launching(app) -> None:
    app.app._launching = "ws-stopped"
    app.app.toggle_from_row("ws-stopped")
    app.app.toggle_from_row("ws-running")
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()
    app.manager.stop.assert_not_called()


def test_handle_double_blocks_while_launching(app) -> None:
    app.app._launching = "ws-stopped"
    app.app._handle_double("ws-stopped")
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()


def test_launch_selected_blocks_while_launching(app) -> None:
    app.app._launching = "ws-stopped"
    app.app.launch_selected()
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()


def test_row_shows_launching_while_in_progress(app) -> None:
    app.app._launching = "ws-stopped"
    app.app.refresh()

    btn = row_action_button(app, "ws-stopped")
    assert btn.text == "Démarrage…"
    assert btn.command is None


def test_launch_sets_status_and_refreshes(app) -> None:
    app.app._select_row("ws-stopped")
    app.app.launch_selected()
    app.app._drain_events()

    status = app.app._status_label._options["text"]
    assert "Lancement" in status
    assert "ws-stopped" in status or "Stopped" in status


def test_launch_resets_flag_and_re_enables_button_after_success(app) -> None:
    app.app._select_row("ws-stopped")
    app.app.launch_selected()
    app.app._drain_events()

    assert app.app._launching is None
    btn = row_action_button(app, "ws-stopped")
    assert btn.text == "Démarrer"
    assert btn.command is not None