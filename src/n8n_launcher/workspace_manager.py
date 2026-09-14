"""Workspace CRUD and lifecycle orchestration."""

from __future__ import annotations

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .api_client import N8nApiClient, N8nApiError
from .compose import write_compose
from .config import ConfigStore
from .database import MigrationRunner
from .db_manager import detect_migrations
from .docker_manager import DockerManager, DockerError, parse_compose_status
from .models import AppConfig, DbConfig, DbMode, Workspace, WorkspaceState
from .n8n_setup import configure_db_credential
from .owner_setup import OwnerSetup
from .paths import compose_file
from .ports import suggest_port


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation cannot be completed."""


class WorkspaceManager:
    def __init__(
        self,
        store: ConfigStore,
        docker: DockerManager,
        *,
        owner_booter: Callable[[str, str, str], str] | None = None,
        api_factory: Callable[[Workspace, str], N8nApiClient] | None = None,
    ) -> None:
        self.store = store
        self.docker = docker
        self.owner_booter = owner_booter or self._default_owner_booter
        self.api_factory = api_factory or self._default_api_factory
        self.migrations = MigrationRunner(docker)

    @staticmethod
    def _default_owner_booter(base_url: str, email: str, password: str) -> str:
        return OwnerSetup().bootstrap(base_url, email, password).api_key

    @staticmethod
    def _default_api_factory(workspace: Workspace, api_key: str) -> N8nApiClient:
        return N8nApiClient(f"http://127.0.0.1:{workspace.port}/api/v1", api_key)

    def list(self) -> list[Workspace]:
        return self.store.load().workspaces

    def live_state(self, workspace: Workspace) -> WorkspaceState:
        compose = compose_file(workspace.id)
        if not compose.exists():
            return workspace.state
        try:
            result = self.docker.status(workspace, compose)
        except Exception:
            return workspace.state
        if result.returncode != 0:
            return workspace.state
        n8n_state = parse_compose_status(result.raw_output).get("n8n")
        if n8n_state == "running":
            return WorkspaceState.RUNNING
        if n8n_state in {"created", "restarting", "starting", "paused"}:
            return WorkspaceState.STARTING
        return WorkspaceState.STOPPED

    def reconcile_all(self) -> int:
        changed = 0
        try:
            config = self.store.load()
        except Exception:
            return changed
        for workspace in config.workspaces:
            live = self.live_state(workspace)
            if live is not workspace.state:
                workspace.state = live
                changed += 1
        if changed:
            try:
                self.store.save(config)
            except Exception:
                pass
        return changed

    def create(
        self,
        name: str,
        workflows_dir: Path,
        *,
        db: DbConfig | None = None,
        port: int | None = None,
        n8n_version: str = "2.33.3",
    ) -> Workspace:
        if not name.strip():
            raise WorkspaceError("Workspace name is required")
        config = self.store.load()
        workspace_id = uuid4().hex[:8]
        migrations = detect_migrations(workflows_dir)
        if db is None:
            if not migrations:
                raise WorkspaceError("External database configuration is required without migrations")
            db = self._managed_db_config()
        if db.mode is DbMode.MANAGED and not db.password:
            db = self._managed_db_config()
        reserved = {workspace.port for workspace in config.workspaces}
        selected_port = port or suggest_port(reserved=reserved)
        if selected_port in reserved:
            raise WorkspaceError(f"Port is already used by another workspace: {selected_port}")
        self._scaffold(workflows_dir, db)
        workspace = Workspace(
            id=workspace_id,
            name=name.strip(),
            workflows_dir=workflows_dir,
            port=selected_port,
            db=db,
            n8n_version=n8n_version,
        )
        config.workspaces.append(workspace)
        self.store.save(config)
        return workspace

    def update(self, workspace_id: str, **changes: object) -> Workspace:
        config = self.store.load()
        current = self._find(config, workspace_id)
        allowed = {"name", "workflows_dir", "port", "db", "n8n_version"}
        unknown = set(changes) - allowed
        if unknown:
            raise WorkspaceError(f"Unsupported workspace fields: {', '.join(sorted(unknown))}")
        updated = replace(current, **changes)
        if "workflows_dir" in changes or "port" in changes:
            updated.restart_required = True
        config.workspaces[config.workspaces.index(current)] = updated
        self.store.save(config)
        return updated

    def delete(self, workspace_id: str) -> None:
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if workspace.state is not WorkspaceState.STOPPED:
            raise WorkspaceError("Workspace must be stopped before deletion")
        config.workspaces.remove(workspace)
        self.store.save(config)

    def ensure_running(self, workspace_id: str) -> Workspace:
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if self.live_state(workspace) is not WorkspaceState.RUNNING:
            self.start(workspace_id)
            config = self.store.load()
            workspace = self._find(config, workspace_id)
        if not workspace.api_key:
            workspace.api_key = self.owner_booter(
                f"http://127.0.0.1:{workspace.port}",
                config.owner_email,
                config.owner_password,
            )
            self.store.save(config)
        self._ensure_db_credentials(workspace)
        return self._find(self.store.load(), workspace_id)

    def start(self, workspace_id: str) -> Workspace:
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if workspace.db.mode is DbMode.MANAGED:
            self._ensure_managed_db_parameters(config, workspace)
            config = self.store.load()
            workspace = self._find(config, workspace_id)
        compose = compose_file(workspace.id)
        write_compose(workspace, compose)
        workspace.state = WorkspaceState.STARTING
        self.store.save(config)
        try:
            self.docker.up(workspace, compose)
            if workspace.db.mode is DbMode.MANAGED:
                self.migrations.ensure(workspace, compose)
                self.migrations.apply(
                    workspace,
                    workspace.workflows_dir / "db" / "migrations",
                    compose,
                )
        except Exception:
            workspace.state = WorkspaceState.ERROR
            self.store.save(config)
            raise
        workspace.state = WorkspaceState.RUNNING
        workspace.restart_required = False
        self.store.save(config)
        return workspace

    def stop(self, workspace_id: str) -> Workspace:
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        compose = compose_file(workspace.id)
        # Skip `docker down` when already stopped: makes the call idempotent so
        # the atexit stop_all() pass after a GUI close does not tear containers
        # down a second time.
        if compose.exists() and workspace.state is not WorkspaceState.STOPPED:
            self.docker.down(workspace, compose, remove_orphans=True)
        workspace.state = WorkspaceState.STOPPED
        self.store.save(config)
        return workspace

    def _ensure_db_credentials(self, workspace: Workspace) -> None:
        if workspace.db.mode is DbMode.NONE:
            return
        try:
            self._configure_credentials(workspace, workspace.api_key)
            return
        except N8nApiError as exc:
            if exc.status_code not in (401, 403):
                raise
        config = self.store.load()
        current = self._find(config, workspace.id)
        current.api_key = self.owner_booter(
            f"http://127.0.0.1:{current.port}",
            config.owner_email,
            config.owner_password,
        )
        self.store.save(config)
        self._configure_credentials(current, current.api_key)

    def _configure_credentials(self, workspace: Workspace, api_key: str) -> None:
        api = self.api_factory(workspace, api_key)
        configure_db_credential(api, workspace)

    def _ensure_managed_db_parameters(self, config: AppConfig, workspace: Workspace) -> None:
        mutated = False
        if not workspace.db.database_name:
            workspace.db.database_name = "data"
            mutated = True
        if not workspace.db.username:
            workspace.db.username = "n8ndata"
            mutated = True
        if not workspace.db.password:
            workspace.db.password = secrets.token_hex(16)
            mutated = True
        if mutated:
            config.workspaces[config.workspaces.index(workspace)] = workspace
            self.store.save(config)

    @staticmethod
    def _managed_db_config() -> DbConfig:
        return DbConfig(
            mode=DbMode.MANAGED,
            database_name="data",
            username="n8ndata",
            password=secrets.token_hex(16),
        )

    @staticmethod
    def _scaffold(workflows_dir: Path, db: DbConfig) -> None:
        (workflows_dir / "n8nPipelines").mkdir(parents=True, exist_ok=True)
        if db.mode is DbMode.MANAGED:
            (workflows_dir / "db" / "migrations").mkdir(parents=True, exist_ok=True)
            schema = workflows_dir / "db" / "schema.sql"
            if not schema.exists():
                schema.write_text(
                    "-- n8n-launcher : schéma de la base locale de ce workspace.\n",
                    encoding="utf-8",
                )

    @staticmethod
    def _find(config: AppConfig, workspace_id: str) -> Workspace:
        for workspace in config.workspaces:
            if workspace.id == workspace_id:
                return workspace
        raise WorkspaceError(f"Unknown workspace: {workspace_id}")