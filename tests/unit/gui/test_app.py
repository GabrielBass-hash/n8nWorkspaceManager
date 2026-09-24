"""GUI shell tests: row rendering, selection, launch, poll and delete."""

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from helpers import (
    ACTIVE_CHIP,
    INACTIVE_CHIP,
    WARN_CHIP,
    FakeRoot,
    FakeTk,
    FakeTtk,
    HoldingThread,
    _drain_queue,
    fake_runs_panel_bases,
    make_workspace,
    row_action_button,
    row_action_text,
    row_chip,
    row_chip_colors,
    row_chip_text,
    row_text,
)

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, GitConfig, ServerConfig, WorkspaceState
from n8n_launcher.core.paths import browser_app_dir
from n8n_launcher.gui import LauncherApp
from n8n_launcher.gui.app import window_size
from n8n_launcher.gui.ci_runs import RunsPanel
from n8n_launcher.gui.dialogs import GitHubTokenPlan
from n8n_launcher.gui.display import GitRowStatus
from n8n_launcher.workspaces import ci_runs


def test_window_size_scales_with_screen() -> None:
    assert window_size(7680, 2160) == (1280, 820)
    assert window_size(1920, 1080) == (1152, 778)
    assert window_size(800, 600) == (720, 460)


def test_window_size_falls_back_on_unknown_screen() -> None:
    # A 1px metric means Tk never mapped the window; keep the legacy geometry.
    assert window_size(1, 1) == (980, 600)


class ScreenRoot(FakeRoot):
    """FakeRoot plus the screen/geometry surface ``_configure_root`` needs."""

    def __init__(self):
        super().__init__()
        self._geometry_calls: list[str] = []

    def geometry(self, value: str) -> None:
        self._geometry_calls.append(value)

    def update_idletasks(self) -> None:
        pass

    def winfo_screenwidth(self) -> int:
        return 3840

    def winfo_screenheight(self) -> int:
        return 2160


def test_configure_root_sizes_window_from_screen(app) -> None:
    fake_root = ScreenRoot()
    app.app.root = fake_root
    app.app._owns_root = True

    app.app._configure_root()

    assert fake_root._geometry_calls[0] == "1280x820"
    # Centred with a slight upward bias; 0.6/0.72 fractions of 3840x2160.
    assert fake_root._geometry_calls[1] == "+1280+446"


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
    assert row_chip_text(app, "ws-gitws", "port_chip") == ":5700"
    assert row_chip_text(app, "ws-gitws", "db_chip") == "locale"
    assert row_chip_colors(app, "ws-gitws", "db_chip") == ACTIVE_CHIP
    assert row_chip_text(app, "ws-gitws", "git_chip") == "git"
    assert row_chip_colors(app, "ws-gitws", "git_chip") == ACTIVE_CHIP
    assert row_chip_text(app, "ws-gitws", "pipelines_chip") == "1"
    assert row_action_text(app, "ws-gitws") == "Arrêter"
    assert row_text(app, "ws-plain") == "Plain"
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
        (GitRowStatus(is_repo=True, dirty=True), ("git", WARN_CHIP, "•")),
        (GitRowStatus(is_repo=True, dirty=True, diverged=True), ("git <>", WARN_CHIP, "•")),
        (GitRowStatus(is_repo=True, diverged=True), ("git <>", WARN_CHIP, "")),
        (GitRowStatus(is_repo=True, push_failed=True), ("git KO", INACTIVE_CHIP, "")),
        (GitRowStatus(is_repo=True, push_failed=True, dirty=True), ("git KO", INACTIVE_CHIP, "•")),
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


def test_each_row_has_db_and_git_chips_clickable_but_no_delete(app) -> None:
    for workspace_id in ("ws-running", "ws-stopped"):
        row = app.app._rows[workspace_id][0]
        assert not hasattr(row, "delete_button")
        for chip in (row.db_chip, row.git_chip):
            assert chip._options["cursor"] == "hand2"
            assert "<Button-1>" in chip._bindings


def test_return_binding_launches_on_workspace_list(app) -> None:
    assert "<Return>" in app.app.workspace_list._bindings


def test_double_click_launches_selected(app) -> None:
    app.app._handle_double("ws-stopped")
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with(
        "ws-stopped", on_ready=app.app._wait_until_healthy
    )
    app.browser.assert_called_once_with("http://127.0.0.1:5680", browser_app_dir("ws-stopped"))


def test_repeated_launch_is_ignored_while_first_runs(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    held = make_workspace(tmp_path, "Hold", 5690)
    manager.list.return_value = [held]
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    launcher._select_row("ws-hold")
    HoldingThread.instances.clear()
    with patch("n8n_launcher.gui.app.threading.Thread", HoldingThread):
        launcher.launch_selected()
        assert launcher._launching == "ws-hold"
        launcher.launch_selected()

    assert len(HoldingThread.instances) == 1
    HoldingThread.instances[0].target()
    # The launch marker is cleared on the main thread, through the event
    # queue, never from the worker — drain it before asserting the reset.
    launcher._drain_events()
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
    app.browser.assert_called_once_with("http://127.0.0.1:5678", browser_app_dir("ws-running"))


def test_launch_reports_health_timeout(app, gui_mocks) -> None:
    def ensure_running(workspace_id, *, on_ready=None):
        if on_ready:
            on_ready(5678)

    app.manager.ensure_running.side_effect = ensure_running
    gui_mocks.health_ok = SimpleNamespace(ok=False)
    with (
        patch(
            "n8n_launcher.gui.app.requests.get",
            side_effect=[SimpleNamespace(ok=False)] * 5,
        ),
        patch("n8n_launcher.gui.app.time.monotonic", side_effect=[0, 1, 2, 300, 301, 302]),
    ):
        app.app._select_row("ws-stopped")
        app.app.launch_selected()
        app.app._drain_events()

    app.browser.assert_not_called()
    assert "did not become ready" in app.mocks.messagebox.errors[0]


def test_wait_until_healthy_waits_through_api_404_window(app, gui_mocks) -> None:
    # n8n answers /healthz with 200 long before it is usable: right after boot
    # it warm-restarts and serves 404 on every route. The wait must keep
    # polling the public API until the router is mounted again.
    api_hits = {"count": 0}

    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/healthz"):
            return SimpleNamespace(ok=True)
        api_hits["count"] += 1
        if api_hits["count"] < 3:
            return SimpleNamespace(ok=True, status_code=404)
        return SimpleNamespace(ok=True, status_code=401)

    with patch("n8n_launcher.gui.app.requests.get", side_effect=fake_get):
        app.app._wait_until_healthy(5678)

    assert api_hits["count"] >= 3


def test_wait_until_healthy_times_out_when_router_stuck_on_404(app, gui_mocks) -> None:
    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/healthz"):
            return SimpleNamespace(ok=True)
        return SimpleNamespace(ok=True, status_code=404)

    with (
        patch("n8n_launcher.gui.app.requests.get", side_effect=fake_get),
        patch("n8n_launcher.gui.app.time.monotonic", side_effect=[0, 1, 300]),
        pytest.raises(RuntimeError, match="did not become ready"),
    ):
        app.app._wait_until_healthy(5678)


def test_wait_until_healthy_accepts_mounted_router(app, gui_mocks) -> None:
    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/healthz"):
            return SimpleNamespace(ok=True)
        return SimpleNamespace(ok=True, status_code=200)

    with patch("n8n_launcher.gui.app.requests.get", side_effect=fake_get):
        app.app._wait_until_healthy(5678)


def test_open_workflows_uses_xdg_open_on_linux(app, tmp_path) -> None:
    app.app._select_row("ws-stopped")

    with (
        patch("n8n_launcher.gui.app.platform.system", return_value="Linux"),
        patch("n8n_launcher.gui.app.subprocess.Popen") as popen,
    ):
        app.app.open_workflows()

    popen.assert_called_once_with(["xdg-open", str(tmp_path / "Stopped")])


def test_action_without_selection_warns_instead_of_crashing(app) -> None:
    app.app.launch_selected()
    app.app.open_workflows()

    assert app.mocks.messagebox.warnings == ["Select a workflow first"] * 2
    app.manager.ensure_running.assert_not_called()
    app.manager.stop.assert_not_called()


def test_empty_state_shown_when_list_empty(app) -> None:
    app.manager.list.return_value = []
    app.app.refresh()

    assert app.app._empty_state._place_options == {"relx": 0.5, "rely": 0.5, "anchor": "center"}


def test_empty_state_hidden_when_rows_exist(app) -> None:
    app.app.refresh()

    assert app.app._empty_state._place_options is None


def test_create_affordance_shown_when_rows_exist(app) -> None:
    app.app.refresh()

    assert app.app._create_affordance.packed is True


def test_create_affordance_hidden_when_list_empty(app) -> None:
    app.manager.list.return_value = []
    app.app.refresh()

    assert app.app._create_affordance.packed is False


def test_create_affordance_stays_below_rows_after_refresh(app) -> None:
    app.app.refresh()
    app.app.refresh()

    assert app.app._create_affordance.packed is True
    assert app.app.workspace_list.children[-1] is app.app._create_affordance


def test_empty_state_claims_canvas_height_when_no_rows(app) -> None:
    app.manager.list.return_value = []
    app.app.refresh()
    app.app._on_canvas_resize(SimpleNamespace(width=800, height=600))

    assert app.app._list_canvas._item_kwargs == {"width": 800, "height": 600}


def test_empty_state_releases_canvas_height_when_rows_appear(app) -> None:
    app.app.refresh()
    app.app._on_canvas_resize(SimpleNamespace(width=800, height=600))

    # A height of 0 makes Tk fall back to the rows' requested height instead
    # of pinning the canvas window to the stale full-canvas value.
    assert app.app._list_canvas._item_kwargs == {"width": 800, "height": 0}


def test_context_menu_has_launch_folder_and_delete(app) -> None:
    app.app._select_row("ws-stopped")
    app.app._show_context_menu(SimpleNamespace(x_root=0, y_root=0))
    labels = [label for label, _ in app.app._menu._items if label]
    assert labels == [
        "Ouvrir n8n",
        "Ouvrir le dossier",
        "Configurer Git…",
        "Configurer les tests GitHub Actions…",
        "Gérer les credentials CI…",
        "Ouvrir les Actions GitHub…",
        "Désactiver les tests CI",
        "Configurer le token GitHub…",
        "Configurer le serveur…",
        "Supprimer",
    ]


def test_context_menu_hides_server_actions_when_disabled(app) -> None:
    app.app._select_row("ws-stopped")
    app.app._show_context_menu(SimpleNamespace(x_root=0, y_root=0))
    labels = [label for label, _ in app.app._menu._items if label]
    assert "Publier sur le serveur…" not in labels
    assert "Désactiver le serveur" not in labels


def test_context_menu_shows_server_actions_when_enabled(app) -> None:
    stopped = next(w for w in app.manager.list() if w.id == "ws-stopped")
    stopped.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.app._select_row("ws-stopped")
    app.app._show_context_menu(SimpleNamespace(x_root=0, y_root=0))
    labels = [label for label, _ in app.app._menu._items if label]
    assert labels.index("Publier sur le serveur…") < labels.index("Désactiver le serveur")
    assert labels.index("Désactiver le serveur") < labels.index("Configurer Git…")


def test_publish_selected_deploys_when_server_enabled(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    running.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.app._select_row("ws-running")
    app.app.publish_selected()
    app.app._drain_events()
    app.manager.publish.assert_called_once_with(running)


def test_publish_selected_skipped_when_server_disabled(app) -> None:
    app.app._select_row("ws-running")
    app.app.publish_selected()
    app.manager.publish.assert_not_called()


def test_disable_server_selected_confirms_and_disables(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    running.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.mocks.messagebox._yesno = True
    app.app._select_row("ws-running")
    app.app.disable_server_selected()
    app.app._drain_events()
    app.manager.disable_server.assert_called_once_with(running)


def test_disable_server_selected_informs_when_not_configured(app) -> None:
    app.app._select_row("ws-running")
    app.app.disable_server_selected()
    assert app.mocks.messagebox.infos


def test_configure_server_selected_saves_and_installs(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    config = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    with patch("n8n_launcher.gui.app.prompt_server_config", return_value=config):
        app.app._select_row("ws-running")
        app.app.configure_server_selected()
    app.app._drain_events()

    app.manager.install_server.assert_called_once_with(running, config)
    app.manager.update.assert_called_once_with("ws-running", server=config)


def test_configure_server_selected_cancel_does_nothing(app) -> None:
    with patch("n8n_launcher.gui.app.prompt_server_config", return_value=None):
        app.app._select_row("ws-running")
        app.app.configure_server_selected()
    app.app._drain_events()

    app.manager.install_server.assert_not_called()
    app.manager.update.assert_not_called()


def test_delete_per_row_confirms_then_removes_workspace(app) -> None:
    app.mocks.messagebox._yesno = True

    app.app.delete_workspace("ws-stopped")
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_called_once_with("ws-stopped")


def test_delete_stops_running_workspace_then_removes(app) -> None:
    app.mocks.messagebox._yesno = True

    app.app.delete_workspace("ws-running")
    app.app._drain_events()

    app.manager.stop.assert_called_once_with("ws-running")
    app.manager.delete.assert_called_once_with("ws-running")


def test_delete_cancelled_when_user_declines(app) -> None:
    app.mocks.messagebox._yesno = False

    app.app.delete_workspace("ws-running")
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_not_called()


def test_delete_unknown_workspace_is_noop(app) -> None:
    app.mocks.messagebox._yesno = True
    app.app.delete_workspace("ws-ghost")
    app.app._drain_events()

    app.manager.stop.assert_not_called()
    app.manager.delete.assert_not_called()


def test_git_chip_click_selects_row_and_opens_git_config(app) -> None:
    with patch("n8n_launcher.gui.app.prompt_git_remote", return_value=None) as remote:
        app.app._rows["ws-stopped"][0].git_chip._bindings["<Button-1>"](None)

    assert app.app._selected_id == "ws-stopped"
    remote.assert_called_once()


def test_db_chip_click_selects_row_and_opens_db_config(app) -> None:
    with patch("n8n_launcher.gui.app.prompt_db_config", return_value=None) as dbc:
        app.app._rows["ws-stopped"][0].db_chip._bindings["<Button-1>"](None)

    assert app.app._selected_id == "ws-stopped"
    dbc.assert_called_once()


def test_configure_db_applies_manager_change(app) -> None:
    from n8n_launcher.core.models import DbConfig, DbMode

    app.app._select_row("ws-stopped")
    db_config = DbConfig(DbMode.MANAGED, database_name="data")

    with patch("n8n_launcher.gui.app.prompt_db_config", return_value=db_config):
        app.app.configure_db_selected()
    app.app._drain_events()

    app.manager.configure_db.assert_called_once()
    assert "Base de données configurée" in app.app._status_label._options["text"]


def test_no_auto_prompt_on_empty_list(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    root = FakeRoot()

    LauncherApp(store, manager, MagicMock(), root=root, browser_opener=MagicMock())

    assert not any(delay == 150 for delay, _ in root.after_callbacks)


def test_create_workflow_guards_reentrant_click(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    launcher = LauncherApp(
        store, MagicMock(), MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )

    calls = 0

    def source(root) -> None:
        nonlocal calls
        calls += 1
        launcher.prompt_create_workflow()
        return None

    with patch("n8n_launcher.gui.app.prompt_create_source", side_effect=source):
        launcher.prompt_create_workflow()

    assert calls == 1


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
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
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
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    _drain_queue(launcher)

    launcher._poll_states()

    _callback, error = launcher.events.get_nowait()
    assert error is None
    assert launcher.events.empty()


def test_poll_skips_overlapping_reconcile(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    manager.reconcile_all.return_value = 0
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
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

    assert launcher._subtitle._options["text"] == ("Aucun workflow — cliquez pour en créer un")


def test_empty_state_hint_disappears_when_workspaces_exist(app) -> None:
    app.app.refresh()

    assert app.app._subtitle._options["text"] == "2 workflows"


def test_row_stop_button_syncs_then_stops_workspace(app) -> None:
    row_action_button(app, "ws-running").command()
    app.app._drain_events()

    app.manager.stop_with_sync.assert_called_once_with("ws-running")
    app.manager.ensure_running.assert_not_called()


def test_row_stop_warns_when_push_failed_during_sync(app) -> None:
    running = app.manager.list.return_value[0]

    def fail_sync(_workspace_id):
        running.git_push_failed = True
        return None

    app.manager.stop_with_sync.side_effect = fail_sync
    row_action_button(app, "ws-running").command()
    app.app._drain_events()

    assert app.mocks.messagebox.warnings, "a push-failure warning should be shown"
    assert "push" in app.mocks.messagebox.warnings[0].lower()


def test_row_start_button_launches_and_opens(app) -> None:
    app.mocks.messagebox._yesno = True

    row_action_button(app, "ws-stopped").command()
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with(
        "ws-stopped", on_ready=app.app._wait_until_healthy
    )
    app.browser.assert_called_once_with("http://127.0.0.1:5680", browser_app_dir("ws-stopped"))


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


def test_stopped_row_uses_accent_start_button(app) -> None:
    app.app.refresh()
    btn = row_action_button(app, "ws-stopped")
    assert btn.text == "Démarrer"
    assert btn.style == "Accent.TButton"


def test_running_row_uses_neutral_stop_button(app) -> None:
    app.app.refresh()
    btn = row_action_button(app, "ws-running")
    assert btn.text == "Arrêter"
    assert btn.style == "Secondary.TButton"


def test_list_scrolls_on_mousewheel(app) -> None:
    app.app._on_mousewheel(SimpleNamespace(delta=120))

    assert app.app._list_canvas.scrolled == 1


def test_list_scrolls_on_linux_wheel(app) -> None:
    app.app._on_wheel_linux(SimpleNamespace(num=4))
    assert app.app._list_canvas.scrolled == 1

    app.app._on_wheel_linux(SimpleNamespace(num=5))
    assert app.app._list_canvas.scrolled == 2


def test_canvas_wheel_bindings_cover_canvas_and_list(app) -> None:
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        assert sequence in app.app._list_canvas._bindings
        assert sequence in app.app.workspace_list._bindings


def test_scrollregion_is_refreshed_after_row_rebuild(app) -> None:
    assert app.app._list_canvas._options.get("scrollregion") is not None


def _stopped(app):
    return app.manager.list.return_value[1]


def test_ci_chip_palette_tracks_config_and_selection(app, tmp_path) -> None:
    from n8n_launcher.gui.theme import CHIP_ACTIVE, CHIP_NEUTRAL, CHIP_WARN

    stopped = _stopped(app)
    (tmp_path / "Stopped" / "n8nPipelines").mkdir(parents=True)
    (tmp_path / "Stopped" / "n8nPipelines" / "manual.json").write_text(
        '{"name": "M", "nodes": [{"name": "Bouton", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}], "connections": {}, "settings": {}}',
        encoding="utf-8",
    )
    app.app.refresh()
    assert row_chip_colors(app, "ws-stopped", "ci_chip") == CHIP_NEUTRAL

    stopped.git = GitConfig(ci_enabled=True)  # enabled but nothing selected
    app.app.refresh()
    assert row_chip_colors(app, "ws-stopped", "ci_chip") == CHIP_WARN

    selection = tmp_path / "Stopped" / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"selected": ["n8nPipelines/manual.json"]}', encoding="utf-8")
    app.app.refresh()
    assert row_chip_colors(app, "ws-stopped", "ci_chip") == CHIP_ACTIVE


def test_configure_ci_enables_then_persists_selection(app) -> None:
    stopped = _stopped(app)
    app.app._select_row(stopped.id)

    with patch(
        "n8n_launcher.gui.app.ci_edit.prompt_ci_workflows",
        return_value=({"n8nPipelines/a.json"}, True),
    ) as prompt:
        app.app.configure_ci_selected()
        app.app._drain_events()

    app.manager.enable_ci.assert_called_once_with(stopped)
    prompt.assert_called_once()
    app.manager.save_ci_selection.assert_called_once_with(
        stopped, {"n8nPipelines/a.json"}, push=True
    )


def test_configure_ci_opens_dialog_directly_when_already_enabled(app) -> None:
    stopped = _stopped(app)
    stopped.git = GitConfig(ci_enabled=True)
    app.app._select_row(stopped.id)

    with patch("n8n_launcher.gui.app.ci_edit.prompt_ci_workflows", return_value=None) as prompt:
        app.app.configure_ci_selected()
        app.app._drain_events()

    app.manager.enable_ci.assert_not_called()
    prompt.assert_called_once()
    app.manager.save_ci_selection.assert_not_called()


def test_disable_ci_selected_requires_confirmation(app) -> None:
    stopped = _stopped(app)
    stopped.git = GitConfig(ci_enabled=True)
    app.app._select_row(stopped.id)

    app.mocks.messagebox._yesno = False
    app.app.disable_ci_selected()
    app.manager.disable_ci.assert_not_called()

    app.mocks.messagebox._yesno = True
    app.app.disable_ci_selected()
    app.app._drain_events()

    app.manager.disable_ci.assert_called_once_with(stopped)


def test_open_ci_actions_opens_github_actions_page(app) -> None:
    stopped = _stopped(app)
    app.manager.git_remote_url.return_value = "https://github.com/owner/repo.git"
    app.app._select_row(stopped.id)

    with patch("n8n_launcher.gui.app.open_url") as open_url:
        app.app.open_ci_actions()

    open_url.assert_called_once_with("https://github.com/owner/repo/actions")


def test_open_ci_actions_warns_without_github_remote(app) -> None:
    stopped = _stopped(app)
    app.manager.git_remote_url.return_value = "https://gitlab.com/owner/repo.git"
    app.app._select_row(stopped.id)
    app.app._drain_events()

    app.app.open_ci_actions()

    assert "Aucun dépôt distant GitHub" in app.mocks.messagebox.warnings[0]


# --- GitHub token resolution (resolved silently, prompted only as fallback) --


def test_ensure_ci_token_resolves_and_caches_without_prompting(app) -> None:
    app.manager.github_token.return_value = None

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value="ghp_auto") as resolve,
        patch("n8n_launcher.gui.app.prompt_github_token") as prompt,
    ):
        assert app.app._ensure_ci_token() == "ghp_auto"
        assert app.app._ensure_ci_token() == "ghp_auto"

    resolve.assert_called_once_with(None)
    prompt.assert_not_called()


def test_ensure_ci_token_returns_none_when_unresolved(app) -> None:
    app.manager.github_token.return_value = None

    with patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value=None):
        assert app.app._ensure_ci_token() is None


def test_refresh_ci_runs_prompts_as_last_resort_and_remembers(app) -> None:
    stopped = _stopped(app)
    app.manager.github_token.return_value = None
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value=None),
        patch(
            "n8n_launcher.gui.app.prompt_github_token",
            return_value=GitHubTokenPlan(token="ghp_typed", remember=True),
        ) as prompt,
        patch.object(app.app, "_build_ci_runs_snapshot", return_value="snapshot"),
    ):
        app.app._refresh_ci_runs(stopped, panel)

    prompt.assert_called_once()
    app.manager.set_github_token.assert_called_once_with("ghp_typed")
    assert app.app._ci_token == "ghp_typed"


def test_refresh_ci_runs_does_not_reprompt_after_cancel(app) -> None:
    stopped = _stopped(app)
    app.manager.github_token.return_value = None
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value=None),
        patch("n8n_launcher.gui.app.prompt_github_token", return_value=None) as prompt,
    ):
        app.app._refresh_ci_runs(stopped, panel)
        app.app._refresh_ci_runs(stopped, panel)

    prompt.assert_called_once()


def _runs_snapshot() -> ci_runs.RunsSnapshot:
    run = ci_runs.RunSummary(
        id=11,
        run_number=11,
        branch="main",
        head_sha="a" * 40,
        status="completed",
        conclusion="success",
        created_at="2026-01-01T00:00:00Z",
        url="https://github.com/octo/repo/actions/runs/11",
    )
    return ci_runs.RunsSnapshot(repo_path="octo/repo", runs=(run,))


@contextlib.contextmanager
def _runs_panel(parent):
    """Build a RunsPanel against the fake Tk layer (no real interpreter)."""
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        yield RunsPanel(parent, refresh=lambda: None, open_run=lambda _r: None)


def test_apply_ci_runs_snapshot_caches_and_renders_on_live_panel(app) -> None:
    workspace = app.manager.list.return_value[1]
    snapshot = _runs_snapshot()

    dialog = FakeTk.Toplevel(None)
    with _runs_panel(dialog) as panel:
        with patch.object(panel, "apply") as spy:
            app.app._apply_ci_runs_snapshot(workspace.id, panel, snapshot)

        spy.assert_called_once_with(snapshot)
        assert app.app._ci_runs_cache[workspace.id] is snapshot


def test_apply_ci_runs_snapshot_skips_rendering_on_destroyed_panel(app) -> None:
    # Reproduction of the TclError "invalid command name …toplevel…" crash: the
    # runs fetch runs on a background thread that can outlive the CI dialog it
    # belonged to. A snapshot landing after the dialog's destruction must be
    # cached (so the next open shows it), but never rendered on dead widgets.
    workspace = app.manager.list.return_value[1]
    snapshot = _runs_snapshot()

    dialog = FakeTk.Toplevel(None)
    with _runs_panel(dialog) as panel:
        panel.destroy()
        with patch.object(panel, "apply") as spy:
            app.app._apply_ci_runs_snapshot(workspace.id, panel, snapshot)

        spy.assert_not_called()
        assert app.app._ci_runs_cache[workspace.id] is snapshot


def test_tooltip_leave_recovers_from_tip_destroyed_under_cursor(app) -> None:
    # The tooltip owns no lifecycle of its own: a dialog closing while the
    # cursor is still over a chip leaves a stale ``tip`` whose destroy raises
    # ``invalid command name`` on the next leave event. The handler must clear
    # the reference silently so a later Enter rebuilds the tooltip.
    widget = app.mocks.tk.Label(None)
    app.app._attach_tooltip(widget, "hint")
    enter = widget._bindings["<Enter>"]
    leave = widget._bindings["<Leave>"]

    FakeTk.Toplevel.instances.clear()
    enter(None)
    tip = FakeTk.Toplevel.instances[0]

    with patch.object(tip, "destroy", side_effect=RuntimeError("invalid command name")):
        leave(None)  # must not raise

    FakeTk.Toplevel.instances.clear()
    enter(None)
    assert len(FakeTk.Toplevel.instances) == 1


def test_configure_github_token_updates_and_persists_when_ticked(app) -> None:
    with patch(
        "n8n_launcher.gui.app.prompt_github_token",
        return_value=GitHubTokenPlan(token="ghp_new", remember=True),
    ):
        app.app.configure_github_token()

    assert app.app._ci_token == "ghp_new"
    app.manager.set_github_token.assert_called_once_with("ghp_new")
    assert "mis à jour" in app.app._status_label._options["text"]


def test_configure_github_token_keeps_in_memory_when_not_ticked(app) -> None:
    with patch(
        "n8n_launcher.gui.app.prompt_github_token",
        return_value=GitHubTokenPlan(token="ghp_new", remember=False),
    ):
        app.app.configure_github_token()

    assert app.app._ci_token == "ghp_new"
    app.manager.set_github_token.assert_not_called()


def test_prompt_github_and_configure_prefills_resolved_token(app) -> None:
    stopped = _stopped(app)

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value="ghp_auto"),
        patch("n8n_launcher.gui.app.prompt_github_create", return_value=None) as prompt,
    ):
        app.app._prompt_github_and_configure(stopped)

    prompt.assert_called_once_with(app.app.root, stopped.name, token="ghp_auto")
