import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import (
    AppConfig,
    DbConfig,
    DbMode,
    GitConfig,
    ServerConfig,
    Workspace,
    WorkspaceState,
)
from n8n_launcher.docker.manager import ComposeStatus, DockerError
from n8n_launcher.git import GitError, workspace_branch
from n8n_launcher.n8n.api import N8nApiError
from n8n_launcher.n8n.owner import OwnerSetupError
from n8n_launcher.remote.ssh import SshError
from n8n_launcher.workspaces.manager import WorkspaceError, WorkspaceManager


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

    with patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5680):
        workspace = launcher.create("Demo", workspace_dir)

    assert workspace.db.mode is DbMode.MANAGED
    assert workspace.db.database_name == "data"
    assert workspace.db.username == "n8ndata"
    assert workspace.db.password
    assert workspace.port == 5680
    assert store.load().workspaces == [workspace]


def test_create_preserves_provided_managed_db_fields(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workflows_dir = tmp_path / "workflows"
    provided = DbConfig(
        mode=DbMode.MANAGED,
        database_name="my-data",
        username="my-user",
        password="my-pass",
    )

    workspace = launcher.create("Demo", workflows_dir, db=provided)

    assert workspace.db.mode is DbMode.MANAGED
    assert workspace.db.database_name == "my-data"
    assert workspace.db.username == "my-user"
    assert workspace.db.password == "my-pass"


def test_create_generates_password_only_for_missing_managed_fields(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workflows_dir = tmp_path / "workflows"
    provided = DbConfig(mode=DbMode.MANAGED, database_name="my-data", username="my-user")

    workspace = launcher.create("Demo", workflows_dir, db=provided)

    assert workspace.db.database_name == "my-data"
    assert workspace.db.username == "my-user"
    assert workspace.db.password


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
    launcher, _store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
        started = launcher.start(workspace.id)
        stopped = launcher.stop(workspace.id)

    assert started.state is WorkspaceState.RUNNING
    assert stopped.state is WorkspaceState.STOPPED
    docker.up.assert_called_once()
    docker.down.assert_called_once()


def test_stop_skips_down_when_never_launched(tmp_path: Path) -> None:
    launcher, _store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "missing.yml"
    ):
        stopped = launcher.stop(workspace.id)

    assert stopped.state is WorkspaceState.STOPPED
    docker.down.assert_not_called()


def test_stop_transitions_through_stopping_while_tearing_down(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    def check_state_on_down(*_args, **_kwargs) -> None:
        assert store.load().workspaces[0].state is WorkspaceState.STOPPING

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        launcher.start(workspace.id)
        with patch.object(docker, "down", side_effect=check_state_on_down):
            stopped = launcher.stop(workspace.id)

    assert stopped.state is WorkspaceState.STOPPED
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_stop_is_idempotent_on_already_stopped(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
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

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.RUNNING


def test_live_state_reports_stopped_when_project_down(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(raw_output="", returncode=0)

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.STOPPED


def test_live_state_falls_back_to_stored_on_docker_error(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    workspace.state = WorkspaceState.RUNNING
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.side_effect = DockerError("boom")

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        assert launcher.live_state(workspace) is WorkspaceState.RUNNING


def test_live_state_uses_stored_when_compose_missing(tmp_path: Path) -> None:
    launcher, _, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "missing.yml"
    ):
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

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        changed = launcher.reconcile_all()

    assert changed == 1
    assert store.load().workspaces[0].state is WorkspaceState.RUNNING


def test_reconcile_all_is_idempotent_on_stopped(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    docker.status.return_value = ComposeStatus(raw_output="", returncode=0)

    with patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose):
        changed = launcher.reconcile_all()

    assert changed == 0
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_start_managed_applies_migrations(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workflows_dir = tmp_path / "workflows"
    (workflows_dir / "db" / "migrations").mkdir(parents=True)
    (workflows_dir / "db" / "migrations" / "001-init.sql").write_text("select 1;", encoding="utf-8")
    workspace = launcher.create("Demo", workflows_dir, db=DbConfig(DbMode.MANAGED))

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
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

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
        launcher.ensure_running(workspace.id)

    docker.up.assert_called_once()
    booter.assert_called_once_with("http://127.0.0.1:5680", "owner@example.test", "secret")
    stored = store.load().workspaces[0]
    assert stored.state is WorkspaceState.RUNNING
    assert stored.api_key == "api-key-123"
    fake_api.ensure_postgres_credential.assert_not_called()


def test_ensure_running_calls_on_ready_with_port_after_start(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_workflows.return_value = []
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    docker = MagicMock()
    launcher = WorkspaceManager(
        store,
        docker,
        owner_booter=MagicMock(return_value="api-key-123"),
        api_factory=MagicMock(return_value=fake_api),
    )
    workspace = create_none(launcher, tmp_path)

    ready_call = []
    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
        launcher.ensure_running(workspace.id, on_ready=lambda port: ready_call.append(port))

    docker.up.assert_called_once()
    assert ready_call == [workspace.port]


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

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
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

    # The compose file must land in tmp, never in the real config directory:
    # without this patch start() writes a compose.yml under
    # ~/.config/n8n-launcher/workspaces/<id>/ (the manager uses the global
    # paths by default), leaving pytest artifacts in the user's config.
    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
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
        workspace = launcher.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED))
        config = store.load()
        config.workspaces[0].api_key = "old-key"
        store.save(config)

        with patch(
            "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
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

    with patch(
        "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
    ):
        launcher.ensure_running(workspace.id)

    fake_api.create_workflow.assert_called_once_with(
        {"name": "Meteo", "nodes": [], "connections": {}, "settings": {}}
    )


def test_ensure_running_propagates_bootstrap_error(tmp_path: Path) -> None:
    launcher, _, docker, booter = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    booter.side_effect = OwnerSetupError("boom")

    with (
        patch(
            "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
        ),
        pytest.raises(OwnerSetupError, match="boom"),
    ):
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
        patch("n8n_launcher.workspaces.manager.git_add") as add,
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True) as commit,
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
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True) as is_repo,
        patch("n8n_launcher.workspaces.manager.git_add") as add,
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True) as commit,
        patch("n8n_launcher.workspaces.manager.git_has_unpushed_commits", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_push") as push,
    ):
        launcher.sync_git(workspace)

    assert is_repo.call_count == 2
    add.assert_called_once_with(workspace.workflows_dir)
    commit.assert_called_once()
    push.assert_called_once()
    assert store.load().workspaces[0].git_push_failed is False


def test_sync_git_pushes_only_when_committed_or_unpushed(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_has_unpushed_commits", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_push") as push,
    ):
        launcher.sync_git(workspace)

    push.assert_not_called()


def test_sync_git_swallows_push_failure_and_flags_workspace(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)
    workspace.git = GitConfig(enabled=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_has_unpushed_commits", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_push", side_effect=GitError("boom")),
    ):
        launcher.sync_git(workspace)  # must not raise

    assert workspace.git_push_failed is True
    assert store.load().workspaces[0].git_push_failed is True


def test_sync_git_swallows_stage_failure_and_flags_workspace(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)
    workspace.git = GitConfig(enabled=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add", side_effect=GitError("stage boom")),
        patch("n8n_launcher.workspaces.manager.git_commit") as commit,
        patch("n8n_launcher.workspaces.manager.git_push") as push,
    ):
        launcher.sync_git(workspace)  # must not raise and must not block close

    commit.assert_not_called()
    push.assert_not_called()
    assert workspace.git_push_failed is True
    assert store.load().workspaces[0].git_push_failed is True


def test_sync_git_swallows_commit_failure_and_flags_workspace(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    store.save(config)
    workspace.git = GitConfig(enabled=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch(
            "n8n_launcher.workspaces.manager.git_commit",
            side_effect=GitError("commit boom"),
        ),
        patch("n8n_launcher.workspaces.manager.git_push") as push,
    ):
        launcher.sync_git(workspace)  # must not raise and must not block close

    push.assert_not_called()
    assert workspace.git_push_failed is True
    assert store.load().workspaces[0].git_push_failed is True


def test_sync_git_clears_push_failed_after_success(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    config.workspaces[0].git_push_failed = True
    store.save(config)
    workspace.git = GitConfig(enabled=True)
    workspace.git_push_failed = True

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_has_unpushed_commits", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_push"),
    ):
        launcher.sync_git(workspace)

    assert workspace.git_push_failed is False
    assert store.load().workspaces[0].git_push_failed is False


def _running_git_workspace(launcher, store, tmp_path: Path) -> Workspace:
    """Persist a workspace as RUNNING with an API key and git enabled."""
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True)
    config.workspaces[0].state = WorkspaceState.RUNNING
    config.workspaces[0].api_key = "key-1"
    store.save(config)
    return launcher.list()[0]


def test_stop_with_sync_exports_and_syncs_before_stop(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = _running_git_workspace(launcher, store, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.SyncRunner") as run,
        patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose),
        patch.object(launcher, "sync_git") as sync,
    ):
        launcher.stop_with_sync(workspace.id)

    run.return_value.export_all.assert_called_once_with(mirror=workspace.workflows_dir)
    sync.assert_called_once()
    assert sync.call_args.args[0].id == workspace.id
    assert sync.call_args.kwargs == {"push": True}
    docker.down.assert_called_once()
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_stop_with_sync_export_failure_does_not_block_stop(tmp_path: Path) -> None:
    launcher, store, docker, _ = manager(tmp_path)
    workspace = _running_git_workspace(launcher, store, tmp_path)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.SyncRunner") as run,
        patch("n8n_launcher.workspaces.manager.compose_file", return_value=compose),
        patch.object(launcher, "sync_git") as sync,
    ):
        run.return_value.export_all.side_effect = RuntimeError("sync boom")
        launcher.stop_with_sync(workspace.id)  # must not raise

    sync.assert_called_once()
    docker.down.assert_called_once()
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_stop_with_sync_skips_export_when_not_running(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.SyncRunner") as run,
        patch.object(launcher, "sync_git") as sync,
    ):
        launcher.stop_with_sync(workspace.id)

    run.assert_not_called()
    sync.assert_not_called()
    assert store.load().workspaces[0].state is WorkspaceState.STOPPED


def test_git_init_workspace_initializes_and_persists_remote(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git_push_failed = True
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_init") as init,
        patch("n8n_launcher.workspaces.manager.ensure_gitignore") as ensure_ignore,
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_add_remote") as add_remote,
    ):
        launcher.git_init_workspace(workspace, remote_url="https://example.test/repo.git")

    init.assert_called_once_with(workspace.workflows_dir, branch=workspace_branch(workspace.id))
    ensure_ignore.assert_called_once_with(workspace.workflows_dir)
    add_remote.assert_called_once_with(
        workspace.workflows_dir, "origin", "https://example.test/repo.git"
    )
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url == "https://example.test/repo.git"
    assert stored.git_push_failed is False


def test_git_init_workspace_detaches_remote_when_url_cleared(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True) as is_repo,
        patch("n8n_launcher.workspaces.manager.git_init") as init,
        patch("n8n_launcher.workspaces.manager.ensure_gitignore"),
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_remove_remote") as remove_remote,
    ):
        launcher.git_init_workspace(workspace, remote_url=None)

    is_repo.assert_called_once()
    init.assert_not_called()
    remove_remote.assert_called_once_with(workspace.workflows_dir, "origin")
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url is None


def test_git_init_workspace_reuses_existing_repo(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True) as is_repo,
        patch("n8n_launcher.workspaces.manager.git_init") as init,
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_set_remote_url") as set_url,
    ):
        launcher.git_init_workspace(workspace, remote_url="https://example.test/repo.git")

    is_repo.assert_called_once()
    init.assert_not_called()
    set_url.assert_called_once_with(
        workspace.workflows_dir, "origin", "https://example.test/repo.git"
    )
    assert store.load().workspaces[0].git.enabled is True


def test_configure_git_sets_remote_url(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git_push_failed = True
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.ensure_gitignore"),
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_set_remote_url") as set_url,
    ):
        launcher.configure_git(workspace, remote_url="https://example.test/other.git")

    set_url.assert_called_once_with(
        workspace.workflows_dir, "origin", "https://example.test/other.git"
    )
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url == "https://example.test/other.git"
    assert stored.git_push_failed is False


def test_configure_git_detaches_remote_when_url_cleared(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True, remote_url="https://example.test/repo.git")
    config.workspaces[0].git_push_failed = True
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.ensure_gitignore"),
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_remove_remote") as remove_remote,
    ):
        launcher.configure_git(workspace, remote_url=None)

    remove_remote.assert_called_once_with(workspace.workflows_dir, "origin")
    stored = store.load().workspaces[0]
    assert stored.git.enabled is True
    assert stored.git.remote_url is None
    assert stored.git_push_failed is False


def test_git_init_workspace_preserves_ci_metadata(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(
        ci_enabled=True,
        ci_credentials=[{"name": "API", "type": "httpRequest"}],
    )
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_init"),
        patch("n8n_launcher.workspaces.manager.ensure_gitignore"),
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=False),
        patch("n8n_launcher.workspaces.manager.git_add_remote"),
    ):
        launcher.git_init_workspace(workspace, remote_url="https://example.test/repo.git")

    stored = store.load().workspaces[0]
    assert stored.git.ci_enabled is True
    assert stored.git.ci_credentials == [{"name": "API", "type": "httpRequest"}]
    assert stored.git.remote_url == "https://example.test/repo.git"


def test_configure_git_preserves_ci_metadata(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(
        enabled=True,
        ci_enabled=True,
        ci_credentials=[{"name": "API", "type": "httpRequest"}],
    )
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.ensure_gitignore"),
        patch("n8n_launcher.workspaces.manager.git_has_remote", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_set_remote_url"),
    ):
        launcher.configure_git(workspace, remote_url="https://example.test/other.git")

    stored = store.load().workspaces[0]
    assert stored.git.ci_enabled is True
    assert stored.git.ci_credentials == [{"name": "API", "type": "httpRequest"}]
    assert stored.git.remote_url == "https://example.test/other.git"


def test_configure_db_switches_to_managed_and_scaffolds(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    assert not (workspace.workflows_dir / "db").exists()

    updated = launcher.configure_db(
        workspace, DbConfig(DbMode.MANAGED, database_name="data", username="n8ndata", password="pw")
    )

    assert (workspace.workflows_dir / "db" / "migrations").is_dir()
    assert (workspace.workflows_dir / "db" / "schema.sql").is_file()
    stored = store.load().workspaces[0]
    assert stored.db.mode is DbMode.MANAGED
    assert stored.db.database_name == "data"
    assert stored.db.password == "pw"
    assert updated.restart_required is True


def test_configure_db_fills_missing_managed_defaults(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    launcher.configure_db(workspace, DbConfig(DbMode.MANAGED))

    stored = store.load().workspaces[0]
    assert stored.db.database_name == "data"
    assert stored.db.username == "n8ndata"
    assert len(stored.db.password) >= 16


def test_configure_db_disables_managed(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = launcher.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED))

    updated = launcher.configure_db(workspace, DbConfig(DbMode.NONE))

    assert store.load().workspaces[0].db.mode is DbMode.NONE
    assert updated.restart_required is True


def test_configure_db_keeps_identity_of_existing_managed_db(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = launcher.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED))
    original = store.load().workspaces[0].db

    # A new password/name/user on an already-started managed DB would orphan
    # the Postgres role and the n8n credential — the values must stay put.
    updated = launcher.configure_db(
        workspace,
        DbConfig(DbMode.MANAGED, database_name="other", username="other", password="hacked-pass"),
    )

    stored = store.load().workspaces[0].db
    assert stored.database_name == original.database_name
    assert stored.username == original.username
    assert stored.password == original.password
    assert updated.db == stored


def test_configure_db_managed_noop_when_identity_unchanged(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = launcher.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.MANAGED))
    original = store.load().workspaces[0]

    # Re-saving the same identity must not flip restart_required.
    updated = launcher.configure_db(
        workspace,
        DbConfig(
            DbMode.MANAGED,
            database_name=original.db.database_name or "data",
            username=original.db.username or "n8ndata",
            password=original.db.password,
        ),
    )

    assert updated.state is original.state
    assert updated.restart_required is False
    assert store.load().workspaces[0].db == original.db


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
        patch(
            "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
        ),
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_pull") as pull,
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
        patch(
            "n8n_launcher.workspaces.manager.compose_file", return_value=tmp_path / "compose.yml"
        ),
        patch("n8n_launcher.workspaces.manager.git_pull") as pull,
    ):
        launcher.ensure_running(launcher.list()[0].id)

    pull.assert_not_called()


def github_workspace(launcher, store, tmp_path: Path, port: int = 5701) -> Workspace:
    """Create a workspace persisted with git + GitHub remote enabled."""
    workspace = create_none(launcher, tmp_path, port=port)
    config = store.load()
    config.workspaces[0].git = GitConfig(
        enabled=True, remote_url="https://github.com/owner/repo.git"
    )
    store.save(config)
    return launcher.list()[0]


def test_enable_ci_writes_harness_and_commits(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = github_workspace(launcher, store, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch(
            "n8n_launcher.workspaces.manager.git_remote_url",
            return_value="https://github.com/owner/repo.git",
        ),
        patch.object(launcher, "_commit_and_push") as commit,
    ):
        enabled = launcher.enable_ci(workspace)

    assert enabled.git.ci_enabled is True
    assert (workspace.workflows_dir / ".github" / "workflows" / "n8n-ci.yml").is_file()
    assert (workspace.workflows_dir / ".n8n-tests" / "runner.py").is_file()
    assert (workspace.workflows_dir / ".n8n-tests" / "validate.py").is_file()
    assert (workspace.workflows_dir / ".n8n-tests" / "tests.json").is_file()
    workflow = (workspace.workflows_dir / ".github" / "workflows" / "n8n-ci.yml").read_text(
        encoding="utf-8"
    )
    assert "n8n-launcher : généré" in workflow
    assert "N8N_CI_CREDENTIALS" in workflow
    commit.assert_called_once()
    assert store.load().workspaces[0].git.ci_enabled is True


def test_enable_ci_requires_git_repo(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=False),
        patch.object(launcher, "_commit_and_push") as commit,
        pytest.raises(WorkspaceError, match="dépôt Git"),
    ):
        launcher.enable_ci(workspace)

    commit.assert_not_called()
    assert store.load().workspaces[0].git.ci_enabled is False


def test_enable_ci_requires_github_remote(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].git = GitConfig(enabled=True, remote_url="git@gitlab.com:u/r.git")
    store.save(config)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch(
            "n8n_launcher.workspaces.manager.git_remote_url", return_value="git@gitlab.com:u/r.git"
        ),
        patch.object(launcher, "_commit_and_push") as commit,
        pytest.raises(WorkspaceError, match="GitHub"),
    ):
        launcher.enable_ci(workspace)

    commit.assert_not_called()


def test_enable_ci_preserves_existing_selection(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = github_workspace(launcher, store, tmp_path)
    selection = workspace.workflows_dir / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"selected": ["n8nPipelines/keep.json"]}', encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch(
            "n8n_launcher.workspaces.manager.git_remote_url",
            return_value="https://github.com/owner/repo.git",
        ),
        patch.object(launcher, "_commit_and_push"),
    ):
        launcher.enable_ci(workspace)

    assert '"n8nPipelines/keep.json"' in selection.read_text(encoding="utf-8")


def test_disable_ci_removes_harness_but_keeps_selection(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = github_workspace(launcher, store, tmp_path)
    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch(
            "n8n_launcher.workspaces.manager.git_remote_url",
            return_value="https://github.com/owner/repo.git",
        ),
        patch.object(launcher, "_commit_and_push"),
    ):
        launcher.enable_ci(workspace)
    selection = workspace.workflows_dir / ".n8n-tests" / "tests.json"
    selection.write_text('{"selected": ["n8nPipelines/keep.json"]}', encoding="utf-8")

    with patch.object(launcher, "_commit_and_push") as commit:
        disabled = launcher.disable_ci(launcher.list()[0])

    assert disabled.git.ci_enabled is False
    assert not (workspace.workflows_dir / ".github" / "workflows" / "n8n-ci.yml").exists()
    assert not (workspace.workflows_dir / ".n8n-tests" / "runner.py").exists()
    assert not (workspace.workflows_dir / ".n8n-tests" / "validate.py").exists()
    assert selection.exists()
    commit.assert_called_once()


def test_save_ci_selection_persists_without_push(tmp_path: Path) -> None:
    launcher, _store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch.object(launcher, "_commit_and_push") as commit:
        updated = launcher.save_ci_selection(workspace, {"n8nPipelines/a.json"})

    selection = (workspace.workflows_dir / ".n8n-tests" / "tests.json").read_text(encoding="utf-8")
    assert '"n8nPipelines/a.json"' in selection
    commit.assert_not_called()
    assert updated is not None


def test_save_ci_selection_pushes_when_requested(tmp_path: Path) -> None:
    launcher, _store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with patch.object(launcher, "_commit_and_push") as commit:
        launcher.save_ci_selection(workspace, {"n8nPipelines/a.json"}, push=True)

    commit.assert_called_once_with(
        workspace, "n8n-launcher: mettre à jour les tests GitHub Actions"
    )


def test_set_ci_credentials_records_metadata_only(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    launcher.set_ci_credentials(
        workspace, [{"name": "API", "type": "httpRequest", "data": {"password": "sekret"}}]
    )

    stored = store.load().workspaces[0]
    assert stored.git.ci_credentials == [{"name": "API", "type": "httpRequest"}]
    assert "sekret" not in str(stored.git.ci_credentials)


def test_ci_credentials_payload_reads_values_from_api(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_credentials.return_value = [{"id": "c1", "name": "API", "type": "httpRequest"}]
    fake_api.get_credential.return_value = {
        "id": "c1",
        "name": "API",
        "type": "httpRequest",
        "data": {"user": "u", "password": "p"},
    }
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    launcher = WorkspaceManager(store, MagicMock(), api_factory=MagicMock(return_value=fake_api))
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].api_key = "key"
    store.save(config)

    payload = launcher.ci_credentials_payload(
        launcher.list()[0], [{"name": "API", "type": "httpRequest"}]
    )

    assert json.loads(payload) == [
        {"name": "API", "type": "httpRequest", "data": {"user": "u", "password": "p"}}
    ]


def test_ci_credentials_payload_requires_api_key(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with pytest.raises(WorkspaceError, match="démarré"):
        launcher.ci_credentials_payload(workspace, [{"name": "API", "type": "httpRequest"}])


def test_ci_credentials_payload_reports_missing(tmp_path: Path) -> None:
    fake_api = MagicMock()
    fake_api.list_credentials.return_value = [{"id": "c1", "name": "Other", "type": "postgres"}]
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    launcher = WorkspaceManager(store, MagicMock(), api_factory=MagicMock(return_value=fake_api))
    workspace = create_none(launcher, tmp_path)
    config = store.load()
    config.workspaces[0].api_key = "key"
    store.save(config)

    with pytest.raises(WorkspaceError, match="introuvables"):
        launcher.ci_credentials_payload(
            launcher.list()[0], [{"name": "API", "type": "httpRequest"}]
        )


def test_github_token_defaults_to_none(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)

    assert launcher.github_token() is None


def test_set_github_token_persists_and_clears(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)

    launcher.set_github_token("ghp_remembered")
    assert launcher.github_token() == "ghp_remembered"
    assert store.load().github_token == "ghp_remembered"

    launcher.set_github_token(None)
    assert launcher.github_token() is None
    assert store.load().github_token is None


def test_set_github_token_treats_empty_string_as_clear(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)

    launcher.set_github_token("ghp_remembered")
    launcher.set_github_token("")

    assert store.load().github_token is None


def test_clone_from_git_clones_and_registers(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    dest = tmp_path / "repo-clone"

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "n8nPipelines").mkdir(exist_ok=True)
        (path / "flow.json").write_text('{"name": "x", "nodes": []}', encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo", side_effect=fake_clone) as clone,
        patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5680),
        patch("n8n_launcher.workspaces.manager.has_db_layout", return_value=False),
    ):
        workspace = launcher.clone_from_git(
            "https://example.test/repo.git",
            dest,
            name="Imported",
            branch="develop",
        )

    clone.assert_called_once_with("https://example.test/repo.git", dest, branch="develop")
    assert workspace.name == "Imported"
    assert workspace.workflows_dir == dest
    assert workspace.git.enabled is True
    assert workspace.git.remote_url == "https://example.test/repo.git"
    assert workspace.git.branch == workspace_branch(workspace.id)
    assert workspace.db.mode is DbMode.NONE
    assert (dest / "n8nPipelines").is_dir()
    assert store.load().workspaces == [workspace]


def test_clone_from_git_defaults_to_managed_when_db_layout_present(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    dest = tmp_path / "repo-with-db"

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "db" / "migrations").mkdir(parents=True)
        (path / "db" / "migrations" / "001-init.sql").write_text("select 1;", encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo", side_effect=fake_clone),
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="main"),
        patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5681),
    ):
        workspace = launcher.clone_from_git("https://example.test/repo.git", dest)

    assert workspace.db.mode is DbMode.MANAGED
    assert workspace.db.password
    assert (dest / "db" / "schema.sql").is_file()


def test_clone_from_git_rejects_non_empty_folder(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    dest = tmp_path / "not-empty"
    dest.mkdir()
    (dest / "existing.txt").write_text("hi", encoding="utf-8")

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo") as clone,
        pytest.raises(WorkspaceError, match="n'est pas vide"),
    ):
        launcher.clone_from_git("https://example.test/repo.git", dest)

    clone.assert_not_called()


def test_clone_from_git_propagates_git_error(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)

    with (
        patch(
            "n8n_launcher.workspaces.manager.git_pull_new_repo",
            side_effect=GitError("fatal: repository not found"),
        ),
        pytest.raises(GitError, match="repository not found"),
    ):
        launcher.clone_from_git("https://bad.test/repo.git", tmp_path / "dest")


def test_clone_from_git_explicit_db_overrides_detection(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    dest = tmp_path / "repo"
    chosen = DbConfig(DbMode.NONE)

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "db" / "migrations").mkdir(parents=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo", side_effect=fake_clone),
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="main"),
        patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5682),
    ):
        workspace = launcher.clone_from_git("https://example.test/repo.git", dest, db=chosen)

    assert workspace.db.mode is DbMode.NONE


def test_clone_from_git_with_token_uses_tokenized_url_then_cleans_remote(
    tmp_path: Path,
) -> None:
    launcher, store, _, _ = manager(tmp_path)
    dest = tmp_path / "repo-token"

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "n8nPipelines").mkdir(exist_ok=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo", side_effect=fake_clone) as clone,
        patch("n8n_launcher.workspaces.manager.git_set_remote_url") as set_remote,
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="develop"),
        patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5683),
        patch("n8n_launcher.workspaces.manager.has_db_layout", return_value=False),
    ):
        workspace = launcher.clone_from_git(
            "https://github.com/octo/flows.git",
            dest,
            branch="develop",
            token="ghp_secret",
        )

    clone.assert_called_once_with(
        "https://ghp_secret@github.com/octo/flows.git", dest, branch="develop"
    )
    set_remote.assert_called_once_with(dest, "origin", "https://github.com/octo/flows.git")
    assert workspace.git.remote_url == "https://github.com/octo/flows.git"
    assert "ghp_secret" not in str(store.load().workspaces[0].to_dict())


def test_clone_from_git_without_token_keeps_original_url(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    dest = tmp_path / "repo-plain"

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "n8nPipelines").mkdir(exist_ok=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_pull_new_repo", side_effect=fake_clone) as clone,
        patch("n8n_launcher.workspaces.manager.git_set_remote_url") as set_remote,
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="main"),
        patch("n8n_launcher.workspaces.manager.suggest_port", return_value=5684),
        patch("n8n_launcher.workspaces.manager.has_db_layout", return_value=False),
    ):
        workspace = launcher.clone_from_git("https://github.com/octo/flows.git", dest)

    clone.assert_called_once_with("https://github.com/octo/flows.git", dest, branch=None)
    set_remote.assert_not_called()
    assert workspace.git.remote_url == "https://github.com/octo/flows.git"


# ---------------------------------------------------------------------------
# Serveur de déploiement
# ---------------------------------------------------------------------------


def server_cfg() -> ServerConfig:
    return ServerConfig(
        enabled=True,
        host="prod.example.test",
        ssh_port=22,
        user="deploy",
        key_path="/home/me/.ssh/id_ed25519",
        base_dir="n8n-launcher/demo",
        n8n_port=5689,
    )


def test_update_accepts_server_without_restart(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    updated = launcher.update(workspace.id, server=server_cfg())

    assert updated.server.enabled is True
    assert updated.server.host == "prod.example.test"
    assert updated.restart_required is False
    assert store.load().workspaces[0].server.host == "prod.example.test"


def test_update_refuses_server_port_used_by_another_workspace(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    first = create_none(launcher, tmp_path, port=5681)
    second = create_none(launcher, tmp_path / "other", port=5682)
    launcher.update(first.id, server=server_cfg())

    with pytest.raises(WorkspaceError, match="utilise déjà"):
        launcher.update(second.id, server=server_cfg())

    assert launcher.store.load().workspaces[1].server.enabled is False


def test_update_accepts_same_server_for_the_same_workspace(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    launcher.update(workspace.id, server=server_cfg())

    updated = launcher.update(workspace.id, server=server_cfg())

    assert updated.server.host == "prod.example.test"


def test_install_server_refuses_duplicate_host_port(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    first = create_none(launcher, tmp_path, port=5681)
    second = create_none(launcher, tmp_path / "other", port=5682)
    launcher.update(first.id, server=server_cfg())

    with (
        patch("n8n_launcher.workspaces.manager.test_connection") as probe,
        pytest.raises(WorkspaceError, match="utilise déjà"),
    ):
        launcher.install_server(second, server_cfg())

    probe.assert_not_called()


def test_update_rejects_unknown_fields(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with pytest.raises(WorkspaceError, match="Unsupported"):
        launcher.update(workspace.id, server_enabled=True)


def test_install_server_pushes_hook_and_deploy_script(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    workspace.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
    launcher.git_init_workspace(workspace, remote_url="https://github.com/x/y.git")

    with (
        patch("n8n_launcher.workspaces.manager.test_connection") as probe,
        patch("n8n_launcher.workspaces.manager.ssh_run") as ssh,
        patch("n8n_launcher.workspaces.manager.render_hook", return_value="#hook#") as hook,
        patch(
            "n8n_launcher.workspaces.manager.render_deploy_script", return_value="#deploy#"
        ) as script,
        patch("n8n_launcher.workspaces.manager.mkdir_remote") as mkdir,
        patch("n8n_launcher.workspaces.manager.write_remote_file") as write,
        patch("n8n_launcher.workspaces.manager.chmod_remote") as chmod,
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_add_remote") as add_remote,
    ):
        launcher.install_server(workspace, server_cfg())

    probe.assert_called_once()
    # The bare repository is created first so the hook directory exists.
    assert ssh.call_args.args[1].startswith("git init --bare ")
    assert "launcher/demo.git" in ssh.call_args.args[1]
    mkdir.assert_called_once()
    hook.assert_called_once()
    write.assert_any_call(
        server_cfg(),
        "n8n-launcher/demo.git/hooks/post-receive",
        "#hook#",
    )
    write.assert_any_call(server_cfg(), "n8n-launcher/demo/deploy.py", "#deploy#")
    assert chmod.call_count == 2
    add_remote.assert_called_once()


def test_install_server_failure_raises_without_persisting(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.test_connection", side_effect=SshError("denied")),
        pytest.raises(SshError),
    ):
        launcher.install_server(workspace, server_cfg())

    assert store.load().workspaces[0].server.enabled is False


def test_install_server_requires_connection_by_default(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with (
        patch(
            "n8n_launcher.workspaces.manager.test_connection", side_effect=SshError("denied")
        ) as probe,
        patch("n8n_launcher.workspaces.manager.write_remote_file") as write,
        pytest.raises(SshError),
    ):
        launcher.install_server(workspace, server_cfg(), test=False)

    probe.assert_not_called()
    write.assert_not_called()


def test_publish_requires_server_enabled(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with pytest.raises(WorkspaceError, match="Aucun serveur"):
        launcher.publish(workspace)


def test_publish_requires_git(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        return current

    workspace = launcher.store.mutate(apply)
    workspace.server = server_cfg()

    with pytest.raises(WorkspaceError, match="Git"):
        launcher.publish(workspace)


def test_publish_blocks_when_credentials_not_exportable(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        current.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
        return current

    workspace = launcher.store.mutate(apply)
    workspace.server = server_cfg()
    workspace.git = GitConfig(enabled=True)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        pytest.raises(WorkspaceError, match="démarré une fois"),
    ):
        launcher.publish(workspace)


def test_publish_exports_pushes_main_and_records_status(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        current.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
        current.api_key = "api-key-123"
        return current

    workspace = launcher.store.mutate(apply)
    workspace.api_key = "api-key-123"
    workspace.server = server_cfg()

    marker = {
        "sha": "abc123",
        "status": "ok",
        "at": 1700000000,
        "error": "",
    }
    credentials = [{"name": "API", "type": "httpRequest", "data": {"url": "x"}}]

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch.object(WorkspaceManager, "_export_all_credentials", return_value=credentials),
        patch("n8n_launcher.workspaces.manager.render_remote_compose", return_value="services: {}"),
        patch("n8n_launcher.workspaces.manager.write_remote_file") as write,
        patch("n8n_launcher.workspaces.manager.chmod_remote") as chmod,
        patch("n8n_launcher.workspaces.manager.git_add") as add,
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True) as commit,
        patch("n8n_launcher.workspaces.manager.git_push_ref") as push_ref,
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="dev"),
        patch(
            "n8n_launcher.workspaces.manager.git_ssh_env",
            return_value={"GIT_SSH_COMMAND": "ssh -i k"},
        ),
        patch.object(WorkspaceManager, "_poll_deploy", return_value=("ok", marker)),
    ):
        launcher.publish(workspace)

    secrets_written = write.call_args_list[0]
    assert secrets_written.args[1] == "n8n-launcher/demo/secrets.json"
    payload = json.loads(secrets_written.args[2])
    assert payload["owner_email"] == "owner@example.test"
    assert payload["credentials"] == credentials
    chmod.assert_any_call(workspace.server, "n8n-launcher/demo/secrets.json", mode="600")
    add.assert_called_once_with(workspace.workflows_dir)
    commit.assert_called_once()
    push_ref.assert_called_once()
    args, kwargs = push_ref.call_args
    assert args[0] == workspace.workflows_dir
    assert args[1] == "server"
    assert args[2] == "dev"
    assert args[3] == "main"
    assert kwargs["env"] == {"GIT_SSH_COMMAND": "ssh -i k"}
    stored = store.load().workspaces[0]
    assert stored.server_last_error is None
    assert stored.server_last_deploy is not None and stored.server_last_deploy != ""


def test_publish_records_error_status_and_raises(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        current.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
        current.api_key = "api-key-123"
        return current

    workspace = launcher.store.mutate(apply)

    marker = {"sha": "abc123", "status": "error", "at": 1700000000, "error": "migration 001: boom"}

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch.object(WorkspaceManager, "_export_all_credentials", return_value=[]),
        patch("n8n_launcher.workspaces.manager.render_remote_compose", return_value="services: {}"),
        patch(
            "n8n_launcher.workspaces.manager.build_secrets_document",
            return_value={"credentials": []},
        ),
        patch("n8n_launcher.workspaces.manager.write_remote_file"),
        patch("n8n_launcher.workspaces.manager.chmod_remote"),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_push_ref"),
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="dev"),
        patch("n8n_launcher.workspaces.manager.git_ssh_env", return_value={}),
        patch.object(WorkspaceManager, "_poll_deploy", return_value=("error", marker)),
        pytest.raises(WorkspaceError, match="boom"),
    ):
        launcher.publish(workspace)

    stored = store.load().workspaces[0]
    assert stored.server_last_error == "migration 001: boom"


def test_publish_timeout_raises(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        current.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
        current.api_key = "api-key-123"
        return current

    workspace = launcher.store.mutate(apply)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch.object(WorkspaceManager, "_export_all_credentials", return_value=[]),
        patch("n8n_launcher.workspaces.manager.render_remote_compose", return_value="services: {}"),
        patch(
            "n8n_launcher.workspaces.manager.build_secrets_document",
            return_value={"credentials": []},
        ),
        patch("n8n_launcher.workspaces.manager.write_remote_file"),
        patch("n8n_launcher.workspaces.manager.chmod_remote"),
        patch("n8n_launcher.workspaces.manager.git_add"),
        patch("n8n_launcher.workspaces.manager.git_commit", return_value=True),
        patch("n8n_launcher.workspaces.manager.git_push_ref"),
        patch("n8n_launcher.workspaces.manager.git_current_branch", return_value="dev"),
        patch("n8n_launcher.workspaces.manager.git_ssh_env", return_value={}),
        patch.object(WorkspaceManager, "_poll_deploy", return_value=("timeout", {})),
        pytest.raises(WorkspaceError, match=r"limite de temps|timed out|expir"),
    ):
        launcher.publish(workspace)

    assert store.load().workspaces[0].server_last_error is not None


def test_disable_server_clears_enabled_and_remote(tmp_path: Path) -> None:
    launcher, store, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    def apply(config: AppConfig) -> Workspace:
        current = next(w for w in config.workspaces if w.id == workspace.id)
        current.server = server_cfg()
        current.git = GitConfig(enabled=True, remote_url="https://github.com/x/y.git")
        return current

    workspace = launcher.store.mutate(apply)

    with (
        patch("n8n_launcher.workspaces.manager.git_is_repo", return_value=True),
        patch(
            "n8n_launcher.workspaces.manager.git_remote_url", return_value="deploy@host:repo.git"
        ),
        patch("n8n_launcher.workspaces.manager.git_remove_remote") as remove,
    ):
        launcher.disable_server(workspace)

    so = store.load().workspaces[0]
    assert so.server.enabled is False
    remove.assert_called_once_with(workspace.workflows_dir, "server")


def test_poll_deploy_returns_ok_marker(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    marker = {"sha": "abc", "status": "ok", "at": 1, "error": ""}
    import json as _json

    with (
        patch("n8n_launcher.workspaces.manager.ssh_run") as ssh,
        patch("n8n_launcher.workspaces.manager.time.monotonic", side_effect=[0.0, 0.01]) as mono,
    ):
        result = ssh.return_value
        result.stdout = _json.dumps(marker)
        status, payload = launcher._poll_deploy(server_cfg(), "demo", timeout=1.0, interval=0.01)

    assert status == "ok"
    assert payload["status"] == "ok"
    mono.assert_called()


def test_poll_deploy_returns_error_marker(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    marker = {"sha": "abc", "status": "error", "at": 1, "error": "boom"}

    with patch("n8n_launcher.workspaces.manager.ssh_run") as ssh:
        ssh.return_value.stdout = json.dumps(marker)
        status, payload = launcher._poll_deploy(server_cfg(), "demo", timeout=1.0, interval=0.01)

    assert status == "error"
    assert payload["error"] == "boom"


def test_poll_deploy_times_out_when_marker_absent(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)

    with (
        patch("n8n_launcher.workspaces.manager.ssh_run") as ssh,
        patch("n8n_launcher.workspaces.manager.time.monotonic", side_effect=[0.0, 99.0]) as mono,
    ):
        ssh.return_value.stdout = ""
        status, payload = launcher._poll_deploy(server_cfg(), "demo", timeout=1.0, interval=0.01)

    assert status == "timeout"
    assert payload == {}
    mono.assert_called()


def test_export_all_credentials_requires_api_key(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)

    with pytest.raises(WorkspaceError, match="démarré une fois"):
        launcher._export_all_credentials(workspace)


def test_export_all_credentials_reads_values(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    workspace = create_none(launcher, tmp_path)
    workspace.api_key = "k"

    class FakeApi:
        def list_credentials(self):
            return [{"id": "c1", "name": "API", "type": "httpRequest"}]

        def get_credential(self, credential_id):
            return {"id": "c1", "data": {"url": "https://x"}}

    launcher.api_factory = lambda ws, key: FakeApi()

    payload = launcher._export_all_credentials(workspace)

    assert payload == [{"name": "API", "type": "httpRequest", "data": {"url": "https://x"}}]
