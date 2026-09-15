from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from n8n_launcher.api_client import N8nApiError
from n8n_launcher.config import ConfigStore
from n8n_launcher.docker_manager import ComposeStatus, DockerError
from n8n_launcher.git_manager import GitError
from n8n_launcher.models import AppConfig, DbConfig, DbMode, GitConfig, WorkspaceState
from n8n_launcher.owner_setup import OwnerSetupError
from n8n_launcher.workspace_manager import WorkspaceError, WorkspaceManager


def manager(tmp_path: Path) -> tuple[WorkspaceManager, ConfigStore, MagicMock, MagicMock]:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    docker = MagicMock()
    booter = MagicMock(return_value="api-key-123")
    return WorkspaceManager(store, docker, owner_booter=booter), store, docker, booter


def create_none(launcher: WorkspaceManager, tmp_path: Path, port: int = 5680):
    return launcher.create(
        "Demo",
        tmp_path / "workflows",
        db=DbConfig(DbMode.NONE),
        port=port,
    )


def test_create_managed_workspace_when_migrations_exist(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workflows"
    (workspace_dir / "db" / "migrations").mkdir(parents=True)
    (workspace_dir / "db" / "migrations" / "001-init.sql").write_text("select 1;", encoding="utf-8")
    launcher, store, _, _ = manager(tmp_path)

    with patch("n8n_launcher.workspace_manager.suggest_port", return_value=5680):
        workspace = launcher.create("Demo", workspace_dir)

    assert workspace.db.mode is DbMode.MANAGED
    assert workspace.db.database_name == "data"
    assert workspace.db.username == "n8ndata"
    assert workspace.db.password
    assert workspace.port == 5680
    assert store.load().workspaces == [workspace]


def test_create_scaffolds_pipelines_and_db_layout(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workflows_dir = tmp_path / "workflows"

    launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.MANAGED))
    none_workspace = launcher.create("Plain", tmp_path / "plain", db=DbConfig(DbMode.NONE))

    assert (workflows_dir / "n8nPipelines").is_dir()
    assert (workflows_dir / "db" / "migrations").is_dir()
    assert (workflows_dir / "db" / "schema.sql").is_file()
    assert (tmp_path / "plain" / "n8nPipelines").is_dir()
    assert not (tmp_path / "plain" / "db").exists()
    assert none_workspace.db.mode is DbMode.NONE


def test_workspace_persists_postgres_image_through_config(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = launcher.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED))

    assert workspace.postgres_image is None

    config = store.load()
    config.workspaces[0].postgres_image = "custom/pg:16"
    store.save(config)

    restored = store.load().workspaces[0]
    assert restored.postgres_image == "custom/pg:16"


def test_create_without_db_defaults_to_none(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)

    workspace = launcher.create("Demo", tmp_path / "workflows")

    assert workspace.db.mode is DbMode.NONE


def test_start_and_stop_update_state(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        started = launcher.start(workspace.id)
        stopped = launcher.stop(workspace.id)

    assert started.state is WorkspaceState.RUNNING
    assert stopped.state is WorkspaceState.STOPPED
    docker.up.assert_called_once()
    docker.down.assert_called_once()


def test_stop_skips_down_when_never_launched(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "missing.yml"):
        stopped = launcher.stop(workspace.id)

    assert stopped.state is WorkspaceState.STOPPED
    docker.down.assert_not_called()


def test_stop_is_idempotent_on_already_stopped(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        launcher.start(workspace.id)
        launcher.stop(workspace.id)
        # Second pass = the atexit stop_all() after a GUI close.
        stopped = launcher.stop(workspace.id)

    assert stopped.state is WorkspaceState.STOPPED
    docker.up.assert_called_once()
    docker.down.assert_called_once()


def test_live_state_reports_running_when_n8n_up(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(
        raw_output='{"Service":"postgres","State":"running"}\n{"Service":"n8n","State":"running"}\n',
        returncode=0,
    )

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.RUNNING


def test_live_state_reports_stopped_when_project_down(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(raw_output="", returncode=0)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.STOPPED


def test_live_state_falls_back_to_stored_on_docker_error(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    workspace.state = WorkspaceState.RUNNING
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.side_effect = DockerError("boom")

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.RUNNING


def test_live_state_uses_stored_when_compose_missing(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "missing.yml"):
        assert launcher.live_state(workspace) is WorkspaceState.STOPPED

    docker.status.assert_not_called()


def test_reconcile_all_persists_live_states(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(
        raw_output='{"Service":"postgres","State":"running"}\n{"Service":"n8n","State":"running"}\n',
        returncode=0,
    )

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        changed = launcher.reconcile_all()

    assert changed == 1
    assert store.load().workspaces[0].state is WorkspaceState.RUNNING


def test_reconcile_all_is_idempotent_on_stopped(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(raw_output="", returncode=0)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=compose):
        changed = launcher.reconcile_all()

    assert changed == 0
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_start_managed_applies_migrations(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workflows_dir = tmp_path / "workflows"
    (workflows_dir / "db" / "migrations").mkdir(parents=True)
    (workflows_dir / "db" / "migrations" / "001-init.sql").write_text("select 1;", encoding="utf-8")
    workspace = launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.MANAGED))

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        started = launcher.start(workspace.id)

    assert started.state is WorkspaceState.RUNNING
    docker.up.assert_called_once()
    assert docker.exec_psql.called
    stored = store.load().workspaces[0]
    assert stored.db.password


def test_ensure_running_starts_stopped_and_bootstraps_owner(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    docker = MagicMock()
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store, docker, owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workspace = create_none(launcher, tmp_path)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        launcher.ensure_running(workspace.id)

    docker.up.assert_called_once()
    booter.assert_called_once_with("http://127.0.0.1:5680", "owner@example.test", "secret")
    stored = store.load().workspaces[0]
    assert stored.state is WorkspaceState.RUNNING
    assert stored.api_key == "api-key-123"
    fake_api.ensure_postgres_credential.assert_not_called()


def test_ensure_running_skips_bootstrap_when_key_present(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    docker = MagicMock()
    booter = MagicMock()
    launcher = WorkspaceManager(
        store, docker, owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].api_key = "already-configured"
    store.save(config)

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        launcher.ensure_running(workspace.id)

    docker.up.assert_called_once()
    booter.assert_not_called()
    fake_api.ensure_postgres_credential.assert_not_called()


def test_ensure_running_running_without_key_only_bootstraps(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    docker = MagicMock()
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store, docker, owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].state = WorkspaceState.RUNNING
    store.save(config)

    launcher.ensure_running(workspace.id)

    docker.up.assert_not_called()
    booter.assert_called_once_with("http://127.0.0.1:5680", "owner@example.test", "secret")
    assert store.load().workspaces[0].api_key == "api-key-123"


def test_ensure_running_skips_credentials_without_database(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store,
        MagicMock(),
        owner_booter=booter,
        api_factory=MagicMock(return_value=fake_api),
    )
    workspace = launcher.create("Plain", tmp_path / "plain", db=DbConfig(DbMode.NONE))

    launcher.ensure_running(workspace.id)

    fake_api.ensure_postgres_credential.assert_not_called()


def test_ensure_running_rotates_key_on_forbidden_scope(tmp_path: Path) -> None:
    for status_code, label in [(403, "forbidden"), (401, "unauthorized")]:
        forbidden = MagicMock()
        forbidden.list_workflows.return_value = []
        forbidden.ensure_postgres_credential.side_effect = [
            N8nApiError(label, status_code=status_code),
            None,
        ]
        store = ConfigStore(tmp_path / "config.json")
        store.save(AppConfig("owner@example.test", "secret", tmp_path))
        docker = MagicMock()
        booter = MagicMock(return_value="rotated-key")
        launcher = WorkspaceManager(
            store, docker, owner_booter=booter, api_factory=MagicMock(return_value=forbidden)
        )
        workspace = launcher.create(
            "Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED)
        )
        config = store.load()
        config.workspaces[0].api_key = "old-key"
        store.save(config)

        with patch(
            "n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"
        ):
            launcher.ensure_running(workspace.id)

        assert booter.call_count == 1
        assert store.load().workspaces[0].api_key == "rotated-key"
        assert forbidden.ensure_postgres_credential.call_count == 2


def test_ensure_running_imports_folder_workflows(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store, MagicMock(), owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workflows_dir = tmp_path / "workflows"
    pipelines_dir = workflows_dir / "n8nPipelines"
    pipelines_dir.mkdir(parents=True)
    (pipelines_dir / "Meteo.json").write_text(
        '{"id": "x", "name": "Meteo", "nodes": [], "connections": {}, "settings": {}}',
        encoding="utf-8",
    )
    workspace = launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.NONE))

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        launcher.ensure_running(workspace.id)

    fake_api.create_workflow.assert_called_once_with(
        {"name": "Meteo", "nodes": [], "connections": {}, "settings": {}}
    )


def test_ensure_running_propagates_bootstrap_error(tmp_path: Path) -> None:
    launcher, _, docker, booter = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    booter.side_effect = OwnerSetupError("boom")

    with patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"):
        with pytest.raises(OwnerSetupError, match="boom"):
            launcher.ensure_running(workspace.id)

    docker.up.assert_called_once()


def test_ensure_running_unknown_workspace(tmp_path: Path) -> None:
    launcher, _, _, booter = manager(tmp_path)

    with pytest.raises(WorkspaceError, match="Unknown workspace"):
        launcher.ensure_running("nope")

    booter.assert_not_called()


def test_workspace_serialization_roundtrip_includes_api_key(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path, port=5700)
    config = store.load()
    config.workspaces[0].api_key = "sekret-1"
    store.save(config)

    assert workspace.port == 5700
    loaded = store.load().workspaces[0]
    assert loaded.api_key == "sekret-1"
    assert loaded.port == 5700
    assert loaded.db.mode is DbMode.NONE


def test_sync_git_skips_when_git_disabled(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspace_manager.git_add") as add,
        patch("n8n_launcher.workspace_manager.git_commit", return_value=True) as commit,
    ):
        launcher.sync_git(workspace)

    add.assert_not_called()
    commit.assert_not_called()


def test_sync_git_commits_and_pushes(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True, remote_url="https://example.test/repo.git")
    store.save(config)
    workspace.git = GitConfig(enabled=True)

    with (
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=True) as is_repo,
        patch("n8n_launcher.workspace_manager.git_add") as add,
        patch("n8n_launcher.workspace_manager.git_commit", return_value=True) as commit,
        patch("n8n_launcher.workspace_manager.git_has_unpushed_commits", return_value=False),
        patch("n8n_launcher.workspace_manager.git_push") as push,
    ):
        launcher.sync_git(workspace)

    is_repo.assert_called_once()
    add.assert_called_once_with(workspace.workflows_dir)
    commit.assert_called_once()
    push.assert_called_once()


def test_sync_git_pushes_only_when_committed_or_unpushed(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)

    with (
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspace_manager.git_add"),
        patch("n8n_launcher.workspace_manager.git_commit", return_value=False),
        patch("n8n_launcher.workspace_manager.git_has_unpushed_commits", return_value=False),
        patch("n8n_launcher.workspace_manager.git_push") as push,
    ):
        launcher.sync_git(workspace)

    push.assert_not_called()


def test_sync_git_swallows_push_failure(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)

    with (
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspace_manager.git_add"),
        patch("n8n_launcher.workspace_manager.git_commit", return_value=True),
        patch("n8n_launcher.workspace_manager.git_has_unpushed_commits", return_value=True),
        patch("n8n_launcher.workspace_manager.git_push", side_effect=GitError("boom")),
    ):
        launcher.sync_git(workspace)  # must not raise


def test_git_init_workspace_initializes_and_persists_remote(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=False),
        patch("n8n_launcher.workspace_manager.git_init") as init,
        patch("n8n_launcher.workspace_manager.git_has_remote", return_value=False),
        patch("n8n_launcher.workspace_manager.git_add_remote") as add_remote,
    ):
        launcher.git_init_workspace(workspace, remote_url="https://example.test/repo.git")

    init.assert_called_once_with(workspace.workflows_dir)
    add_remote.assert_called_once_with(workspace.workflows_dir, "origin", "https://example.test/repo.git")
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url == "https://example.test/repo.git"


def test_git_init_workspace_reuses_existing_repo(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=True) as is_repo,
        patch("n8n_launcher.workspace_manager.git_init") as init,
        patch("n8n_launcher.workspace_manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspace_manager.git_set_remote_url") as set_url,
    ):
        launcher.git_init_workspace(workspace, remote_url="https://example.test/repo.git")

    is_repo.assert_called_once()
    init.assert_not_called()
    set_url.assert_called_once_with(workspace.workflows_dir, "origin", "https://example.test/repo.git")
    assert store.load().workspaces[0].git.enabled is True


def test_configure_git_sets_remote_url(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspace_manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspace_manager.git_set_remote_url") as set_url,
    ):
        launcher.configure_git(workspace, remote_url="https://example.test/other.git")

    set_url.assert_called_once_with(workspace.workflows_dir, "origin", "https://example.test/other.git")
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url == "https://example.test/other.git"


def test_ensure_running_pulls_before_import_when_git_enabled(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store, MagicMock(), owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workflows_dir = tmp_path / "workflows"
    pipelines_dir = workflows_dir / "n8nPipelines"
    pipelines_dir.mkdir(parents=True)
    workspace = launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.NONE))
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True, remote_url="https://example.test/repo.git")
    store.save(config)

    with (
        patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"),
        patch("n8n_launcher.workspace_manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspace_manager.git_pull") as pull,
    ):
        launcher.ensure_running(workspace.id)

    pull.assert_called_once_with(workflows_dir)


def test_ensure_running_skips_pull_when_git_disabled(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    booter = MagicMock(return_value="api-key-123")
    launcher = WorkspaceManager(
        store, MagicMock(), owner_booter=booter, api_factory=MagicMock(return_value=fake_api)
    )
    workflows_dir = tmp_path / "workflows"
    (workflows_dir / "n8nPipelines").mkdir(parents=True)
    launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.NONE))

    with (
        patch("n8n_launcher.workspace_manager.compose_file", return_value=tmp_path / "compose.yml"),
        patch("n8n_launcher.workspace_manager.git_pull") as pull,
    ):
        launcher.ensure_running(launcher.list()[0].id)

    pull.assert_not_called()
