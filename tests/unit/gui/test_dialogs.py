"""GUI creation-dialog tests: prompt_create_workflow, DB default and click triggers."""

import types
from unittest.mock import MagicMock, patch

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, DbConfig, DbMode
from n8n_launcher.gui import CreatePlan, LauncherApp
from n8n_launcher.gui.dialogs import default_creation_db, prompt_ask_string, prompt_db_config

from helpers import FakeRoot, make_workspace  # noqa: E402
from helpers import row_chip_text, row_text  # noqa: E402
from n8n_launcher.workspaces.manager import WorkspaceManager


def test_prompt_create_uses_plan_name_and_db(app, tmp_path) -> None:
    folder = tmp_path / "wf-plan"
    folder.mkdir()
    plan = CreatePlan(name="MyPlan", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
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

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
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

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
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

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.git_init_workspace.assert_not_called()


def test_prompt_create_cancels_when_plan_is_none(app, tmp_path) -> None:
    folder = tmp_path / "wf-cancel"
    folder.mkdir()

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=None
    ):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()
    app.app._drain_events()


def test_prompt_create_skips_when_user_cancels_directory(app) -> None:
    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=""):
        app.app.prompt_create_workflow()

    app.manager.create.assert_not_called()


def test_default_creation_db_uses_managed_when_migrations_exist(tmp_path) -> None:
    folder = tmp_path / "wf"
    (folder / "db" / "migrations").mkdir(parents=True)
    (folder / "db" / "migrations" / "001.sql").write_text("select 1;")

    db = default_creation_db(folder)
    assert db.mode is DbMode.MANAGED
    assert db.password


def test_default_creation_db_uses_none_when_no_migrations(tmp_path) -> None:
    folder = tmp_path / "wf-nomig"
    folder.mkdir()

    db = default_creation_db(folder)
    assert db.mode is DbMode.NONE


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


def test_empty_space_click_creates_workflow(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-click"
    folder.mkdir()
    plan = CreatePlan(name="wf-click", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
    ):
        launcher.workspace_list._bindings["<Button-1>"](None)
    launcher._drain_events()

    database = manager.create.call_args.kwargs["db"]
    assert manager.create.call_args.args == ("wf-click", folder)
    assert database.mode is DbMode.NONE


def test_watermark_click_triggers_creation(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-watermark"
    folder.mkdir()
    plan = CreatePlan(name="wf-watermark", db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
    ):
        launcher._watermark._bindings["<Button-1>"](None)
    launcher._drain_events()

    assert manager.create.call_args.args[0] == "wf-watermark"


def test_create_with_real_manager_persists_and_selects_row(gui_mocks, tmp_path) -> None:
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
    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
    ):
        launcher.prompt_create_workflow()
    launcher._drain_events()

    workspaces = store.load().workspaces
    assert len(workspaces) == 1
    assert workspaces[0].name == "wf-real"
    assert workspaces[0].db.mode is DbMode.MANAGED
    created_id = workspaces[0].id

    if created_id not in launcher._rows:
        launcher.refresh()
    if launcher._row_order and launcher._selected_id != created_id:
        launcher._select_row(created_id)

    wrapper = types.SimpleNamespace(app=launcher)
    assert row_text(wrapper, created_id) == "wf-real"
    assert row_chip_text(wrapper, created_id, "port_chip") == (
        f":{workspaces[0].port}"
    )
    assert row_chip_text(wrapper, created_id, "db_chip") == "locale"
    assert launcher._selected_id == created_id
    assert (folder / "n8nPipelines").is_dir()
    assert (folder / "db" / "migrations").is_dir()
    assert (folder / "db" / "schema.sql").is_file()


def test_delete_with_real_manager_removes_but_keeps_folder(gui_mocks, tmp_path) -> None:
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
    with patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)), patch(
        "n8n_launcher.gui.app.prompt_create_plan", return_value=plan
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


def test_prompt_ask_string_returns_submitted_value(gui_mocks) -> None:
    with patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk), patch(
        "n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk
    ):
        result = prompt_ask_string(
            FakeRoot(), "Configurer Git", "Question ?", initial="valeur"
        )

    assert result == "valeur"


def test_prompt_ask_string_escape_returns_none(gui_mocks) -> None:
    toplevel = gui_mocks.tk.Toplevel
    saved = toplevel.cancel_on_wait
    toplevel.cancel_on_wait = True
    try:
        with patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk), patch(
            "n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk
        ):
            result = prompt_ask_string(
                FakeRoot(), "Configurer Git", "Question ?", initial="valeur"
            )
    finally:
        toplevel.cancel_on_wait = saved

    assert result is None


def test_prompt_db_config_returns_submitted_managed_config(gui_mocks) -> None:
    expected = DbConfig(
        DbMode.MANAGED, database_name="data", username="n8ndata", password="oldpass"
    )
    with patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk), patch(
        "n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk
    ):
        result = prompt_db_config(FakeRoot(), expected)

    assert result == expected


def test_prompt_db_config_locks_identity_for_existing_managed(gui_mocks) -> None:
    gui_mocks.tk.Toplevel.instances.clear()
    gui_mocks.ttk.Button.instances.clear()
    expected = DbConfig(
        DbMode.MANAGED, database_name="data", username="n8ndata", password="oldpass"
    )

    with patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk), patch(
        "n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk
    ):
        result = prompt_db_config(FakeRoot(), expected)

    dialog = gui_mocks.tk.Toplevel.instances[0]
    gui_entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
    assert len(gui_entries) == 3
    assert all(entry._options.get("state") == "disabled" for entry in gui_entries)

    gui_buttons = [
        child for child in dialog.children if isinstance(child, gui_mocks.ttk.Button)
    ]
    generate = next(button for button in gui_buttons if "Régénérer" in (button.text or ""))
    assert generate.state == "disabled"
    assert result == expected


def test_prompt_db_config_escape_returns_none(gui_mocks) -> None:
    toplevel = gui_mocks.tk.Toplevel
    saved = toplevel.cancel_on_wait
    toplevel.cancel_on_wait = True
    try:
        with patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk), patch(
            "n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk
        ):
            result = prompt_db_config(FakeRoot(), DbConfig(DbMode.NONE))
    finally:
        toplevel.cancel_on_wait = saved

    assert result is None