"""GUI shell tests: row rendering, selection, launch, poll and delete."""

import contextlib
import time
from contextlib import contextmanager
from dataclasses import replace
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
    fake_monitoring_panel_bases,
    fake_runs_panel_bases,
    fake_server_panel_bases,
    make_workspace,
    row_action_button,
    row_action_text,
    row_chip,
    row_chip_colors,
    row_chip_pack,
    row_chip_text,
    row_full_name,
    row_label,
    row_text,
)

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, GitConfig, ServerConfig, WorkspaceState
from n8n_launcher.core.paths import browser_app_dir
from n8n_launcher.gui import LauncherApp, board, pages, server_page
from n8n_launcher.gui.app import (
    _CHIP_GAP,
    _CHIP_PADX,
    _FLOAT_MARGIN,
    _ROW_NAME_MINIMUM,
    BOARD_FLOOR,
    MIN_WINDOW_WIDTH,
    SERVER_SNAPSHOT_TTL_SECONDS,
    _api_router_mounted,
    _ChipOverflow,
    clamp_to_screen,
    window_minsize,
    window_size,
)
from n8n_launcher.gui.ci_runs import RunsPanel
from n8n_launcher.gui.dialogs import GitHubTokenPlan
from n8n_launcher.gui.layout import ELLIPSIS
from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.store import EventStore
from n8n_launcher.remote import RemoteExecution, RemoteExecutionStatus, RemoteHealth
from n8n_launcher.workspaces import ci_runs, display
from n8n_launcher.workspaces.display import GitRowStatus


def test_window_size_scales_with_screen() -> None:
    # The two tables side by side need ~1000px, so a wide screen gets 72% of it
    # up to the 1600px ceiling rather than 60% of it.
    assert window_size(7680, 2160) == (1600, 900)
    assert window_size(1920, 1080) == (1382, 778)
    assert window_size(2560, 1440) == (1600, 900)


def test_window_size_never_exceeds_a_small_screen() -> None:
    # The 760px floor is a floor for a large screen only: an 800px-wide display
    # still gets a window that fits on it, decorations included.
    assert window_size(800, 600) == (760, 440)
    assert window_size(600, 400) == (600, 400)


def test_window_size_falls_back_on_unknown_screen() -> None:
    # A 1px metric means Tk never mapped the window; keep the default geometry.
    assert window_size(1, 1) == (1000, 600)


def test_window_minsize_is_the_dashboard_floor() -> None:
    # Below this the list, the dock, the journal and the panes' own chrome no
    # longer fit together, so the window stops shrinking. The floor is *derived*
    # from the board's breakpoints: a breakpoint edited on one side would
    # otherwise silently make the floor impossible, and the symptom would be a
    # column that can only ever be cut.
    assert BOARD_FLOOR == (
        board.LIST_MINIMUM + board.DOCK_MINIMUM + board.JOURNAL_RAIL + 2 * board.PANE_CHROME
    )
    assert MIN_WINDOW_WIDTH >= BOARD_FLOOR
    assert window_minsize(3840, 2160) == (760, 440)
    # A screen narrower than the floor gets its own width back.
    assert window_minsize(1024, 768) == (760, 440)
    assert window_minsize(800, 600) == (760, 440)
    assert window_minsize(700, 600) == (700, 440)
    assert window_minsize(1, 1) == (760, 440)


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

    assert fake_root._geometry_calls[0] == "1600x900"
    # Centred with a slight upward bias; 0.72 fractions of 3840x2160, capped.
    assert fake_root._geometry_calls[1] == "+1120+420"


def _boarded_app(app, *, width: int = 1400):
    """Give the launcher's dashboard a room of *width* pixels and reflow it."""
    board_ = app.app._board
    assert board_ is not None
    board_.pane._width = width
    board_.reflow()
    return board_


def test_the_main_window_is_a_dashboard_not_a_notebook(app) -> None:
    # One paned window, three columns, no tab bar: the list, the dock of open
    # pages and the journal are read together.
    the_board = app.app._board
    assert the_board is not None
    assert the_board.pane.packed
    assert list(the_board.pane.panes()) == [the_board.list_card, the_board.journal_card]
    assert not FakeTtk.Notebook.instances


def test_a_wide_window_gives_the_journal_its_columns(app) -> None:
    # A pane is never given its request by Tk, so the board drives the split
    # itself: the journal gets its floor and the list — which can ellipsize a
    # name — takes the rest.
    the_board = _boarded_app(app, width=1400)
    assert the_board.pane.panes()[0] is the_board.list_card
    assert the_board.pane.pane_width_of(the_board.journal_card) >= board.JOURNAL_MINIMUM - 2


def test_a_narrow_window_collapses_the_journal_to_its_rail(app) -> None:
    # Nothing is ever clipped: the list keeps its floor, and when the room runs
    # out the journal becomes a rail — one control to bring it back.
    the_board = _boarded_app(app, width=560)
    assert the_board.journal_rail is True
    assert the_board.journal_pane_width() == board.JOURNAL_RAIL
    # The last pane is the room the split left, which the plan holds one handle's
    # worth above, so the rail is measured a chrome wider than its budget.
    assert the_board.pane.pane_width_of(the_board.journal_card) <= (
        board.JOURNAL_RAIL + board.PANE_CHROME
    )
    assert app.app._monitor_panel is not None


def test_the_window_is_never_resized_behind_the_users_back(app) -> None:
    # The board collapses columns instead of growing the window: a resize the
    # user made is the window they want, and the sash is theirs to move.
    root = ScreenRoot()
    app.app.root = root
    app.app._owns_root = True
    _boarded_app(app, width=560)
    assert root._geometry_calls == []


def test_the_journal_rail_is_announced_to_the_panel(app) -> None:
    _boarded_app(app, width=560)
    panel = app.app._monitor_panel
    assert panel is not None
    assert panel.rail is True


def test_a_wide_window_keeps_the_journal_open(app) -> None:
    _boarded_app(app, width=1400)
    panel = app.app._monitor_panel
    assert panel is not None
    assert panel.rail is False


def test_a_resize_is_debounced_into_one_reflow(app) -> None:
    the_board = app.app._board
    assert the_board is not None
    the_board.pane.fire_configure(1200)
    the_board.pane.fire_configure(900)
    assert len(the_board.pane.after_callbacks) == 1
    the_board.pane.run_after()
    assert the_board.widths["list"] == the_board._plan(900).list


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

    with patch("n8n_launcher.workspaces.display.git_row_status", side_effect=pick_status):
        app.app.refresh()

    assert row_text(app, "ws-gitws") == "GitWs"
    assert row_chip_text(app, "ws-gitws", "db_chip") == "locale"
    assert row_chip_colors(app, "ws-gitws", "db_chip") == ACTIVE_CHIP
    assert row_chip_text(app, "ws-gitws", "git_chip") == "git"
    assert row_chip_colors(app, "ws-gitws", "git_chip") == ACTIVE_CHIP
    assert row_action_text(app, "ws-gitws") == "Arrêter"
    assert row_text(app, "ws-plain") == "Plain"
    assert row_chip_text(app, "ws-plain", "db_chip") == "locale"
    assert row_chip_colors(app, "ws-plain", "db_chip") == INACTIVE_CHIP
    assert row_chip_colors(app, "ws-plain", "git_chip") == INACTIVE_CHIP
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
        with patch("n8n_launcher.workspaces.display.git_row_status", return_value=status):
            app.app.refresh()
        assert row_chip_text(app, "ws-g", "git_chip") == expected_label, status
        assert row_chip_colors(app, "ws-g", "git_chip") == expected_palette, status
        dot = row_chip(app, "ws-g", "dirty_dot")
        assert dot._options["text"] == expected_dot, status


def test_refresh_updates_rows_in_place_when_order_unchanged(app) -> None:
    rows_before = {
        wid: (frame, frame.git_chip, frame.server_chip)
        for wid, (frame, _label) in app.app._rows.items()
    }
    status = GitRowStatus(is_repo=True, diverged=True)
    with patch("n8n_launcher.workspaces.display.git_row_status", return_value=status):
        app.app.refresh()

    # Same workspace set + order: nothing is destroyed, widgets are patched.
    # ``server_chip`` is wired on the row frame, so the in-place refresh must
    # reach it without raising (regression: it was never assigned in _build_row).
    for wid, (frame, git_chip, server_chip) in rows_before.items():
        assert app.app._rows[wid][0] is frame
        assert not frame.destroyed
        assert app.app._rows[wid][0].git_chip is git_chip
        assert row_chip_text(app, wid, "git_chip") == "git <>"
        assert row_chip_colors(app, wid, "git_chip") == WARN_CHIP
        assert app.app._rows[wid][0].server_chip is server_chip
        assert row_chip_text(app, wid, "server_chip") == "serv"


def test_refresh_rebuilds_when_order_changes(app) -> None:
    old_running_frame = app.app._rows["ws-running"][0]
    reversed_workspaces = list(reversed(app.manager.list.return_value))
    app.manager.list.return_value = reversed_workspaces

    app.app.refresh()

    assert old_running_frame.destroyed
    assert app.app._rows["ws-running"][0] is not old_running_frame
    assert app.app._row_order == ["ws-stopped", "ws-running"]


def test_refresh_drops_removed_workspace(app) -> None:
    running = app.manager.list.return_value[0]
    app.manager.list.return_value = [running]

    app.app.refresh()

    assert app.app._row_order == ["ws-running"]
    assert "ws-stopped" not in app.app._rows
    assert "ws-stopped" not in app.app._row_order


def test_row_chips_are_packed_tightly(app) -> None:
    # The name is the elastic field of the row, so the chips around it take as
    # little room as they can: one 4px gap between them, 6px of inner padding.
    for attr in ("overflow_chip", "db_chip", "git_chip", "ci_chip", "server_chip"):
        options = row_chip_pack(app, "ws-running", attr)
        assert options["side"] == "right"
        # One 4px gap between the chips, and 6px of padding inside each of them.
        assert options["padx"] == (_CHIP_GAP, 0)
        assert _CHIP_GAP == 4
        chip = row_chip(app, "ws-running", attr)
        assert chip._options["padx"] == _CHIP_PADX
        assert _CHIP_PADX == 6
        assert chip._options["pady"] == 2
    # The dirty dot and the action button share the same gap on the left.
    assert row_chip_pack(app, "ws-running", "action_button")["padx"] == (_CHIP_GAP + 2, 0)


def test_row_name_keeps_its_full_text_in_a_wide_row(app) -> None:
    # Plenty of room: the label shows the name as it was typed.
    label = row_label(app, "ws-running")
    label._width = 400
    label._options["text"] = "Running"

    app.app._fit_row_name("ws-running")

    assert row_text(app, "ws-running") == "Running"
    assert row_full_name(app, "ws-running") == "Running"


def test_row_name_is_cut_to_the_width_the_chips_leave(app) -> None:
    # Not enough room for the whole name: the label is re-cut with an ellipsis
    # rather than clipped, and the untouched name stays available for the tooltip.
    label = row_label(app, "ws-running")
    label._width = 30
    label._options["text"] = "Running"

    app.app._fit_row_name("ws-running")

    text = row_text(app, "ws-running")
    assert text.endswith(ELLIPSIS)
    assert text != "Running"
    assert row_full_name(app, "ws-running") == "Running"


def test_row_name_follows_a_resize_in_both_directions(app) -> None:
    # The <Configure> binding is what makes the name follow the list width.
    label = row_label(app, "ws-running")
    label._width = 30
    label._bindings["<Configure>"](SimpleNamespace(width=30))
    narrow = row_text(app, "ws-running")
    assert narrow.endswith(ELLIPSIS)

    label._width = 400
    label._bindings["<Configure>"](SimpleNamespace(width=400))

    assert row_text(app, "ws-running") == "Running"


def test_row_name_survives_a_refresh_before_it_was_ever_mapped(app) -> None:
    # A row is stored in ``app._rows`` only after it is built, so the name must be
    # seeded by the build itself: the first refresh and the first <Configure> would
    # otherwise read an attribute that was never set.
    label = row_label(app, "ws-running")
    label._width = 30
    app.app._fit_row_name("ws-running", workspace=app.manager.list.return_value[0])

    with patch("n8n_launcher.workspaces.display.git_row_status", return_value=GitRowStatus()):
        app.app.refresh()

    assert row_full_name(app, "ws-running") == "Running"
    assert row_text(app, "ws-running").endswith(ELLIPSIS)


def test_row_name_tooltip_shows_the_untouched_name(app) -> None:
    # Hovering the row shows the full name, so a cut name is never a dead end.
    label = row_label(app, "ws-running")
    assert "<Enter>" in label._bindings
    assert "<Leave>" in label._bindings

    label._width = 30
    app.app._fit_row_name("ws-running")
    assert row_text(app, "ws-running").endswith(ELLIPSIS)
    assert "Running" in row_full_name(app, "ws-running")


def test_row_name_of_an_unknown_workspace_is_ignored(app) -> None:
    # A <Configure> can arrive for a row that was just removed: no crash.
    app.app._fit_row_name("ws-gone")

    assert "ws-gone" not in app.app._rows


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
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    held = make_workspace(tmp_path, "Hold", 5690)
    manager.list.return_value = [held]
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    launcher._select_row("ws-hold")
    HoldingThread.instances.clear()
    with patch("n8n_launcher.gui.app.threading.Thread", HoldingThread):
        launcher.launch_selected()
        assert launcher._launching == {"ws-hold"}
        launcher.launch_selected()

    assert len(HoldingThread.instances) == 1
    HoldingThread.instances[0].target()
    # The launch marker is cleared on the main thread, through the event
    # queue, never from the worker — drain it before asserting the reset.
    launcher._drain_events()
    assert launcher._launching == set()
    manager.ensure_running.assert_called_once_with("ws-hold", on_ready=launcher._wait_until_healthy)


def test_other_workspace_launches_while_one_is_in_flight(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    first = make_workspace(tmp_path, "A", 5671)
    first.state = WorkspaceState.STOPPED
    second = make_workspace(tmp_path, "B", 5672)
    second.state = WorkspaceState.STOPPED
    manager.list.return_value = [first, second]
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    launcher._select_row("ws-a")
    launcher.launch_selected()
    assert launcher._launching == {"ws-a"}
    launcher._select_row("ws-b")
    launcher.launch_selected()
    launcher._drain_events()

    assert manager.ensure_running.call_count == 2
    assert launcher._launching == set()


def test_double_click_launches_other_workspace_while_one_in_flight(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    held = make_workspace(tmp_path, "Hold", 5690)
    other = make_workspace(tmp_path, "Other", 5691)
    other.state = WorkspaceState.STOPPED
    manager.list.return_value = [held, other]
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    launcher._launching = {"ws-hold"}

    # While "Hold" is booting, double-clicking another stopped workspace must
    # still start it — only a second click on the same row is swallowed.
    launcher._handle_double("ws-other")
    launcher._drain_events()

    manager.ensure_running.assert_called_once_with(
        "ws-other", on_ready=launcher._wait_until_healthy
    )


def test_start_slot_released_even_when_boot_fails(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    held = make_workspace(tmp_path, "Hold", 5690)
    manager.list.return_value = [held]
    sem = MagicMock()
    with patch("n8n_launcher.gui.app.threading.BoundedSemaphore", return_value=sem):
        launcher = LauncherApp(
            store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
        )

    launcher._select_row("ws-hold")
    launcher.launch_selected()
    launcher._drain_events()
    assert sem.acquire.call_count == 1
    assert sem.release.call_count == 1
    assert manager.ensure_running.call_count == 1

    # A boot failure must still free the slot, or the app would permanently
    # starve the pool (a broken workspace would block every later start).
    manager.ensure_running.side_effect = RuntimeError("boom")
    launcher.launch_selected()
    launcher._drain_events()
    assert sem.acquire.call_count == 2
    assert sem.release.call_count == 2
    assert gui_mocks.messagebox.errors == ["boom"]


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
    assert "n'est pas devenu prêt" in app.mocks.messagebox.errors[0]


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
        pytest.raises(RuntimeError, match="n'est pas devenu prêt"),
    ):
        app.app._wait_until_healthy(5678)


def test_wait_until_healthy_accepts_mounted_router(app, gui_mocks) -> None:
    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/healthz"):
            return SimpleNamespace(ok=True)
        return SimpleNamespace(ok=True, status_code=200)

    with patch("n8n_launcher.gui.app.requests.get", side_effect=fake_get):
        app.app._wait_until_healthy(5678)


def test_api_router_mounted_accepts_403_and_ok_fake() -> None:
    assert _api_router_mounted(SimpleNamespace(ok=True, status_code=403)) is True
    assert _api_router_mounted(SimpleNamespace(ok=True)) is True
    assert _api_router_mounted(SimpleNamespace(ok=True, status_code=404)) is False


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

    assert app.mocks.messagebox.warnings == ["Sélectionnez d'abord un workspace"] * 2
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


def test_publish_selected_ignored_while_deploy_in_flight(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    running.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.app._select_row("ws-running")
    app.app._deploying = True
    app.app.publish_selected()
    app.app._drain_events()
    app.manager.publish.assert_not_called()


def test_publish_selected_releases_guard_after_success(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    running.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.app._select_row("ws-running")
    app.app.publish_selected()
    assert app.app._deploying is True  # in flight while the worker runs
    app.app._drain_events()
    assert app.app._deploying is False
    app.manager.publish.assert_called_once_with(running)


def test_publish_selected_releases_guard_after_error(app) -> None:
    running = next(w for w in app.manager.list() if w.id == "ws-running")
    running.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    app.manager.publish.side_effect = RuntimeError("boom")
    app.app._select_row("ws-running")
    app.app.publish_selected()
    app.app._drain_events()
    assert app.app._deploying is False
    assert "boom" in "\n".join(app.mocks.messagebox.errors)


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


def test_ci_chip_click_selects_row_and_opens_ci_config(app) -> None:
    with patch.object(app.app, "configure_ci_selected") as configure:
        app.app._rows["ws-stopped"][0].ci_chip._bindings["<Button-1>"](None)

    assert app.app._selected_id == "ws-stopped"
    configure.assert_called_once_with()


def test_ci_chip_click_is_ignored_while_closing(app) -> None:
    app.app._select_row("ws-running")
    app.app._closing = True

    with patch.object(app.app, "configure_ci_selected") as configure:
        app.app._on_ci_chip_click("ws-stopped")

    assert app.app._selected_id == "ws-running"
    configure.assert_not_called()


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
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    root = FakeRoot()

    LauncherApp(store, manager, MagicMock(), root=root, browser_opener=MagicMock())

    assert not any(delay == 150 for delay, _ in root.after_callbacks)


def test_create_workflow_guards_reentrant_click(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
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
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    root = FakeRoot()

    LauncherApp(store, manager, MagicMock(), root=root, browser_opener=MagicMock())

    manager.reconcile_all.assert_called_once()
    assert any(delay == 5000 for delay, _ in root.after_callbacks)


def test_poll_skips_refresh_when_state_unchanged(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
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
    store = ConfigStore(tmp_path / "launcher.db")
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
    store = ConfigStore(tmp_path / "launcher.db")
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
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    assert "<Button-1>" in launcher.workspace_list._bindings


def test_empty_list_subtitle_hints_create(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
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
    app.app._launching = {"ws-stopped"}
    app.app.toggle_from_row("ws-stopped")
    app.app.toggle_from_row("ws-running")
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()
    app.manager.stop.assert_not_called()


def test_handle_double_blocks_while_launching(app) -> None:
    app.app._launching = {"ws-stopped"}
    app.app._handle_double("ws-stopped")
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()


def test_launch_selected_blocks_while_launching(app) -> None:
    app.app._launching = {"ws-stopped"}
    app.app.launch_selected()
    app.app._drain_events()

    app.manager.ensure_running.assert_not_called()


def test_row_shows_launching_while_in_progress(app) -> None:
    app.app._launching = {"ws-stopped"}
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

    assert app.app._launching == set()
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


def test_configure_ci_enables_then_opens_the_page(app) -> None:
    stopped = _stopped(app)
    app.app._select_row(stopped.id)

    app.app.configure_ci_selected()
    app.app._drain_events()

    app.manager.enable_ci.assert_called_once_with(stopped)
    assert app.app._board.dock.page(pages.PageKind.CI) is not None


def test_configure_ci_opens_the_page_directly_when_already_enabled(app) -> None:
    stopped = _stopped(app)
    stopped.git = GitConfig(ci_enabled=True)
    app.app._select_row(stopped.id)

    app.app.configure_ci_selected()
    app.app._drain_events()

    app.manager.enable_ci.assert_not_called()
    assert app.app._board.dock.page(pages.PageKind.CI) is not None


def test_saving_from_the_ci_page_persists_through_the_manager(app) -> None:
    # The page owns no git call: it hands the ticked paths and the push answer
    # to the manager, off the main thread.
    stopped = _stopped(app)
    app.app._select_row(stopped.id)
    app.app._open_ci_page(stopped)
    page = app.app._board.dock.page(pages.PageKind.CI)

    page.selection._selection = {"n8nPipelines/a.json"}
    app.app._save_ci_selection(page.workspace, page.selection.selection(), True)
    app.app._drain_events()

    app.manager.save_ci_selection.assert_called_once_with(
        stopped, {"n8nPipelines/a.json"}, push=True
    )


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


def test_a_tooltip_is_kept_inside_the_display(app) -> None:
    # The last row on the right is exactly where a free-text tip (the full name
    # plus the metadata line) would run off the edge of a narrow display, and a
    # tip that is off screen is worse than no tip: the name stays cut and its
    # full text is nowhere to be read.
    app.mocks.tk._screen = (1024, 768)
    widget = app.mocks.tk.Label(None)
    widget._rootx = 940
    widget._rooty = 700
    widget._height = 30
    app.app._attach_tooltip(widget, "a very long workspace name and its summary line")

    FakeTk.Toplevel.instances.clear()
    widget._bindings["<Enter>"](None)
    tip = FakeTk.Toplevel.instances[0]
    label = next(child for child in tip.children if isinstance(child, app.mocks.tk.Label))
    label._reqwidth = 600
    label._reqheight = 40

    # The width is measured after the label is packed, so the geometry the
    # handler computed is recomputed here with the measured text.
    _x, y = map(int, app.app._tooltip_geometry(widget, label).lstrip("+").split("+"))
    assert 0 < _x <= 1024 - 600
    # No room below the row on the bottom of the screen: the tip goes above it.
    assert y < widget._rooty


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


# ------------------------------------------------------------- monitoring
@contextmanager
def _fake_monitoring_gui():
    """Build the integrated journal/server views on the Tk fakes."""
    with (
        fake_monitoring_panel_bases(),
        fake_server_panel_bases(),
        patch("n8n_launcher.gui.monitoring.tk", FakeTk()),
        patch("n8n_launcher.gui.monitoring.ttk", FakeTtk()),
    ):
        yield


def server_page_text(app) -> str:
    """Return the text rendered by the open server page."""
    page = app.app._board.dock.page(pages.PageKind.SERVER)
    assert page is not None
    return page.note_text()


def _monitor_app(app, tmp_path, events=()):
    """Attach a real (tmp) event store to *app* holding *events*."""
    store = EventStore(tmp_path / "logs")
    for event in events:
        store.append(event)
    app.app.monitor = store
    return store


def _event(
    id_: int,
    *,
    level: str = "INFO",
    name: str = "workspace.start",
    message: str = "boom",
    context: dict | None = None,
) -> Event:
    return Event(id=id_, name=name, message=message, level=level, context=context or {})


def _monitor_scheduled(app) -> list[object]:
    """Return the journal poll timers currently scheduled on the root."""
    return [cb for _delay, cb in app.app.root.after_callbacks if cb == app.app._monitor_tick]


def test_main_window_embeds_the_monitoring_panel(app) -> None:
    assert app.app._monitor_panel is not None
    # The journal header is a bare download icon: no "Tous les workspaces"
    # button and no "Actualiser" one (the panel polls on its own timer).
    assert app.app._monitor_panel._export_icon is not None


# ------------------------------------------------------------------ pages
class StubPage:
    """A page the app-level tests can open without building a real view."""

    def __init__(self, workspace, subject, parent=None):
        self.workspace = workspace
        self.subject = subject
        self.parent = parent
        self.retargets: list[object] = []
        self.shown = 0
        self.hidden = 0
        self.closed = 0

    def retarget(self, workspace) -> None:
        self.workspace = workspace
        self.retargets.append(workspace)

    def on_show(self) -> None:
        self.shown += 1

    def on_hide(self) -> None:
        self.hidden += 1

    def on_close(self) -> None:
        self.closed += 1

    def destroy(self) -> None:
        self.closed += 1


CI_SUBJECT = pages.PageSubject("Tests CI", ("CI", "GitHub"))


SERVER_SUBJECT = pages.PageSubject("Serveur", ("Published", "server"))


def _open_page(app, kind: pages.PageKind, subject: pages.PageSubject = CI_SUBJECT) -> StubPage:
    """Open a stub page of *kind* for the selected workspace, in the dock."""
    workspace = app.app._selected_workspace()
    assert workspace is not None
    dock = app.app._board.dock  # type: ignore[union-attr]
    page = dock.open(kind, workspace, lambda _ws, parent: StubPage(_ws, subject, parent))
    app.app._board.pane._width = 1400  # type: ignore[union-attr]
    return page


def test_a_page_is_built_into_a_card_beside_the_list(app) -> None:
    # A card is the dock's pane, and the list is the column on its left: a page
    # opens *beside* the list, so the journal never loses either of them.
    the_board = app.app._board
    assert the_board is not None
    app.app._select_row("ws-running")
    page = _open_page(app, pages.PageKind.CI)
    assert page.parent is the_board.dock.card(pages.PageKind.CI).body()
    panes = list(the_board.pane.panes())
    assert panes.index(the_board.dock.pane) == 1
    assert app.app._list_canvas._parent is the_board.list_card


def test_the_dock_takes_no_share_of_the_room_left_over(app) -> None:
    # A pane with a weight is handed part of the room its content did not ask
    # for — which stretched the journal's header row past its columns — and part
    # of the squeeze when the window is narrow, which cut them off. Every column
    # here carries no weight: the board's split is the only thing that sizes it.
    the_board = app.app._board
    assert the_board is not None
    for index in range(len(the_board.pane.panes())):
        assert the_board.pane.pane(index, "weight") == 0
    app.app._select_row("ws-running")
    _open_page(app, pages.PageKind.CI)
    for index in range(len(the_board.pane.panes())):
        assert the_board.pane.pane(index, "weight") == 0


def test_opening_a_page_filters_the_journal_on_its_subject(app) -> None:
    app.app._select_row("ws-running")
    _open_page(app, pages.PageKind.CI)
    panel = app.app._monitor_panel
    assert panel is not None
    assert panel.visible_subject() == CI_SUBJECT
    assert panel._subject_chip is not None and panel._subject_chip.packed


def test_the_journal_subject_follows_the_focused_card(app) -> None:
    # Two cards are on screen at once, so the *focused* one is what the log is
    # scoped to — and the other one stops polling while it is not.
    app.app._select_row("ws-running")
    ci = _open_page(app, pages.PageKind.CI)
    server = _open_page(app, pages.PageKind.SERVER, SERVER_SUBJECT)
    panel = app.app._monitor_panel
    assert panel is not None
    assert panel.visible_subject() is SERVER_SUBJECT
    assert ci.hidden == 1
    app.app._board.dock.focus(pages.PageKind.CI)  # type: ignore[union-attr]
    assert panel.visible_subject() == CI_SUBJECT
    assert server.hidden == 1


def test_selecting_another_workspace_retargets_open_pages(app) -> None:
    app.app._select_row("ws-running")
    page = _open_page(app, pages.PageKind.CI)
    app.app._select_row("ws-stopped")
    assert page.workspace is not None
    assert page.workspace.id == "ws-stopped"
    # Opened for "ws-running", then retargeted for the newly selected one.
    assert [item.id for item in page.retargets] == ["ws-running", "ws-stopped"]


def test_a_second_workspace_reuses_the_open_card(app) -> None:
    app.app._select_row("ws-running")
    page = _open_page(app, pages.PageKind.CI)
    app.app._select_row("ws-stopped")
    # The list selection already retargeted the page; opening it again must not
    # stack a second card of the same kind.
    dock = app.app._board.dock  # type: ignore[union-attr]
    reopened = dock.open(
        pages.PageKind.CI, app.app._selected_workspace(), lambda *_: pytest.fail("rebuilt")
    )
    assert reopened is page
    assert len(dock.pane.panes()) == 1


def test_monitor_events_are_bounded_and_absent_without_a_store(app, tmp_path) -> None:
    _monitor_app(app, tmp_path, [_event(1)])
    assert len(app.app._monitor_events()) == 1
    app.app.monitor = None
    assert app.app._monitor_events() == []


def test_selecting_a_workspace_scopes_the_journal(app, tmp_path) -> None:
    workspace = next(item for item in app.manager.list.return_value if item.id == "ws-running")
    app.app._select_row(workspace.id)
    app.app._apply_monitor_snapshot(
        [
            _event(1, name="start", context={"workspace_id": workspace.id}),
            _event(2, name="other", context={"workspace_id": "ws-other"}),
        ]
    )
    assert app.app._monitor_panel is not None
    assert app.app._monitor_panel.tree.get_children() == ["event-1"]
    assert app.app._monitor_panel._scope.text == workspace.name


def test_clicking_the_selected_row_again_returns_to_global_logs(app) -> None:
    app.app._select_row("ws-running")
    click = app.app._rows["ws-running"][0].name_label._bindings["<Button-1>"]
    click(None)
    assert app.app._selected_id is None
    assert app.app._monitor_panel is not None
    assert app.app._monitor_panel._scope.text == ""
    assert "Tous les workspaces" in app.app._monitor_panel._summary.text


def test_clicking_another_row_scopes_the_journal_to_it(app) -> None:
    app.app._select_row("ws-running")
    app.app._rows["ws-stopped"][0].name_label._bindings["<Button-1>"](None)
    assert app.app._selected_id == "ws-stopped"


def test_apply_monitor_snapshot_reports_critical_events_without_a_window(app, tmp_path) -> None:
    _monitor_app(app, tmp_path)
    before = len(FakeTk.Toplevel.instances)
    app.app._apply_monitor_snapshot([_event(1, level="CRITICAL")])
    assert "Incident critique" in app.app._status_label._options["text"]
    assert len(FakeTk.Toplevel.instances) == before


def test_apply_monitor_snapshot_groups_repeated_criticals(app, tmp_path) -> None:
    _monitor_app(app, tmp_path)
    before = len(FakeTk.Toplevel.instances)
    app.app._apply_monitor_snapshot([_event(1, level="ERROR", name="docker.up")])
    first_status = app.app._status_label._options["text"]
    app.app._apply_monitor_snapshot([_event(2, level="ERROR", name="docker.up")])
    assert app.app._status_label._options["text"] == first_status
    assert len(FakeTk.Toplevel.instances) == before


def test_apply_monitor_snapshot_ignores_non_critical_events(app, tmp_path) -> None:
    _monitor_app(app, tmp_path)
    app.app.set_status("unchanged")
    app.app._apply_monitor_snapshot([_event(1, level="WARNING")])
    assert app.app._status_label._options["text"] == "unchanged"


def test_apply_monitor_snapshot_ignores_already_seen_events(app, tmp_path) -> None:
    _monitor_app(app, tmp_path)
    seen = replace(_event(1, level="CRITICAL"), id=1)
    app.app._apply_monitor_snapshot([seen], initial=True)
    app.app.set_status("unchanged")
    app.app._apply_monitor_snapshot([seen])
    assert app.app._status_label._options["text"] == "unchanged"


def test_apply_monitor_snapshot_initial_pass_never_reports_critical(app, tmp_path) -> None:
    _monitor_app(app, tmp_path)
    app.app.set_status("unchanged")
    app.app._apply_monitor_snapshot([_event(1, level="CRITICAL")], initial=True)
    assert app.app._status_label._options["text"] == "unchanged"


def test_export_monitor_reports_count_and_path(app, tmp_path) -> None:
    store = _monitor_app(app, tmp_path, [_event(1)])
    target = store.path.with_name("events-export.json")
    with patch("n8n_launcher.gui.app.open_folder") as reveal:
        app.app._export_monitor()
        app.app._drain_events()

    assert target.exists()
    assert app.app._status_label._options["text"] == f"1 événement(s) exportés vers {target}"
    # The export lands in a hidden log directory: the folder is opened for the
    # user, otherwise the status line is the only clue where the file went.
    reveal.assert_called_once_with(target)


def test_export_monitor_does_not_open_the_folder_when_the_write_fails(app, tmp_path) -> None:
    store = _monitor_app(app, tmp_path, [_event(1)])
    with (
        patch("n8n_launcher.gui.app.open_folder") as reveal,
        patch.object(store, "export_events", side_effect=OSError("disk full")),
    ):
        app.app._export_monitor()
        app.app._drain_events()

    reveal.assert_not_called()


def test_monitor_tick_keeps_last_snapshot_when_read_fails(app, tmp_path) -> None:
    _monitor_app(app, tmp_path, [_event(1)])
    app.app._drain_events()
    app.app._apply_monitor_snapshot([_event(1)], initial=True)
    cached = app.app._monitor_events_cache[0]
    with patch.object(app.app, "_monitor_events", side_effect=OSError("disk unavailable")):
        app.app._monitor_tick()
        app.app._drain_events()
    assert app.app._monitor_events_cache == [cached]
    assert "Journal indisponible" in app.app._status_label._options["text"]


def test_monitor_tick_reads_in_the_background_and_reschedules(app, tmp_path) -> None:
    _monitor_app(app, tmp_path, [_event(1)])
    app.app._set_monitor_in_flight(False)
    app.app._monitor_tick()
    app.app._drain_events()
    assert app.app.monitor_in_flight() is False
    assert len(_monitor_scheduled(app)) == 1


def test_monitor_tick_reschedules_while_a_read_is_in_flight(app, tmp_path) -> None:
    _monitor_app(app, tmp_path, [_event(1)])
    app.app._set_monitor_in_flight(True)
    before = _monitor_scheduled(app)
    app.app._monitor_tick()
    assert len(_monitor_scheduled(app)) == len(before)
    assert app.app._monitor_after_id is not None


def test_monitor_tick_is_skipped_once_the_app_is_closed(app, tmp_path) -> None:
    _monitor_app(app, tmp_path, [_event(1)])
    app.app._closed = True
    before = _monitor_scheduled(app)
    app.app._monitor_tick()
    assert _monitor_scheduled(app) == before


# -------------------------------------------------- server supervision entry
def _server_workspace(app, tmp_path):
    """Return the selected workspace, with its server deployment enabled."""
    workspace = make_workspace(tmp_path, "Running", 5678)
    server = ServerConfig(
        enabled=True,
        host="prod.example.test",
        ssh_port=22,
        user="deploy",
        key_path="/home/me/.ssh/id_ed25519",
        base_dir="n8n-launcher/demo",
        n8n_port=5689,
    )
    app.manager.list.return_value = [workspace]
    workspace.server = server
    app.app.refresh()
    app.app._select_row(workspace.id)
    return workspace


def test_context_menu_offers_server_supervision(app, tmp_path) -> None:
    _server_workspace(app, tmp_path)
    app.app._build_context_menu(app.manager.list.return_value[0])
    labels = [label for label, _command in app.app._menu._items if label]
    assert "Superviser le serveur…" in labels


def test_context_menu_hides_server_supervision_without_a_server(app, tmp_path) -> None:
    workspace = make_workspace(tmp_path, "Running", 5678)
    app.app._build_context_menu(workspace)
    labels = [label for label, _command in app.app._menu._items if label]
    assert "Superviser le serveur…" not in labels


def test_supervise_server_opens_the_page_and_reads_in_background(app, tmp_path) -> None:
    workspace = _server_workspace(app, tmp_path)
    health = RemoteHealth(available=True, healthy=True, services={"n8n": "running"})
    app.manager.server_health.return_value = health
    app.manager.server_logs.return_value = "n8n ready"
    app.manager.server_deploy_status.return_value = ({"status": "ok"}, ({"status": "ok"},))
    app.manager.server_execution_status.return_value = RemoteExecutionStatus(
        supported=True,
        executions=(
            RemoteExecution("9001", "success", "Import", "2026-09-25T10:00:00Z", finished=True),
        ),
    )

    app.app.supervise_server_selected()
    app.app._drain_events()

    assert "Santé : ok." in server_page_text(app)
    assert "n8n ready" in server_page_text(app)
    assert "#9001 · terminé" in server_page_text(app)
    assert "Supervision « Running » — sain" in app.app._status_label._options["text"]
    app.manager.server_health.assert_called_once_with(workspace.id)
    app.manager.server_execution_status.assert_called_once_with(workspace.id)


def test_supervise_server_reports_a_degraded_stack(app, tmp_path) -> None:
    _server_workspace(app, tmp_path)
    app.manager.server_health.return_value = RemoteHealth(
        available=True, healthy=False, services={"n8n": "exited"}, error="n8n down"
    )
    app.manager.server_logs.return_value = ""
    app.manager.server_deploy_status.return_value = (None, ())

    app.app.supervise_server_selected()
    app.app._drain_events()

    assert "à vérifier" in app.app._status_label._options["text"]


def test_supervise_server_degrades_to_a_partial_read(app, tmp_path) -> None:
    workspace = _server_workspace(app, tmp_path)
    app.manager.server_health.return_value = RemoteHealth(available=True, healthy=True)
    app.manager.server_logs.side_effect = OSError("ssh timeout")
    app.manager.server_deploy_status.side_effect = OSError("marker unreadable")
    app.manager.server_execution_status.side_effect = OSError("status unreachable")

    app.app.supervise_server_selected()
    app.app._drain_events()

    assert "ssh timeout" in server_page_text(app)
    assert "Santé : ok." in server_page_text(app)
    # The failed fourth read drops its own section only.
    assert "Exécutions n8n" not in server_page_text(app)
    app.manager.server_logs.assert_called_once_with(workspace.id)


def test_supervise_server_without_a_server_opens_the_note(app, tmp_path) -> None:
    # The tab opens whatever the workspace looks like — it never appears and
    # disappears under the user — but a workspace with no server is not read.
    workspace = make_workspace(tmp_path, "Running", 5678)
    app.manager.list.return_value = [workspace]
    app.app.refresh()
    app.app._select_row(workspace.id)

    app.app.supervise_server_selected()
    app.app._drain_events()

    assert server_page.NO_SERVER_NOTE in server_page_text(app)
    app.manager.server_health.assert_not_called()


def test_supervise_server_reports_a_failed_read(app, tmp_path) -> None:
    _server_workspace(app, tmp_path)
    app.manager.server_health.side_effect = OSError("no route to host")
    app.manager.server_logs.side_effect = OSError("no route to host")
    app.manager.server_deploy_status.side_effect = OSError("no route to host")
    app.manager.server_execution_status.side_effect = OSError("no route to host")

    app.app.supervise_server_selected()
    app.app._drain_events()

    # An unreachable server is a rendered partial read, never a raised worker.
    assert "Santé : inconnue." in server_page_text(app)
    assert "no route to host" in server_page_text(app)


# ------------------------------------------------- server page freshness
def _stub_server_reads(app) -> None:
    """Give the manager sane server reads so a snapshot renders as text."""
    app.manager.server_health.return_value = RemoteHealth(available=True, healthy=True)
    app.manager.server_logs.return_value = ""
    app.manager.server_deploy_status.return_value = (None, ())
    app.manager.server_execution_status.return_value = RemoteExecutionStatus(supported=True)


def _server_page_of(app):
    """Return the open server page, asserting the tab exists."""
    page = app.app._board.dock.page(pages.PageKind.SERVER)
    assert page is not None
    return page


def test_a_read_within_the_freshness_window_is_served_from_the_cache(app, tmp_path) -> None:
    # A read is four SSH commands: coming back to the tab, or walking the list
    # and returning, must not pay for it again.
    workspace = _server_workspace(app, tmp_path)
    _stub_server_reads(app)
    app.app.supervise_server_selected()
    app.app._drain_events()
    page = _server_page_of(app)

    page.on_hide()
    page.on_show()
    app.app._drain_events()

    app.manager.server_health.assert_called_once_with(workspace.id)


def test_an_expired_snapshot_is_read_again(app, tmp_path) -> None:
    workspace = _server_workspace(app, tmp_path)
    _stub_server_reads(app)
    app.app.supervise_server_selected()
    app.app._drain_events()
    page = _server_page_of(app)

    app.app._server_fetched_at[workspace.id] = time.monotonic() - (SERVER_SNAPSHOT_TTL_SECONDS + 1)
    page.on_hide()
    page.on_show()
    app.app._drain_events()

    assert app.manager.server_health.call_count == 2


def test_the_manual_refresh_bypasses_the_freshness_window(app, tmp_path) -> None:
    _server_workspace(app, tmp_path)
    _stub_server_reads(app)
    app.app.supervise_server_selected()
    app.app._drain_events()
    page = _server_page_of(app)

    page.force_refresh()
    app.app._drain_events()

    assert app.manager.server_health.call_count == 2


def test_a_read_in_flight_is_never_stacked(app, tmp_path) -> None:
    # The release is queued on the Tk loop, so a second request made before the
    # drain finds the read still in flight and does nothing.
    _server_workspace(app, tmp_path)
    _stub_server_reads(app)
    app.app.supervise_server_selected()
    page = _server_page_of(app)

    page.force_refresh()
    page.force_refresh()
    app.app._drain_events()

    app.manager.server_health.assert_called_once()


def test_a_selection_change_mid_read_is_read_when_the_read_releases(app, tmp_path) -> None:
    # The first read is still on the wire when the selection moves: the second
    # workspace is queued, not dropped, so the tab is not left empty.
    first = _server_workspace(app, tmp_path)
    second = _server_workspace(app, tmp_path)
    second.id = "ws-second"
    app.manager.list.return_value = [first, second]
    app.manager.git_remote_url.return_value = None
    _stub_server_reads(app)
    app.app._select_row(first.id)
    app.app.supervise_server_selected()

    app.app._select_row(second.id)
    # Nothing started yet: the first read still owns the worker slot.
    assert app.manager.server_health.call_count == 1

    app.app._drain_events()

    assert [call.args[0] for call in app.manager.server_health.call_args_list] == [
        first.id,
        second.id,
    ]
    page = _server_page_of(app)
    assert page.workspace is not None
    assert page.workspace.id == "ws-second"
    assert "Santé : ok." in page.note_text()


def test_only_the_latest_deferred_read_is_served(app, tmp_path) -> None:
    first = _server_workspace(app, tmp_path)
    others = []
    for index in range(2):
        other = _server_workspace(app, tmp_path)
        other.id = f"ws-{index}"
        others.append(other)
    app.manager.list.return_value = [first, *others]
    app.manager.git_remote_url.return_value = None
    _stub_server_reads(app)
    app.app._select_row(first.id)
    app.app.supervise_server_selected()

    app.app._select_row(others[0].id)
    app.app._select_row(others[1].id)
    app.app._drain_events()

    # Walking three workspaces in one read is one queued request, not two.
    assert [call.args[0] for call in app.manager.server_health.call_args_list] == [
        first.id,
        others[1].id,
    ]


def test_a_deferred_read_is_dropped_when_the_launcher_closes(app, tmp_path) -> None:
    first = _server_workspace(app, tmp_path)
    second = _server_workspace(app, tmp_path)
    second.id = "ws-second"
    app.manager.list.return_value = [first, second]
    app.manager.git_remote_url.return_value = None
    _stub_server_reads(app)
    app.app._select_row(first.id)
    app.app.supervise_server_selected()
    app.app._select_row(second.id)

    app.app._set_closing(True)
    app.app._drain_events()

    assert app.manager.server_health.call_count == 1
    assert app.app._server_pending is None


def test_a_snapshot_landing_after_the_tab_closed_is_cached(app, tmp_path) -> None:
    # The read outlives the tab: cache it so the next visit is instant, but
    # never render it on destroyed widgets.
    workspace = _server_workspace(app, tmp_path)
    _stub_server_reads(app)
    app.manager.server_logs.return_value = "n8n ready"
    app.app.supervise_server_selected()

    app.app._board.dock.close(pages.PageKind.SERVER)
    app.app._drain_events()

    assert app.app._board.dock.page(pages.PageKind.SERVER) is None
    assert app.app._server_cache[workspace.id].logs == "n8n ready"
    assert app.mocks.messagebox.errors == []


def test_the_server_page_follows_the_list_selection(app, tmp_path) -> None:
    first = _server_workspace(app, tmp_path)
    second = _server_workspace(app, tmp_path)
    second.id = "ws-second"
    second.name = "Second"
    app.manager.list.return_value = [first, second]
    app.manager.git_remote_url.return_value = None
    _stub_server_reads(app)
    app.app._select_row(first.id)
    app.app.supervise_server_selected()
    app.app._drain_events()

    app.app._select_row(second.id)
    app.app._drain_events()

    page = _server_page_of(app)
    assert page.workspace is not None
    assert page.workspace.id == "ws-second"
    # The tab was visible, so the workspace that just changed under the user is
    # read: one read per workspace, never for the previous one.
    assert [call.args[0] for call in app.manager.server_health.call_args_list] == [
        first.id,
        second.id,
    ]


def test_a_hidden_server_page_only_serves_its_cache(app, tmp_path) -> None:
    first = _server_workspace(app, tmp_path)
    second = _server_workspace(app, tmp_path)
    second.id = "ws-second"
    app.manager.list.return_value = [first, second]
    app.manager.git_remote_url.return_value = None
    _stub_server_reads(app)
    app.app._select_row(first.id)
    app.app.supervise_server_selected()
    app.app._drain_events()

    page = _server_page_of(app)
    page.on_hide()  # the user went back to the workspace list
    app.app._select_row(second.id)
    app.app._drain_events()

    app.manager.server_health.assert_called_once_with(first.id)


# ---------------------------------------------------------- context menu
def test_context_menu_no_longer_offers_a_workspace_journal(app, tmp_path) -> None:
    workspace = make_workspace(tmp_path, "Running", 5678)
    app.app._build_context_menu(workspace)
    labels = [label for label, _command in app.app._menu._items if label]
    assert "Journal du workspace…" not in labels


def test_clamp_to_screen_anchors_below_and_to_the_right() -> None:
    # The plain case first: the window hangs off the chip, with a small offset so
    # it does not sit flush against it.
    assert clamp_to_screen(
        anchor_x=100,
        anchor_y=200,
        anchor_height=20,
        width=80,
        height=40,
        screen_width=1000,
        screen_height=800,
    ) == (112, 224)


def test_clamp_to_screen_pulls_the_window_back_inside_the_display() -> None:
    # A chip near the right edge would push the window off the display, so it is
    # pulled back to keep its margin.
    x, _y = clamp_to_screen(
        anchor_x=990,
        anchor_y=100,
        anchor_height=20,
        width=200,
        height=40,
        screen_width=1000,
        screen_height=800,
    )
    assert x == 1000 - 200 - _FLOAT_MARGIN


def test_clamp_to_screen_flips_above_when_there_is_no_room_below() -> None:
    # A chip on the last row would open the window off the bottom of the display,
    # so it opens above the chip instead.
    x, y = clamp_to_screen(
        anchor_x=100,
        anchor_y=770,
        anchor_height=20,
        width=80,
        height=40,
        screen_width=1000,
        screen_height=800,
    )
    assert (x, y) == (112, 770 - 40 - 4)


def test_clamp_to_screen_pins_a_window_bigger_than_the_display() -> None:
    # There is no placement that fits, so the margin wins rather than pushing the
    # window off the far edge where it could not be reached at all.
    assert clamp_to_screen(
        anchor_x=100,
        anchor_y=200,
        anchor_height=20,
        width=1400,
        height=900,
        screen_width=1000,
        screen_height=800,
    ) == (_FLOAT_MARGIN, _FLOAT_MARGIN)


def test_overflow_popover_opens_under_the_chip_and_closes_on_escape(gui_mocks) -> None:
    # The popover is a frameless window of its own, placed from the chip's real
    # position, and Escape is the keyboard way out of it.
    root = FakeRoot()
    root._screen = (1000, 800)
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+2")
    chip._rootx, chip._rooty, chip._height = 100, 200, 20

    popover = _ChipOverflow(root, chip, ["Port : 5680", "Pipelines : 3"])

    window = popover.window
    assert window is not None
    assert window._geometry == "+112+224"
    assert [line.cget("text") for line in _labels_of(window)] == [
        "Port : 5680",
        "Pipelines : 3",
    ]

    window._bindings["<Escape>"](SimpleNamespace(widget=window))
    assert popover.window is None
    assert window.destroyed


def test_overflow_popover_stays_on_screen_when_the_chip_is_in_a_corner(gui_mocks) -> None:
    root = FakeRoot()
    root._screen = (1000, 800)
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+2")
    chip._rootx, chip._rooty, chip._height = 995, 795, 20

    popover = _ChipOverflow(root, chip, ["Port : 5680"])

    window = popover.window
    assert window is not None
    # Both coordinates are pulled back: the window would otherwise open off the
    # right edge and off the bottom of the display at once.
    geometry = window._geometry
    assert geometry.startswith(f"+{1000 - window.winfo_reqwidth() - _FLOAT_MARGIN}+")
    assert int(geometry.rsplit("+", 1)[1]) < 795


def test_overflow_popover_closes_on_a_click_outside_it(gui_mocks) -> None:
    # The click is delivered to the root's own bindtags, so a click anywhere in
    # the list dismisses the popover.
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])
    window = popover.window
    assert window is not None

    root.fire("<Button-1>", SimpleNamespace(widget=SimpleNamespace(__str__=lambda s: ".!frame")))

    assert popover.window is None
    assert window.destroyed


def test_overflow_popover_ignores_a_click_inside_itself(gui_mocks) -> None:
    # The popover is its own toplevel, so a click on it never reaches the root's
    # binding — which is what makes it the "outside" test.
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])
    window = popover.window
    assert window is not None

    root.fire("<Button-1>", SimpleNamespace(widget=window))

    assert popover.window is window
    assert not window.destroyed


def test_overflow_popover_takes_its_own_binding_off_the_root(gui_mocks) -> None:
    # The dismissal must not leave a handler behind, and must not take another
    # handler on the same sequence down with it.
    root = FakeRoot()
    neighbour = MagicMock()
    root._bindings["<Button-1>"] = [neighbour]
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")

    _ChipOverflow(root, chip, ["Port : 5680"])
    assert len(root._bindings["<Button-1>"]) == 2

    root.fire("<Button-1>", SimpleNamespace(widget="!.somewhere"))

    assert root._bindings["<Button-1>"] == [neighbour]
    neighbour.assert_called_once()


def test_overflow_popover_can_be_opened_again_after_being_closed(gui_mocks) -> None:
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])
    first = popover.window

    popover.dismiss()
    popover.show()

    assert popover.window is not None
    assert popover.window is not first


def test_overflow_popover_showing_twice_leaves_one_window(gui_mocks) -> None:
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])
    window = popover.window

    popover.show()

    assert popover.window is window


def test_overflow_popover_survives_a_teardown_that_takes_the_root_away(gui_mocks) -> None:
    # Everything here is best-effort: a popover closed after the widgets it
    # refers to are gone must not surface an error to the caller.
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])

    root.bind = None
    popover.dismiss()

    assert popover.window is None


def test_overflow_popover_survives_an_anchor_that_cannot_be_measured(gui_mocks) -> None:
    # A row that is not on screen yet cannot report where it is; the popover
    # still opens rather than taking the row's click handler down with it.
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    chip.winfo_rootx = None

    popover = _ChipOverflow(root, chip, ["Port : 5680"])

    assert popover.window is not None
    # No geometry was ever applied: the fake only records it on ``geometry``.
    assert not hasattr(popover.window, "_geometry")


def test_clicking_the_chip_again_closes_its_popover(gui_mocks) -> None:
    root = FakeRoot()
    chip = FakeTk.Label(FakeTk.Frame(FakeRoot()), text="+1")
    popover = _ChipOverflow(root, chip, ["Port : 5680"])
    assert popover.window is not None

    handlers = chip._bindings["<Button-1>"]
    for handler in handlers:
        handler(SimpleNamespace(widget=chip))

    assert popover.window is None


def _labels_of(window) -> list[FakeTk.Label]:
    """Return the labels of a popover, in the order they were packed."""
    card = window.children[0]
    return [child for child in card.children if isinstance(child, FakeTk.Label)]


def _refit(app) -> None:
    """Re-fit every row, as a resize of the list does."""
    for workspace_id in list(app.app._rows):
        app.app._fit_row_name(workspace_id)


def _row_overflow(app, workspace_id: str) -> list[str]:
    """Return the details a row currently collapsed, as the user sees it."""
    return app.app._rows[workspace_id][0].overflow


def _overflow_chip_text(app, workspace_id: str) -> str:
    return app.app._rows[workspace_id][0].overflow_chip.cget("text")


def test_a_row_with_room_shows_no_overflow_chip(app) -> None:
    # With room to spare, every detail is on the row and the chip has nothing to
    # say — a chip reading "+0" would be a lie about what is hidden.
    app.app._list_canvas._width = 2000
    _refit(app)

    assert _row_overflow(app, "ws-running") == []
    assert _overflow_chip_text(app, "ws-running") == ""


def test_a_narrow_row_collapses_a_detail_into_the_chip(app) -> None:
    # The name may yield down to its minimum, and not one pixel further: at that
    # exact room both details still fit, one pixel less and the count is hidden.
    both = _row_essentials(app, "ws-running") + _details_width(app)
    room = both + _ROW_NAME_MINIMUM

    app.app._list_canvas._width = room
    _refit(app)
    assert _row_overflow(app, "ws-running") == []
    assert _overflow_chip_text(app, "ws-running") == ""

    app.app._list_canvas._width = room - 1
    _refit(app)
    assert _row_overflow(app, "ws-running") == ["pipelines"]
    assert _overflow_chip_text(app, "ws-running") == "+1"


def test_a_narrow_row_collapses_the_port_last(app) -> None:
    # The port is the detail that says where n8n can be reached, so it is the one
    # that survives: it goes only once the name would fall under its minimum with
    # the count already gone.
    essentials = _row_essentials(app, "ws-running")
    port = _detail_width(app, "port")
    room = essentials + _details_width(app) + _ROW_NAME_MINIMUM

    app.app._list_canvas._width = room - 1
    _refit(app)
    assert _row_overflow(app, "ws-running") == ["pipelines"]

    app.app._list_canvas._width = essentials + port + _ROW_NAME_MINIMUM - 1
    _refit(app)
    assert _row_overflow(app, "ws-running") == ["port", "pipelines"]
    assert _overflow_chip_text(app, "ws-running") == "+2"


def test_a_row_hidden_detail_reappears_when_the_room_comes_back(app) -> None:
    app.app._list_canvas._width = 300
    _refit(app)
    assert _row_overflow(app, "ws-running") == ["port", "pipelines"]

    app.app._list_canvas._width = 2000
    _refit(app)

    assert _row_overflow(app, "ws-running") == []
    assert _overflow_chip_text(app, "ws-running") == ""


def test_a_row_keeps_its_chips_when_the_width_changes(app) -> None:
    # The chip is packed once and only relabelled: rebuilding it on every resize
    # is how a row ends up with a chip in the wrong place.
    row = app.app._rows["ws-running"][0]
    chip = row.overflow_chip
    before = dict(chip._pack_options)

    app.app._list_canvas._width = 300
    _refit(app)

    assert app.app._rows["ws-running"][0].overflow_chip is chip
    assert chip._pack_options == before


def test_a_row_decides_nothing_before_the_list_is_laid_out(app) -> None:
    # Tk answers 0 or 1 before the first layout; that means "unmeasured", not
    # "no room at all", so nothing may be hidden on that answer.
    app.app._list_canvas._width = 1
    _refit(app)

    assert _row_overflow(app, "ws-running") == []
    assert _overflow_chip_text(app, "ws-running") == ""


def test_the_overflow_chip_opens_the_details_of_its_own_row(app) -> None:
    app.app._list_canvas._width = 300
    _refit(app)
    row = app.app._rows["ws-running"][0]

    app.app._toggle_overflow("ws-running")

    assert row.popover is not None
    window = row.popover.window
    assert window is not None
    assert [label.cget("text") for label in _labels_of(window)] == [
        "Port : 5678",
        "Pipelines : 0",
    ]


def test_the_overflow_chip_toggles_its_popover(app) -> None:
    app.app._list_canvas._width = 300
    _refit(app)
    row = app.app._rows["ws-running"][0]

    app.app._toggle_overflow("ws-running")
    window = row.popover.window
    app.app._toggle_overflow("ws-running")

    assert row.popover is None
    assert window is not None
    assert window.destroyed


def test_the_overflow_chip_does_nothing_when_nothing_is_hidden(app) -> None:
    # With no hidden detail there is no "+0" chip to click, and a call from a
    # stale binding must not open an empty window.
    app.app._list_canvas._width = 2000
    _refit(app)
    row = app.app._rows["ws-running"][0]

    app.app._toggle_overflow("ws-running")

    assert row.popover is None


def test_an_open_popover_closes_when_its_row_hides_them_again(app) -> None:
    app.app._list_canvas._width = 300
    _refit(app)
    row = app.app._rows["ws-running"][0]
    app.app._toggle_overflow("ws-running")
    window = row.popover.window

    app.app._list_canvas._width = 2000
    _refit(app)

    assert row.popover is None
    assert window is not None
    assert window.destroyed


def test_the_overflow_of_a_row_that_disappeared_does_nothing(app) -> None:
    # The list is rebuilt on every change, so a binding can fire for a row that is
    # already gone: that must be silent rather than an error.
    app.app._rows.pop("ws-running", None)

    app.app._toggle_overflow("ws-running")
    app.app._fit_row_overflow(app.app._rows["ws-stopped"][0])


def _essentials(app, workspace_id: str) -> list:
    """Return the widgets on a row that are never collapsed, in pack order."""
    row = app.app._rows[workspace_id][0]
    return [
        row.db_chip,
        row.git_chip,
        row.ci_chip,
        row.server_chip,
        row.action_button,
        row.overflow_chip,
    ]


def _row_essentials(app, workspace_id: str) -> int:
    """Return the width of everything on a row that is never collapsed.

    A fake widget reports neither a mapped width nor a request unless the test
    sets one, so what is left is the text plus the gap every packed widget
    carries — which is exactly what the rule falls back to.
    """
    widgets = _essentials(app, workspace_id)
    return sum(_measure(app.app, str(widget.cget("text"))) + _CHIP_GAP for widget in widgets)


def _details_width(app) -> int:
    """Return the width of every optional detail of a row, padding included."""
    row = app.app._rows["ws-running"][0]
    details = display.row_details(app.app._row_workspace(row))
    return sum(_measure(app.app, value) + 2 * _CHIP_PADX for _name, value in details)


def _detail_width(app, name: str) -> int:
    """Return the width one optional detail takes, value and padding."""
    row = app.app._rows["ws-running"][0]
    details = dict(display.row_details(app.app._row_workspace(row)))
    return _measure(app.app, details[name]) + 2 * _CHIP_PADX


def _measure(app, text: str) -> int:
    """Return the width a row reserves for *text*, the way the app measures it."""
    return app._measure(str(text))
