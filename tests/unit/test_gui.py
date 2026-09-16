from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from n8n_launcher.config import ConfigStore
from n8n_launcher.gui import CreatePlan, LauncherApp
from n8n_launcher.models import (
    AppConfig,
    DbConfig,
    DbMode,
    Workspace,
    WorkspaceState,
)
from n8n_launcher.updater import Asset, Release, parse_version
from n8n_launcher.workspace_info import GitRowStatus


class FakeTk:
    END = "end"

    class Tk:
        def __init__(self):
            raise AssertionError("A real Tk root must not be created in tests")

    class Frame:
        def __init__(self, _parent, **kwargs):
            self.children: list[object] = []
            self._options = dict(kwargs)
            self.destroyed = False
            self._bindings: dict[str, object] = {}

        def pack(self, *_args, **_kwargs) -> None:
            pass

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def destroy(self) -> None:
            self.destroyed = True
            self.children.clear()

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def place(self, **kwargs) -> None:
            self._place_options = dict(kwargs)

    class Label:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def unbind(self, sequence: str) -> None:
            self._bindings.pop(sequence, None)

        def place(self, **kwargs) -> None:
            self._place_options = dict(kwargs)

    class Button:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self.text = kwargs.get("text")
            self.command = kwargs.get("command")
            self._options = dict(kwargs)
            self.packed = False

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

    class Menu:
        def __init__(self, _parent, **_kwargs):
            self._items: list[tuple[str | None, object | None]] = []

        def add_command(self, label: str, command) -> None:
            self._items.append((label, command))

        def add_separator(self) -> None:
            self._items.append((None, None))

        def tk_popup(self, _x, _y) -> None:
            pass


class FakeTtk:
    class Frame:
        def __init__(self, _parent, *_args, **_kwargs):
            pass

        def pack(self, *_args, **_kwargs) -> None:
            pass

    class Button:
        instances: list["FakeTtk.Button"] = []

        def __init__(self, _parent, **kwargs):
            self.text = kwargs.pop("text", None)
            self.command = kwargs.pop("command", None)
            self.packed = False
            FakeTtk.Button.instances.append(self)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True


class FakeRoot:
    def __init__(self):
        self.after_callbacks: list[tuple[int, object]] = []
        self._protocol_handlers: dict[str, object] = {}
        self.destroyed = False

    def after(self, delay: int, callback) -> None:
        self.after_callbacks.append((delay, callback))

    def mainloop(self) -> None:
        pass

    def protocol(self, name: str, handler) -> None:
        self._protocol_handlers[name] = handler

    def destroy(self) -> None:
        self.destroyed = True

    def title(self, _value: str) -> None:
        pass

    def geometry(self, _value: str) -> None:
        pass

    def minsize(self, *_args) -> None:
        pass

    def configure(self, **_kwargs) -> None:
        pass


class SyncThread:
    def __init__(self, *, target=None, args=(), kwargs=None, **_extra):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self) -> None:
        if self.target:
            self.target(*self.args, **self.kwargs)


class HoldingThread:
    instances: list["HoldingThread"] = []

    def __init__(self, *, target=None, **kwargs):
        self.target = target
        self.started = False
        HoldingThread.instances.append(self)

    def start(self) -> None:
        self.started = True


class FakeMessagebox:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self._yesno = False
        self._yesnocancel = None
        self._yesnocancel_messages: list[str] = []

    def showerror(self, _title, message, **_kwargs) -> None:
        self.errors.append(message)

    def showwarning(self, _title, message, **_kwargs) -> None:
        self.warnings.append(message)

    def showinfo(self, _title, message, **_kwargs) -> None:
        self.infos.append(message)

    def askyesno(self, *args, **_kwargs) -> bool:
        return self._yesno

    def askyesnocancel(self, *_args, **_kwargs) -> bool | None:
        self._yesnocancel_messages.append(_args[1] if len(_args) > 1 else "")
        return self._yesnocancel


def make_workspace(tmp_path: Path, name: str, port: int) -> Workspace:
    return Workspace(
        id=f"ws-{name.lower()}",
        name=name,
        workflows_dir=tmp_path / name,
        port=port,
        db=DbConfig(DbMode.MANAGED),
    )


def _safe_git_row_status(workspace):
    """Return a default GitRowStatus for non-existent directories."""
    if not workspace.workflows_dir.is_dir():
        return GitRowStatus()
    from n8n_launcher.workspace_info import git_row_status as _real

    return _real(workspace)


@pytest.fixture
def gui_mocks():
    mocks = SimpleNamespace(tk=FakeTk(), ttk=FakeTtk(), messagebox=FakeMessagebox())
    mocks.health_ok = SimpleNamespace(ok=True)
    FakeTtk.Button.instances.clear()
    with patch("n8n_launcher.gui.tk", mocks.tk), patch(
        "n8n_launcher.gui.ttk", mocks.ttk
    ), patch("n8n_launcher.gui.messagebox", mocks.messagebox), patch(
        "n8n_launcher.gui.threading.Thread", SyncThread
    ), patch(
        "n8n_launcher.gui.requests.get", return_value=mocks.health_ok
    ), patch(
        "n8n_launcher.gui.time.sleep"
    ), patch("n8n_launcher.gui.N8nApiClient"), patch(
        "n8n_launcher.gui.SyncRunner"
    ), patch(
        "n8n_launcher.gui.git_row_status", side_effect=_safe_git_row_status
    ):
        yield mocks


@pytest.fixture
def app(gui_mocks, tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    running = make_workspace(tmp_path, "Running", 5678)
    running.state = WorkspaceState.RUNNING
    stopped = make_workspace(tmp_path, "Stopped", 5680)
    manager.list.return_value = [running, stopped]
    docker = MagicMock()
    browser = MagicMock()
    launcher = LauncherApp(
        store, manager, docker, root=FakeRoot(), browser_opener=browser
    )
    return SimpleNamespace(app=launcher, manager=manager, browser=browser, mocks=gui_mocks)


def row_label(app, workspace_id: str) -> FakeTk.Label:
    return app.app._rows[workspace_id][1]


def row_delete_button(app, workspace_id: str):
    frame = app.app._rows[workspace_id][0]
    for child in frame.children:
        if isinstance(child, FakeTk.Button) and child.text == "×":
            return child
    raise AssertionError(f"no delete button in row {workspace_id}")


def row_text(app, workspace_id: str) -> str:
    return row_label(app, workspace_id)._options["text"]


def row_status_label(app, workspace_id: str):
    return app.app._rows[workspace_id][0].status_label


def row_status_text(app, workspace_id: str) -> str:
    return row_status_label(app, workspace_id)._options["text"]


def row_chip(app, workspace_id: str, attr: str):
    return getattr(app.app._rows[workspace_id][0], attr)


def row_chip_text(app, workspace_id: str, attr: str) -> str:
    return row_chip(app, workspace_id, attr)._options["text"]


def row_chip_colors(app, workspace_id: str, attr: str) -> tuple[str, str]:
    chip = row_chip(app, workspace_id, attr)
    return chip._options["bg"], chip._options["fg"]


def row_action_button(app, workspace_id: str):
    frame = app.app._rows[workspace_id][0]
    return getattr(frame, "action_button")


def row_action_text(app, workspace_id: str) -> str:
    return row_action_button(app, workspace_id).text


ACTIVE_CHIP = ("#064e3b", "#34d399")
INACTIVE_CHIP = ("#7f1d1d", "#fca5a5")
WARN_CHIP = ("#78350f", "#fcd34d")


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

    with patch("n8n_launcher.gui.git_row_status", side_effect=pick_status):
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
        with patch("n8n_launcher.gui.git_row_status", return_value=status):
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
    with patch("n8n_launcher.gui.threading.Thread", HoldingThread):
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
        "n8n_launcher.gui.requests.get",
        side_effect=[SimpleNamespace(ok=False)] * 5,
    ), patch(
        "n8n_launcher.gui.time.monotonic", side_effect=[0, 1, 2, 300, 301, 302]
    ):
        app.app._select_row("ws-stopped")
        app.app.launch_selected()
        app.app._drain_events()

    app.browser.assert_not_called()
    assert "did not become ready" in app.mocks.messagebox.errors[0]


def test_open_workflows_uses_xdg_open_on_linux(app, tmp_path) -> None:
    app.app._select_row("ws-stopped")

    with patch("n8n_launcher.gui.platform.system", return_value="Linux"), patch(
        "n8n_launcher.gui.subprocess.Popen"
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


def test_prompt_create_uses_plan_name_and_db(app, tmp_path) -> None:
    folder = tmp_path / "wf-plan"
    folder.mkdir()
    plan = CreatePlan(name="MyPlan", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.create.assert_called_once()
    assert app.manager.create.call_args.args[0] == "MyPlan"
    assert app.manager.create.call_args.kwargs["db"].mode is DbMode.NONE


def test_prompt_create_uses_managed_db_from_plan(app, tmp_path) -> None:
    folder = tmp_path / "wf-managed"
    folder.mkdir()
    plan = CreatePlan(name="wf-managed", db=DbConfig(DbMode.MANAGED, database_name="data", username="n8ndata", password="secret"))

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    database = app.manager.create.call_args.kwargs["db"]
    assert database.mode is DbMode.MANAGED
    assert database.password == "secret"


def test_prompt_create_with_git_enabled_inits_repo(app, tmp_path) -> None:
    folder = tmp_path / "wf-git"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-git",
        db=DbConfig(DbMode.NONE),
        git_enabled=True,
        git_url="https://example.test/repo.git",
    )

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.git_init_workspace.assert_called_once()
    workspace_obj = app.manager.create.return_value
    app.manager.git_init_workspace.assert_called_with(
        workspace_obj, remote_url="https://example.test/repo.git"
    )


def test_prompt_create_with_git_disabled_skips_init(app, tmp_path) -> None:
    folder = tmp_path / "wf-nogit"
    folder.mkdir()
    plan = CreatePlan(name="wf-nogit", db=DbConfig(DbMode.NONE), git_enabled=False)

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.git_init_workspace.assert_not_called()


def test_prompt_create_cancels_when_plan_is_none(app, tmp_path) -> None:
    folder = tmp_path / "wf-cancel"
    folder.mkdir()

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=None
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()
    app.app._drain_events()


def test_prompt_create_skips_when_user_cancels_directory(app) -> None:
    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=""):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()


def test_default_creation_db_uses_managed_when_migrations_exist(app, tmp_path) -> None:
    folder = tmp_path / "wf"
    (folder / "db" / "migrations").mkdir(parents=True)
    (folder / "db" / "migrations" / "001.sql").write_text("select 1;")

    db = app.app._default_creation_db(folder)
    assert db.mode is DbMode.MANAGED
    assert db.password


def test_default_creation_db_uses_none_when_no_migrations(app, tmp_path) -> None:
    folder = tmp_path / "wf-nomig"
    folder.mkdir()

    db = app.app._default_creation_db(folder)
    assert db.mode is DbMode.NONE


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


def _drain_queue(app) -> None:
    while not app.events.empty():
        app.events.get_nowait()


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


def test_empty_space_click_creates_workflow(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-click"
    folder.mkdir()
    plan = CreatePlan(name="wf-click", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        launcher.workspace_list._bindings["<Button-1>"](None)
    launcher._drain_events()

    database = manager.create.call_args.kwargs["db"]
    assert manager.create.call_args.args == ("wf-click", folder)
    assert database.mode is DbMode.NONE


def test_empty_space_click_deselects_when_rows_exist(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    ws = make_workspace(tmp_path, "Existing", 5678)
    manager.list.return_value = [ws]
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    launcher._select_row("ws-existing")

    launcher.workspace_list._bindings["<Button-1>"](None)

    assert launcher._selected_id is None
    manager.create.assert_not_called()


def test_watermark_click_triggers_creation(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-watermark"
    folder.mkdir()
    plan = CreatePlan(name="wf-watermark", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        launcher._watermark._bindings["<Button-1>"](None)
    launcher._drain_events()

    assert manager.create.call_args.args[0] == "wf-watermark"


def test_close_wired_on_window_protocol(app) -> None:
    assert "WM_DELETE_WINDOW" in app.app.root._protocol_handlers


def test_on_close_stops_and_syncs_workspaces_then_destroys(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.app.on_close()
    app.app._drain_events()

    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])
    assert app.app.root.destroyed
    assert app.app._closed


def test_on_close_blocks_when_sync_fails_and_declines_close(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"
    app.mocks.messagebox._yesnocancel = None

    from n8n_launcher import gui as gui_module

    gui_module.SyncRunner.return_value.export_all.side_effect = RuntimeError("sync boom")
    app.app.on_close()
    app.app._drain_events()

    assert not app.app.root.destroyed
    assert app.app._closing is False
    assert "sync boom" in app.mocks.messagebox._yesnocancel_messages[0]
    app.manager.stop.assert_not_called()


def test_on_close_retries_sync_then_destroys(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"
    app.mocks.messagebox._yesnocancel = True

    from n8n_launcher import gui as gui_module

    gui_module.SyncRunner.return_value.export_all.side_effect = [RuntimeError("sync boom"), None]
    app.app.on_close()
    app.app._drain_events()

    assert app.app.root.destroyed
    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])


def test_on_close_destroys_when_no_workspaces(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []

    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    launcher.on_close()

    assert launcher.root.destroyed


def test_create_with_real_manager_persists_and_selects_row(gui_mocks, tmp_path) -> None:
    from n8n_launcher.workspace_manager import WorkspaceManager

    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = WorkspaceManager(store, MagicMock())
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )

    folder = tmp_path / "wf-real"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-real",
        db=DbConfig(DbMode.MANAGED, database_name="data", username="n8ndata", password="pw"),
        git_enabled=False,
    )
    gui_mocks.messagebox._yesno = True
    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        launcher.prompt_create_workflow()
    launcher._drain_events()

    workspaces = store.load().workspaces
    assert len(workspaces) == 1
    assert workspaces[0].name == "wf-real"
    assert workspaces[0].db.mode is DbMode.MANAGED
    created_id = workspaces[0].id
    assert row_text(SimpleNamespace(app=launcher), created_id) == "wf-real"
    assert row_status_text(SimpleNamespace(app=launcher), created_id) == "Arrêté"
    assert row_chip_text(SimpleNamespace(app=launcher), created_id, "port_chip") == (
        f":{workspaces[0].port}"
    )
    assert row_chip_text(SimpleNamespace(app=launcher), created_id, "db_chip") == "locale"
    assert launcher._selected_id == created_id
    assert (folder / "n8nPipelines").is_dir()
    assert (folder / "db" / "migrations").is_dir()
    assert (folder / "db" / "schema.sql").is_file()


def test_delete_with_real_manager_removes_but_keeps_folder(gui_mocks, tmp_path) -> None:
    from n8n_launcher.workspace_manager import WorkspaceManager

    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = WorkspaceManager(store, MagicMock())
    launcher = LauncherApp(
        store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock()
    )
    folder = tmp_path / "wf-to-delete"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-to-delete",
        db=DbConfig(DbMode.NONE),
        git_enabled=False,
    )
    with patch("n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.LauncherApp._prompt_create_dialog", return_value=plan
    ):
        launcher.prompt_create_workflow()
    launcher._drain_events()

    workspace_id = store.load().workspaces[0].id
    gui_mocks.messagebox._yesno = True
    launcher.delete_workspace(workspace_id)
    launcher._drain_events()

    assert store.load().workspaces == []
    assert launcher._rows == {}
    assert folder.is_dir()
    assert (folder / "n8nPipelines").is_dir()


# --- auto-update ------------------------------------------------------------


TEST_ASSET = Asset(
    name="n8n-launcher-macos.dmg",
    url="https://example.test/n8n-launcher-macos.dmg",
    size=42,
)


def make_test_release() -> Release:
    return Release(
        tag_name="v0.4.01",
        version=parse_version("v0.4.01"),
        assets={TEST_ASSET.name: TEST_ASSET},
    )


def update_worker_patches(target, *, writable: bool):
    return [
        patch("n8n_launcher.gui.updater.fetch_latest_release", return_value=make_test_release()),
        patch("n8n_launcher.gui.updater.install_target", return_value=target),
        patch("n8n_launcher.gui.updater.compatible_asset", return_value=TEST_ASSET),
        patch("n8n_launcher.gui.os.access", return_value=writable),
    ]


def run_update_worker(app, target, *, writable: bool) -> None:
    from contextlib import ExitStack

    _drain_queue(app.app)
    with ExitStack() as stack:
        with patch("n8n_launcher.gui.updater.cleanup_stale"):
            for cm in update_worker_patches(target, writable=writable):
                stack.enter_context(cm)
            app.app._check_updates()


def next_event(app):
    return app.app.events.get_nowait()


def test_update_check_scheduled_when_frozen(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    root = FakeRoot()
    with patch(
        "n8n_launcher.gui.updater.install_target",
        return_value=tmp_path / "n8n-launcher",
    ):
        LauncherApp(store, MagicMock(), MagicMock(), root=root, browser_opener=MagicMock())

    assert any(delay == 1500 for delay, _ in root.after_callbacks)


def test_update_check_not_scheduled_from_source(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    root = FakeRoot()
    with patch("n8n_launcher.gui.updater.install_target", return_value=None):
        LauncherApp(store, MagicMock(), MagicMock(), root=root, browser_opener=MagicMock())

    assert not any(delay == 1500 for delay, _ in root.after_callbacks)


def test_update_worker_posts_offer_through_queue(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    callback, error = next_event(app)
    assert error is None
    ask = MagicMock(return_value=False)
    with patch("n8n_launcher.gui.messagebox.askyesno", ask), patch(
        "n8n_launcher.gui.updater.download_asset"
    ) as download_asset:
        callback()

    ask.assert_called_once()
    download_asset.assert_not_called()


def test_update_worker_stays_silent_when_network_fails(app, tmp_path) -> None:
    _drain_queue(app.app)
    with patch(
        "n8n_launcher.gui.updater.fetch_latest_release",
        side_effect=RuntimeError("offline"),
    ), patch(
        "n8n_launcher.gui.updater.install_target",
        return_value=tmp_path / "n8n-launcher",
    ), patch("n8n_launcher.gui.updater.cleanup_stale"):
        app.app._check_updates()

    assert app.app.events.empty()


def test_update_worker_shows_link_when_not_writable(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)

    callback, error = next_event(app)
    assert error is None
    callback()

    status = app.app._status_label
    assert "Nouvelle version" in status._options["text"]
    assert "<Button-1>" in status._bindings
    assert status._options["cursor"] == "hand2"


def test_update_link_opens_release_page(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)
    callback, _error = next_event(app)
    callback()

    with patch("n8n_launcher.gui.webbrowser.open") as webbrowser_open:
        app.app._status_label._bindings["<Button-1>"](None)

    webbrowser_open.assert_called_once_with("https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest")


def test_set_status_resets_update_link(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)
    callback, _error = next_event(app)
    callback()

    app.app.set_status("")

    assert "<Button-1>" not in app.app._status_label._bindings
    assert app.app._status_label._options["cursor"] == ""
    assert app.app._status_label._options["fg"] == "#94a3b8"


def test_update_accept_flow_downloads_installs_and_relaunches(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    script = tmp_path / "apply_update.sh"
    run_update_worker(app, target, writable=True)

    ask = MagicMock(side_effect=[True, True])
    with patch("n8n_launcher.gui.messagebox.askyesno", ask), patch(
        "n8n_launcher.gui.updater.download_asset"
    ) as download_asset, patch(
        "n8n_launcher.gui.updater.installer_script", return_value=script
    ) as installer_script, patch(
        "n8n_launcher.gui.updater.spawn_installer"
    ) as spawn_installer:
        app.app._drain_events()

    download_asset.assert_called_once()
    args, kwargs = download_asset.call_args
    assert args[0] == TEST_ASSET.url
    assert kwargs["expected_size"] == TEST_ASSET.size
    assert callable(kwargs["progress"])
    installer_script.assert_called_once()
    spawn_installer.assert_called_once_with(script)
    assert app.app.root.destroyed
    assert app.app._closed


def test_update_offer_declined_skips_download(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    with patch("n8n_launcher.gui.messagebox.askyesno", return_value=False), patch(
        "n8n_launcher.gui.updater.download_asset"
    ) as download_asset:
        app.app._drain_events()

    download_asset.assert_not_called()
    assert not app.app.root.destroyed


def test_update_download_failure_surfaces_error(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    with patch("n8n_launcher.gui.messagebox.askyesno", return_value=True), patch(
        "n8n_launcher.gui.updater.download_asset",
        side_effect=RuntimeError("500 boom"),
    ) as download_asset:
        app.app._drain_events()

    download_asset.assert_called_once()
    assert app.mocks.messagebox.errors == ["Téléchargement impossible : 500 boom"]
    assert not app.app.root.destroyed


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


def test_on_close_warns_when_push_failed_during_sync(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    def fail_push(workspace, *, push: bool = True) -> None:
        workspace.git_push_failed = True

    app.manager.sync_git.side_effect = fail_push
    app.app.on_close()
    app.app._drain_events()

    assert app.mocks.messagebox.warnings, "a push-failure warning should be shown"
    assert "push" in app.mocks.messagebox.warnings[0].lower()
    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])
    assert app.app.root.destroyed


def test_on_close_does_not_warn_when_push_ok(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.manager.sync_git.side_effect = lambda ws, **kwargs: None
    app.app.on_close()
    app.app._drain_events()

    assert app.mocks.messagebox.warnings == []
    assert app.app.root.destroyed


def test_close_sync_reports_status(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.app._close_sync(running, [])

    assert app.app._status_label._options["text"] == (
        f"Fermeture : synchronisation de « {running.name} »…"
    )
    app.app._drain_events()


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
