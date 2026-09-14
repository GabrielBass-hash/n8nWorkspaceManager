from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from n8n_launcher.config import ConfigStore
from n8n_launcher.gui import LauncherApp
from n8n_launcher.models import (
    AppConfig,
    DbConfig,
    DbMode,
    Workspace,
    WorkspaceState,
)


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
    def __init__(self, *, target=None, **kwargs):
        self.target = target

    def start(self) -> None:
        if self.target:
            self.target()


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
    ), patch("n8n_launcher.gui.N8nApiClient"), patch("n8n_launcher.gui.SyncRunner"):
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


def row_meta_text(app, workspace_id: str) -> str:
    return app.app._rows[workspace_id][0].meta_label._options["text"]


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

    app.app.refresh()

    assert row_text(app, "ws-gitws") == "GitWs"
    assert row_status_text(app, "ws-gitws") == "En cours"
    status = row_status_label(app, "ws-gitws")
    assert status._options["bg"] == "#064e3b"
    assert status._options["fg"].startswith("#")
    assert row_meta_text(app, "ws-gitws") == ":5700 · db locale · git oui · 1 pipeline"
    assert row_text(app, "ws-plain") == "Plain"
    assert row_status_text(app, "ws-plain") == "Arrêté"
    assert row_meta_text(app, "ws-plain") == ":5680 · db locale · git non · 0 pipeline"


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

    app.manager.ensure_running.assert_called_once_with("ws-stopped")
    app.browser.assert_called_once_with("http://127.0.0.1:5680")


def test_launch_dispatches_to_manager(app) -> None:
    app.app._select_row("ws-stopped")
    app.app.launch_selected()

    app.manager.ensure_running.assert_called_once_with("ws-stopped")
    app.manager.stop.assert_not_called()


def test_async_error_surfaces_in_messagebox(app) -> None:
    app.manager.ensure_running.side_effect = RuntimeError("boom")
    app.app._select_row("ws-running")

    app.app.launch_selected()
    app.app._drain_events()

    assert app.app.root.after_callbacks
    assert app.mocks.messagebox.errors == ["boom"]


def test_launch_does_not_restart_when_running(app) -> None:
    app.app._select_row("ws-running")
    app.app.launch_selected()
    app.app._drain_events()

    app.manager.ensure_running.assert_called_once_with("ws-running")
    app.manager.start.assert_not_called()
    app.browser.assert_called_once_with("http://127.0.0.1:5678")


def test_launch_reports_health_timeout(app, gui_mocks) -> None:
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

    app.manager.ensure_running.assert_called_once_with("ws-stopped")
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


def test_footer_plus_button_is_wired(app) -> None:
    create = next(
        button for button in FakeTtk.Button.instances if button.text == "+  Nouveau workflow"
    )
    assert create.packed
    assert callable(create.command)


def test_context_menu_has_launch_folder_and_delete(app) -> None:
    labels = [label for label, _ in app.app._menu._items if label]
    assert labels == ["Ouvrir n8n", "Ouvrir le dossier", "Supprimer"]


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


def test_prompt_create_uses_managed_db_when_db_layout_present(app, tmp_path) -> None:
    folder = tmp_path / "wf"
    (folder / "db" / "migrations").mkdir(parents=True)
    (folder / "db" / "migrations" / "001.sql").write_text("select 1;")

    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="My flow"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_called_once_with(
        "My flow", folder, db=DbConfig(DbMode.MANAGED)
    )


def test_prompt_create_uses_managed_db_by_default_on_empty_folder(app, tmp_path) -> None:
    folder = tmp_path / "wf-nomig"
    folder.mkdir()
    app.mocks.messagebox._yesnocancel = True

    with patch(
        "n8n_launcher.gui.simpledialog.askstring", return_value="My flow"
    ), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        app.app.prompt_create_workflow()

    database = app.manager.create.call_args.kwargs["db"]
    assert database.mode is DbMode.MANAGED
    assert database.password


def test_prompt_create_uses_external_db_when_local_declined(app, tmp_path) -> None:
    folder = tmp_path / "wf2"
    folder.mkdir()
    app.mocks.messagebox._yesnocancel = False

    with patch(
        "n8n_launcher.gui.simpledialog.askstring",
        side_effect=["My flow", "postgresql://u:p@host/db"],
    ), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_called_once_with(
        "My flow",
        folder,
        db=DbConfig(DbMode.EXTERNAL, connection_string="postgresql://u:p@host/db"),
    )


def test_prompt_create_uses_none_db_when_user_cancels_dialog(app, tmp_path) -> None:
    folder = tmp_path / "wf4"
    folder.mkdir()
    app.mocks.messagebox._yesnocancel = None

    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="My flow"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_called_once_with(
        "My flow", folder, db=DbConfig(DbMode.NONE)
    )


def test_prompt_create_none_db_when_folder_already_has_content(app, tmp_path) -> None:
    folder = tmp_path / "wf-existing"
    folder.mkdir()
    (folder / "notes.txt").write_text("deja la")

    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="My flow"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_called_once_with(
        "My flow", folder, db=DbConfig(DbMode.NONE)
    )


def test_prompt_create_aborts_with_info_when_dsn_empty(app, tmp_path) -> None:
    folder = tmp_path / "wf3"
    folder.mkdir()
    app.mocks.messagebox._yesnocancel = False

    with patch(
        "n8n_launcher.gui.simpledialog.askstring", return_value="My flow"
    ), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ), patch(
        "n8n_launcher.gui.simpledialog.askstring",
        side_effect=["My flow", None],
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()
    assert app.mocks.messagebox.infos == [
        "Création annulée : aucune connexion distante fournie."
    ]


def test_prompt_create_skips_when_user_cancels(app) -> None:
    with patch("n8n_launcher.gui.simpledialog.askstring", return_value=None):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()


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


def test_empty_list_has_empty_space_click_binding(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    assert "<Button-1>" in launcher.workspace_list._bindings


def test_empty_state_hint_is_shown_and_clickable(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    hint = launcher._empty_hint
    assert hint is not None
    assert hint._options["text"] == "Aucun workflow — cliquez ici pour en créer un"
    assert callable(hint._bindings.get("<Button-1>"))


def test_empty_state_hint_disappears_when_workspaces_exist(app) -> None:
    app.app.refresh()

    assert app.app._empty_hint is None


def test_empty_space_click_creates_workflow(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-click"
    folder.mkdir()
    gui_mocks.messagebox._yesnocancel = True

    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="Click flow"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        launcher.workspace_list._bindings["<Button-1>"](None)
    launcher._drain_events()

    database = manager.create.call_args.kwargs["db"]
    assert manager.create.call_args.args == ("Click flow", folder)
    assert database.mode is DbMode.MANAGED
    assert database.password


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
    gui_mocks.messagebox._yesnocancel = True
    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="Mon flow"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
    ):
        launcher.prompt_create_workflow()
    launcher._drain_events()

    workspaces = store.load().workspaces
    assert len(workspaces) == 1
    assert workspaces[0].name == "Mon flow"
    assert workspaces[0].db.mode is DbMode.MANAGED
    created_id = workspaces[0].id
    assert row_text(SimpleNamespace(app=launcher), created_id) == "Mon flow"
    assert row_status_text(SimpleNamespace(app=launcher), created_id) == "Arrêté"
    meta = row_meta_text(SimpleNamespace(app=launcher), created_id)
    assert f":{workspaces[0].port}" in meta
    assert "db locale" in meta
    assert launcher._selected_id == created_id
    assert (folder / "n8nPipelines").is_dir()
    assert (folder / "db" / "migrations").is_dir()


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
    gui_mocks.messagebox._yesnocancel = True
    with patch("n8n_launcher.gui.simpledialog.askstring", return_value="À supprimer"), patch(
        "n8n_launcher.gui.filedialog.askdirectory", return_value=str(folder)
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