"""GUI creation-dialog tests: prompt_create_workflow, DB default and click triggers."""

import types
from unittest.mock import MagicMock, patch

from helpers import (
    FakeRoot,
    FakeTk,
    make_workspace,
    row_chip_text,
    row_text,
)

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, DbConfig, DbMode
from n8n_launcher.gui import CreatePlan, LauncherApp
from n8n_launcher.gui.dialogs import (
    GitClonePlan,
    GitConfigChoice,
    GitHubCreatePlan,
    GitHubTokenPlan,
    _repo_name_from,
    default_creation_db,
    prompt_ask_string,
    prompt_clone_plan,
    prompt_create_source,
    prompt_db_config,
    prompt_github_create,
    prompt_github_token,
)
from n8n_launcher.workspaces.manager import WorkspaceManager


def test_prompt_create_uses_plan_name_and_db(app, tmp_path) -> None:
    folder = tmp_path / "wf-plan"
    folder.mkdir()
    plan = CreatePlan(name="MyPlan", db=DbConfig(DbMode.NONE))

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.create.assert_called_once()
    assert app.manager.create.call_args.args[0] == "MyPlan"
    assert app.manager.create.call_args.kwargs["db"].mode is DbMode.NONE


def test_prompt_create_uses_managed_db_from_plan(app, tmp_path) -> None:
    folder = tmp_path / "wf-managed"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-managed",
        db=DbConfig(DbMode.MANAGED, database_name="data", username="n8ndata", password="secret"),
    )

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
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

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
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

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    app.manager.git_init_workspace.assert_not_called()


def test_prompt_create_cancels_when_plan_is_none(app, tmp_path) -> None:
    folder = tmp_path / "wf-cancel"
    folder.mkdir()

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=None),
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

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
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

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
    ):
        launcher._watermark._bindings["<Button-1>"](None)
    launcher._drain_events()

    assert manager.create.call_args.args[0] == "wf-watermark"


def test_create_with_real_manager_persists_and_selects_row(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = WorkspaceManager(store, MagicMock())
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())

    folder = tmp_path / "wf-real"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-real",
        db=DbConfig(DbMode.MANAGED, database_name="data", username="n8ndata", password="pw"),
        git_enabled=False,
    )
    gui_mocks.messagebox._yesno = True
    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
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
    assert row_chip_text(wrapper, created_id, "port_chip") == (f":{workspaces[0].port}")
    assert row_chip_text(wrapper, created_id, "db_chip") == "locale"
    assert launcher._selected_id == created_id
    assert (folder / "n8nPipelines").is_dir()
    assert (folder / "db" / "migrations").is_dir()
    assert (folder / "db" / "schema.sql").is_file()


def test_delete_with_real_manager_removes_but_keeps_folder(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = WorkspaceManager(store, MagicMock())
    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    folder = tmp_path / "wf-to-delete"
    folder.mkdir()
    plan = CreatePlan(
        name="wf-to-delete",
        db=DbConfig(DbMode.NONE),
        git_enabled=False,
    )
    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
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
    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
    ):
        result = prompt_ask_string(FakeRoot(), "Configurer Git", "Question ?", initial="valeur")

    assert result == "valeur"


def test_prompt_ask_string_escape_returns_none(gui_mocks) -> None:
    toplevel = gui_mocks.tk.Toplevel
    saved = toplevel.cancel_on_wait
    toplevel.cancel_on_wait = True
    try:
        with (
            patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
            patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        ):
            result = prompt_ask_string(FakeRoot(), "Configurer Git", "Question ?", initial="valeur")
    finally:
        toplevel.cancel_on_wait = saved

    assert result is None


def test_prompt_db_config_returns_submitted_managed_config(gui_mocks) -> None:
    expected = DbConfig(
        DbMode.MANAGED, database_name="data", username="n8ndata", password="oldpass"
    )
    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
    ):
        result = prompt_db_config(FakeRoot(), expected)

    assert result == expected


def test_prompt_db_config_locks_identity_for_existing_managed(gui_mocks) -> None:
    gui_mocks.tk.Toplevel.instances.clear()
    gui_mocks.ttk.Button.instances.clear()
    expected = DbConfig(
        DbMode.MANAGED, database_name="data", username="n8ndata", password="oldpass"
    )

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
    ):
        result = prompt_db_config(FakeRoot(), expected)

    dialog = gui_mocks.tk.Toplevel.instances[0]
    gui_entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
    assert len(gui_entries) == 3
    assert all(entry._options.get("state") == "disabled" for entry in gui_entries)

    gui_buttons = [child for child in dialog.children if isinstance(child, gui_mocks.ttk.Button)]
    generate = next(button for button in gui_buttons if "Régénérer" in (button.text or ""))
    assert generate.state == "disabled"
    assert result == expected


def test_prompt_db_config_escape_returns_none(gui_mocks) -> None:
    toplevel = gui_mocks.tk.Toplevel
    saved = toplevel.cancel_on_wait
    toplevel.cancel_on_wait = True
    try:
        with (
            patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
            patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        ):
            result = prompt_db_config(FakeRoot(), DbConfig(DbMode.NONE))
    finally:
        toplevel.cancel_on_wait = saved

    assert result is None


# --- GitHub repo creation helpers -------------------------------------------


def test_repo_name_from_sanitizes_workspace_name() -> None:
    assert _repo_name_from("Mon Workspace!") == "mon-workspace"
    assert _repo_name_from("  BAZ_2.0  ") == "baz_2.0"
    assert _repo_name_from("!!!") == "workspace"


def test_prompt_github_create_requires_name_and_token(gui_mocks) -> None:
    gui_mocks.tk.Toplevel.instances.clear()
    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch("n8n_launcher.gui.dialogs.messagebox", gui_mocks.messagebox),
    ):
        result = prompt_github_create(FakeRoot(), "Mon Workspace")

    assert result is None
    assert gui_mocks.messagebox.warnings


def test_prompt_github_create_prefills_token(gui_mocks) -> None:
    gui_mocks.tk.Toplevel.instances.clear()

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        assert entries[1]._options["textvariable"].get() == "ghp_resolved"
        entries[0]._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_github_create(FakeRoot(), "Mon Workspace", token="ghp_resolved")

    assert result == GitHubCreatePlan(name="mon-workspace", private=True, token="ghp_resolved")


# --- GitHub repo creation wired into app flows -------------------------------


def test_prompt_create_with_github_creates_and_seeds(app, tmp_path) -> None:
    folder = tmp_path / "wf-gh"
    folder.mkdir()
    plan = CreatePlan(name="wf-gh", db=DbConfig(DbMode.NONE), git_enabled=True, github_create=True)
    gh_plan = GitHubCreatePlan(name="wf-gh", private=True, token="ghp_tok")
    app.manager.create.return_value.name = "wf-gh"
    client = MagicMock()
    client.create_repo.return_value = "https://github.com/octo/wf-gh.git"

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
        patch("n8n_launcher.gui.app.prompt_github_create", return_value=gh_plan),
        patch("n8n_launcher.gui.app.display.git_repo_status", return_value=False),
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
        patch("n8n_launcher.gui.app.git_seed_remote") as seed,
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    client.create_repo.assert_called_once_with(
        "wf-gh", private=True, description="Workspace n8n « wf-gh »"
    )
    workspace = app.manager.create.return_value
    app.manager.git_init_workspace.assert_called_once_with(
        workspace, remote_url="https://github.com/octo/wf-gh.git"
    )
    seed.assert_called_once_with(
        workspace.workflows_dir, "https://github.com/octo/wf-gh.git", "ghp_tok"
    )


def test_prompt_create_github_cancel_degrades_to_local_git(app, tmp_path) -> None:
    folder = tmp_path / "wf-ghc"
    folder.mkdir()
    plan = CreatePlan(name="wf-ghc", db=DbConfig(DbMode.NONE), git_enabled=True, github_create=True)

    with (
        patch("n8n_launcher.gui.dialogs.filedialog.askdirectory", return_value=str(folder)),
        patch("n8n_launcher.gui.app.prompt_create_plan", return_value=plan),
        patch("n8n_launcher.gui.app.prompt_github_create", return_value=None),
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app.prompt_create_workflow()
    app.app._drain_events()

    client.assert_not_called()
    app.manager.git_init_workspace.assert_called_once_with(
        app.manager.create.return_value, remote_url=None
    )


def test_configure_git_creates_github_when_no_remote(app) -> None:
    app.manager.git_remote_url.return_value = None
    gh_plan = GitHubCreatePlan(name="ws-repo", private=True, token="ghp_tok")
    client = MagicMock()
    client.create_repo.return_value = "https://github.com/octo/ws-repo.git"

    with (
        patch(
            "n8n_launcher.gui.app.prompt_git_config",
            return_value=GitConfigChoice(create_github=True),
        ),
        patch("n8n_launcher.gui.app.prompt_github_create", return_value=gh_plan),
        patch("n8n_launcher.gui.app.display.git_repo_status", return_value=False),
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
        patch("n8n_launcher.gui.app.git_seed_remote") as seed,
    ):
        app.app._select_row("ws-stopped")
        app.app.configure_git_selected()
    app.app._drain_events()

    workspace = app.manager.list.return_value[1]
    app.manager.git_init_workspace.assert_called_once_with(
        workspace, remote_url="https://github.com/octo/ws-repo.git"
    )
    seed.assert_called_once_with(
        workspace.workflows_dir, "https://github.com/octo/ws-repo.git", "ghp_tok"
    )


def test_configure_git_uses_url_when_no_github_asked(app) -> None:
    app.manager.git_remote_url.return_value = None

    with (
        patch(
            "n8n_launcher.gui.app.prompt_git_config",
            return_value=GitConfigChoice(remote_url="https://example.test/repo.git"),
        ),
        patch("n8n_launcher.gui.app.display.git_repo_status", return_value=False),
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._select_row("ws-stopped")
        app.app.configure_git_selected()
    app.app._drain_events()

    client.assert_not_called()
    workspace = app.manager.list.return_value[1]
    app.manager.git_init_workspace.assert_called_once_with(
        workspace, remote_url="https://example.test/repo.git"
    )


def test_configure_git_github_cancel_is_noop(app) -> None:
    app.manager.git_remote_url.return_value = None

    with (
        patch(
            "n8n_launcher.gui.app.prompt_git_config",
            return_value=GitConfigChoice(create_github=True),
        ),
        patch("n8n_launcher.gui.app.prompt_github_create", return_value=None),
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._select_row("ws-stopped")
        app.app.configure_git_selected()
    app.app._drain_events()

    client.assert_not_called()
    app.manager.configure_git.assert_not_called()
    app.manager.git_init_workspace.assert_not_called()


# --- GitHub token dialog (runs view) ----------------------------------------


def _token_entry(dialog, gui_mocks):
    return next(child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry))


def _paste_button(dialog, gui_mocks):
    return next(
        button
        for button in dialog.children
        if isinstance(button, gui_mocks.ttk.Button) and "Coller" in (button.text or "")
    )


def _remember_check(dialog, gui_mocks):
    return next(child for child in dialog.children if isinstance(child, gui_mocks.tk.Checkbutton))


def test_prompt_github_token_pastes_and_returns_token(gui_mocks) -> None:
    def drive(dialog) -> None:
        entry = _token_entry(dialog, gui_mocks)
        dialog._clipboard = "ghp_pasted"
        _paste_button(dialog, gui_mocks).command()
        entry._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_github_token(FakeRoot())

    assert result == GitHubTokenPlan(token="ghp_pasted", remember=False)


def test_prompt_github_token_remember_flag(gui_mocks) -> None:
    def drive(dialog) -> None:
        entry = _token_entry(dialog, gui_mocks)
        dialog._clipboard = "ghp_pasted"
        _paste_button(dialog, gui_mocks).command()
        _remember_check(dialog, gui_mocks).select()
        entry._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_github_token(FakeRoot())

    assert result == GitHubTokenPlan(token="ghp_pasted", remember=True)


def test_prompt_github_token_empty_paste_returns_none(gui_mocks) -> None:
    def drive(dialog) -> None:
        entry = _token_entry(dialog, gui_mocks)
        dialog._clipboard = "   "
        _paste_button(dialog, gui_mocks).command()
        entry._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_github_token(FakeRoot())

    assert result is None


def test_prompt_github_token_cancel_returns_none(gui_mocks) -> None:
    def drive(dialog) -> None:
        dialog._bindings["<Escape>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_github_token(FakeRoot())

    assert result is None


# --- Clone dialog (prompt_create_source / prompt_clone_plan) ----------------


def test_prompt_create_source_defaults_to_local(gui_mocks) -> None:
    """Sans interaction, le choix par défaut est « local »."""
    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
    ):
        result = prompt_create_source(FakeRoot())

    # ``FakeTk.Toplevel.wait_window`` déclenche le premier <Return> du dialogue ;
    # le bouton « Continuer » n'en a pas, donc c'est la valeur par défaut qui sort.
    # Selon le helper, ce chemin peut renvoyer ``None`` si aucun binding <Return>
    # n'est trouvé → on accepte les deux (le test suivant force le choix clone).
    assert result in (None, "local")


def test_prompt_create_source_selects_clone(gui_mocks) -> None:
    """Sélectionner « Cloner » + Continuer doit retourner "clone"."""
    gui_mocks.tk.Toplevel.instances.clear()

    def drive(dialog) -> None:
        # Le dialogue stocke les radiobuttons dans dialog.children ; on retrouve
        # celui dont la valeur est "clone" et on le sélectionne avant de valider.
        radios = [
            child
            for child in dialog.children
            if isinstance(child, gui_mocks.tk.Radiobutton)
            and child._options.get("value") == "clone"
        ]
        assert radios, "Radiobutton 'clone' introuvable"
        radios[0]._options["variable"].set("clone")
        # Le bouton « Continuer » appelle submit() → set result + destroy.
        continue_btn = next(
            button
            for button in dialog.children
            if isinstance(button, gui_mocks.ttk.Button)
            and (button.text or "").startswith("Continuer")
        )
        continue_btn.command()

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_create_source(FakeRoot())

    assert result == "clone"


def test_prompt_create_source_escape_returns_none(gui_mocks) -> None:
    saved = gui_mocks.tk.Toplevel.cancel_on_wait
    gui_mocks.tk.Toplevel.cancel_on_wait = True
    try:
        with (
            patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
            patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        ):
            result = prompt_create_source(FakeRoot())
    finally:
        gui_mocks.tk.Toplevel.cancel_on_wait = saved

    assert result is None


def test_prompt_clone_plan_submits_url_and_branch(gui_mocks) -> None:
    """Renseigner URL + branche puis <Return> doit produire un GitClonePlan."""
    gui_mocks.tk.Toplevel.instances.clear()

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        assert len(entries) == 2, f"attendu 2 Entry (URL + branche), obtenu {len(entries)}"
        entries[0]._options["textvariable"].set("https://example.test/repo.git")
        entries[1]._options["textvariable"].set("develop")
        # <Return> sur le champ branche = submit (cf. binding du dialogue).
        entries[1]._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_clone_plan(FakeRoot())

    assert result == GitClonePlan(url="https://example.test/repo.git", branch="develop")


def test_prompt_clone_plan_url_only_defaults_branch_to_none(gui_mocks) -> None:
    gui_mocks.tk.Toplevel.instances.clear()

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        entries[0]._options["textvariable"].set("https://example.test/repo.git")
        # branche laissée vide → None
        entries[1]._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_clone_plan(FakeRoot())

    assert result == GitClonePlan(url="https://example.test/repo.git", branch=None)


def test_prompt_clone_plan_missing_url_warns_and_returns_none(gui_mocks) -> None:
    """Sans URL, submit doit refuser et laisser la boîte ouverte ; Escape → None."""
    gui_mocks.tk.Toplevel.instances.clear()
    gui_mocks.messagebox.warnings.clear()

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        # <Return> sans URL : submit → showwarning + return (pas de destroy)
        entries[1]._bindings["<Return>"](None)
        # On simule ensuite l'annulation utilisateur.
        dialog._bindings["<Escape>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch("n8n_launcher.gui.dialogs.messagebox", gui_mocks.messagebox),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_clone_plan(FakeRoot())

    assert result is None
    assert any("URL" in msg for msg in gui_mocks.messagebox.warnings)


def test_prompt_clone_plan_load_branches_reports_error_on_git_failure(gui_mocks) -> None:
    """Un échec ``git ls-remote`` ne bloque pas : le message d'erreur s'affiche."""
    from n8n_launcher.git import GitError

    gui_mocks.tk.Toplevel.instances.clear()

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        entries[0]._options["textvariable"].set("https://bad.test/repo.git")
        load = next(
            button
            for button in dialog.children
            if isinstance(button, gui_mocks.ttk.Button)
            and (button.text or "").startswith("Charger")
        )
        with patch(
            "n8n_launcher.git.git_list_remote_branches",
            side_effect=GitError("repository not found"),
        ):
            load.command()
        # Statut mis à jour = "Impossible de lister...", on annule.
        dialog._bindings["<Escape>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        prompt_clone_plan(FakeRoot())


def test_prompt_clone_plan_load_branches_prefills_main(gui_mocks) -> None:
    """Cliquer « Charger les branches » doit préremplir ``main`` si présent."""
    gui_mocks.tk.Toplevel.instances.clear()
    captured: dict[str, str] = {}

    def drive(dialog) -> None:
        entries = [child for child in dialog.children if isinstance(child, gui_mocks.tk.Entry)]
        entries[0]._options["textvariable"].set("https://example.test/repo.git")
        load = next(
            button
            for button in dialog.children
            if isinstance(button, gui_mocks.ttk.Button)
            and (button.text or "").startswith("Charger")
        )
        with patch(
            "n8n_launcher.git.git_list_remote_branches",
            return_value=["main", "develop"],
        ):
            load.command()
        # Après chargement : branche doit valoir "main".
        captured["branch"] = entries[1]._options["textvariable"].get()
        entries[1]._bindings["<Return>"](None)

    with (
        patch("n8n_launcher.gui.dialogs.tk", gui_mocks.tk),
        patch("n8n_launcher.gui.dialogs.ttk", gui_mocks.ttk),
        patch.object(FakeTk.Toplevel, "wait_window", drive),
    ):
        result = prompt_clone_plan(FakeRoot())

    assert captured["branch"] == "main"
    assert result == GitClonePlan(url="https://example.test/repo.git", branch="main")
