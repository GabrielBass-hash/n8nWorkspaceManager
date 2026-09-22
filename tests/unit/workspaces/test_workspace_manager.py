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
    Workspace,
    WorkspaceState,
)
from n8n_launcher.docker.manager import ComposeStatus, DockerError
from n8n_launcher.git import GitError
from n8n_launcher.n8n.api import N8nApiError
from n8n_launcher.n8n.owner import OwnerSetupError
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

    is_repo.assert_called_once()
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

    run.return_value.export_all.assert_called_once_with()
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

    init.assert_called_once_with(workspace.workflows_dir)
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
        patch(
            "n8n_launcher.workspaces.manager.git_current_branch",
            return_value="develop",
        ),
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
    assert workspace.git.branch == "develop"
    assert workspace.db.mode is DbMode.NONE
    assert (dest / "n8nPipelines").is_dir()
    assert store.load().workspaces == [workspace]


def test_clone_from_git_defaults_to_managed_when_db_layout_present(tmp_path: Path) -> None:
    launcher, _, _, _ = manager(tmp_path)
    dest = tmp_path / "repo-with-db"

    def fake_clone(url, path, *, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / "db" / "migrations").mkdir(parents=True)
        (path / "db" / "migrations" / "001-init.sql").write_text(
            "select 1;", encoding="utf-8"
        )

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
