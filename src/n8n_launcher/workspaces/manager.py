"""Workspace CRUD and lifecycle orchestration."""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..core.config import ConfigStore
from ..core.models import AppConfig, DbConfig, DbMode, GitConfig, Workspace, WorkspaceState
from ..core.paths import compose_file
from ..database import MigrationRunner, configure_db_credential, detect_migrations
from ..docker.compose import write_compose
from ..docker.manager import DockerManager, DockerError, parse_compose_status
from ..git import (
    GitError,
    ensure_gitignore,
    git_add,
    git_add_remote,
    git_commit,
    git_has_remote,
    git_has_unpushed_commits,
    git_init,
    git_is_repo,
    git_pull,
    git_push,
    git_remote_url,
    git_remove_remote,
    git_set_remote_url,
)
from ..n8n.api import N8nApiClient, N8nApiError
from ..n8n.owner import OwnerSetup
from ..n8n.workflows import SyncRunner
from ..platform.ports import suggest_port
from . import ci


logger = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation cannot be completed."""


class WorkspaceManager:
    """Central controller for workspace CRUD and the start/stop lifecycle.

    Orchestrates Compose rendering, Docker, migrations, owner bootstrap, DB
    credentials, and workflow import on behalf of the GUI and the entry point.
    """

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
        """Return all persisted workspaces."""
        return self.store.load().workspaces

    def live_state(self, workspace: Workspace) -> WorkspaceState:
        """Query Docker for the real state, falling back to the stored one."""
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
        """Sync persisted states with Docker reality and save the changes.

        Returns the number of workspaces whose state changed.
        """
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
        n8n_version: str = "2.40.0",
    ) -> Workspace:
        """Create and persist a new workspace, scaffolding its folders."""
        if not name.strip():
            raise WorkspaceError("Workspace name is required")
        config = self.store.load()
        workspace_id = uuid4().hex[:8]
        migrations = detect_migrations(workflows_dir)
        if db is None:
            if migrations:
                db = self._managed_db_config()
            else:
                db = DbConfig(DbMode.NONE)
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
        """Update the allowed workspace fields and flag a restart when needed."""
        config = self.store.load()
        current = self._find(config, workspace_id)
        allowed = {"name", "workflows_dir", "port", "db", "git", "n8n_version"}
        unknown = set(changes) - allowed
        if unknown:
            raise WorkspaceError(f"Unsupported workspace fields: {', '.join(sorted(unknown))}")
        updated = replace(current, **changes)
        if any(key in changes for key in ("workflows_dir", "port", "db")):
            updated.restart_required = True
        config.workspaces[config.workspaces.index(current)] = updated
        self.store.save(config)
        return updated

    def delete(self, workspace_id: str) -> None:
        """Remove a stopped workspace from the configuration."""
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if workspace.state is not WorkspaceState.STOPPED:
            raise WorkspaceError("Workspace must be stopped before deletion")
        config.workspaces.remove(workspace)
        self.store.save(config)

    def ensure_running(
        self,
        workspace_id: str,
        *,
        on_ready: Callable[[int], None] | None = None,
    ) -> Workspace:
        """Start the workspace, wait for n8n readiness, then bootstrap owner, DB creds, workflows.

        ``on_ready`` is invoked with the workspace port once the container is up so
        the caller can wait for n8n's HTTP health endpoint before any API call.
        """
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if self.live_state(workspace) is not WorkspaceState.RUNNING:
            self.start(workspace_id)
            config = self.store.load()
            workspace = self._find(config, workspace_id)
        if on_ready is not None:
            on_ready(workspace.port)
        if not workspace.api_key:
            workspace.api_key = self.owner_booter(
                f"http://127.0.0.1:{workspace.port}",
                config.owner_email,
                config.owner_password,
            )
            self.store.save(config)
        self._ensure_db_credentials(workspace)
        self._import_workflows(workspace)
        return self._find(self.store.load(), workspace_id)

    def _import_workflows(self, workspace: Workspace) -> None:
        """Import n8n workflow exports from the workspace folder into n8n."""
        pipelines_dir = workspace.workflows_dir / "n8nPipelines"
        if workspace.git.enabled and git_is_repo(workspace.workflows_dir):
            try:
                git_pull(workspace.workflows_dir)
            except GitError as exc:
                logger.warning("git pull failed for %s: %s", workspace.name, exc)
        if not pipelines_dir.is_dir():
            return
        # Import files stored at the folder root as well, so a folder that
        # simply contains workflow exports is picked up too.
        api = self.api_factory(workspace, workspace.api_key)
        runner = SyncRunner(api, pipelines_dir)
        runner.import_all()
        root_runner = SyncRunner(api, workspace.workflows_dir)
        root_runner.import_all()

    def sync_git(self, workspace: Workspace, *, push: bool = True) -> None:
        """Commit local workflow changes and push to the remote.

        Called automatically when an n8n instance is stopped (exported
        workflows are committed and pushed so the repository tracks the last
        known state). Git failures never raise: they degrade to the
        ``git_push_failed`` flag that the chip surfaces, so the close sequence
        is never blocked by repository problems.
        """
        if not workspace.git.enabled or not git_is_repo(workspace.workflows_dir):
            return
        message = f"n8n-launcher: sync workflows [{datetime.now().isoformat(timespec='seconds')}]"
        self._commit_and_push(workspace, message, push=push)

    def _commit_and_push(self, workspace: Workspace, message: str, *, push: bool = True) -> bool:
        """Stage everything, commit with *message*, and push when requested.

        Mirrors ``sync_git``'s semantics: git failures never raise — they
        degrade to the ``git_push_failed`` flag that the UI chip surfaces.
        Returns True when a commit was actually created.
        """
        committed = False
        try:
            git_add(workspace.workflows_dir)
            committed = git_commit(workspace.workflows_dir, message)
        except GitError as exc:
            logger.warning("git stage/commit failed for %s: %s", workspace.name, exc)
            self._set_git_push_failed(workspace, True)
            return committed
        if committed:
            logger.info("Committed for %s: %s", workspace.name, message)
        if push and (committed or git_has_unpushed_commits(workspace.workflows_dir)):
            try:
                git_push(workspace.workflows_dir)
            except GitError as exc:
                logger.warning("git push failed for %s: %s", workspace.name, exc)
                self._set_git_push_failed(workspace, True)
                return committed
            self._set_git_push_failed(workspace, False)
        return committed

    def _set_git_push_failed(self, workspace: Workspace, failed: bool) -> None:
        """Persist the push-failed flag (and mirror it on the in-memory object)."""
        workspace.git_push_failed = failed
        config = self.store.load()
        current = self._find(config, workspace.id)
        current.git_push_failed = failed
        self.store.save(config)

    def git_init_workspace(self, workspace: Workspace, *, remote_url: str | None = None) -> None:
        """Initialize a git repository in the workspace folder."""
        if not git_is_repo(workspace.workflows_dir):
            git_init(workspace.workflows_dir)
        ensure_gitignore(workspace.workflows_dir)
        if remote_url:
            if git_has_remote(workspace.workflows_dir):
                git_set_remote_url(workspace.workflows_dir, "origin", remote_url)
            else:
                git_add_remote(workspace.workflows_dir, "origin", remote_url)
        elif git_has_remote(workspace.workflows_dir):
            # Clearing the URL must really detach the repo, otherwise the stale
            # origin would keep being pushed to while the UI shows no remote.
            git_remove_remote(workspace.workflows_dir, "origin")
        config = self.store.load()
        workspace = self._find(config, workspace.id)
        workspace.git = GitConfig(enabled=True, remote_url=remote_url)
        workspace.git_push_failed = False
        self.store.save(config)

    def git_remote_url(self, workspace: Workspace) -> str | None:
        """Return the current origin URL of the workspace repository."""
        if not git_is_repo(workspace.workflows_dir):
            return None
        return git_remote_url(workspace.workflows_dir)

    def configure_db(self, workspace: Workspace, db: DbConfig) -> Workspace:
        """Switch or update the workspace database configuration.

        Enabling a ``MANAGED`` DB on a workspace that has none scaffolds the
        ``db/`` layout (migrations dir + schema) so the start flow can detect
        and apply migrations on the next launch. Missing credentials are
        filled with the usual defaults / a fresh random password.

        The identity (name/user/password) of an already-managed database is
        immutable: the Postgres role and the n8n credential were created with
        the current values, so swapping them here would orphan both until the
        next ``ALTER ROLE`` — which ``ensure()`` never performs. To change the
        identity, switch the workspace back to ``NONE`` and re-enable
        ``MANAGED``, which regenerates everything.
        """
        if db.mode is DbMode.MANAGED:
            if workspace.db.mode is DbMode.MANAGED:
                same_identity = (
                    (db.database_name or None) == workspace.db.database_name
                    and (db.username or None) == workspace.db.username
                    and db.password == workspace.db.password
                )
                if same_identity:
                    return workspace
                db = replace(
                    db,
                    database_name=workspace.db.database_name,
                    username=workspace.db.username,
                    password=workspace.db.password,
                )
                logger.warning(
                    "Ignoring DB identity change for managed workspace %s",
                    workspace.name,
                )
            else:
                db.database_name = db.database_name or "data"
                db.username = db.username or "n8ndata"
                db.password = db.password or secrets.token_hex(16)
                self._scaffold(workspace.workflows_dir, db)
        return self.update(workspace.id, db=db)

    def configure_git(self, workspace: Workspace, *, remote_url: str | None = None) -> None:
        """Attach, update or detach the remote of an existing git repository."""
        config = self.store.load()
        workspace = self._find(config, workspace.id)
        ensure_gitignore(workspace.workflows_dir)
        if remote_url:
            if git_has_remote(workspace.workflows_dir):
                git_set_remote_url(workspace.workflows_dir, "origin", remote_url)
            else:
                git_add_remote(workspace.workflows_dir, "origin", remote_url)
        elif git_has_remote(workspace.workflows_dir):
            # Detach for real: an empty URL must not leave a stale origin that
            # keeps being pushed to behind the launcher's back.
            git_remove_remote(workspace.workflows_dir, "origin")
        workspace.git = GitConfig(enabled=True, remote_url=remote_url)
        workspace.git_push_failed = False
        self.store.save(config)
        logger.info("Configured git for %s", workspace.name)

    def enable_ci(self, workspace: Workspace) -> Workspace:
        """Generate the CI harness in the workspace repository and enable CI.

        Requires a real git repository with a GitHub remote: without one there
        is nowhere for a GitHub Actions workflow to run. The persisted
        selection (``tests.json``) is preserved across disable/re-enable
        cycles instead of being reset.
        """
        if not workspace.git.enabled or not git_is_repo(workspace.workflows_dir):
            raise WorkspaceError(
                "Les tests GitHub Actions nécessitent un dépôt Git (configurez Git d'abord)."
            )
        if ci.github_repo_path(self.git_remote_url(workspace)) is None:
            raise WorkspaceError(
                "Les tests GitHub Actions nécessitent un dépôt distant GitHub "
                "(ex. https://github.com/utilisateur/repo.git)."
            )
        files = ci.render_harness(workspace.n8n_version)
        selection = ci.selection_path(workspace.workflows_dir)
        for rel, content in files.items():
            if rel == f"{ci.CI_DIR}/{ci.SELECTION_FILE}":
                if not selection.exists():
                    selection.parent.mkdir(parents=True, exist_ok=True)
                    selection.write_text(content, encoding="utf-8")
                continue
            target = workspace.workflows_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        config = self.store.load()
        workspace = self._find(config, workspace.id)
        workspace.git = replace(workspace.git, ci_enabled=True)
        config.workspaces[config.workspaces.index(workspace)] = workspace
        self.store.save(config)
        self._commit_and_push(workspace, "n8n-launcher: activer les tests GitHub Actions")
        logger.info("Enabled CI for %s", workspace.name)
        return workspace

    def disable_ci(self, workspace: Workspace) -> Workspace:
        """Remove the generated CI harness while keeping the pipeline selection."""
        for rel in ci.DISABLE_FILES:
            target = workspace.workflows_dir / rel
            try:
                if target.exists():
                    target.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s: %s", target, exc)
        config = self.store.load()
        workspace = self._find(config, workspace.id)
        workspace.git = replace(workspace.git, ci_enabled=False)
        config.workspaces[config.workspaces.index(workspace)] = workspace
        self.store.save(config)
        self._commit_and_push(workspace, "n8n-launcher: désactiver les tests GitHub Actions")
        logger.info("Disabled CI for %s", workspace.name)
        return workspace

    def set_ci_credentials(
        self, workspace: Workspace, credentials: list[dict[str, Any]]
    ) -> Workspace:
        """Record the metadata of the credentials included in the CI secret.

        Only ``name`` and ``type`` are persisted — never the values. The
        values themselves live in the ``N8N_CI_CREDENTIALS`` GitHub secret,
        which is why this call never touches the repository.
        """
        clean = [
            {"name": item.get("name"), "type": item.get("type")} for item in credentials
        ]
        config = self.store.load()
        workspace = self._find(config, workspace.id)
        workspace.git = replace(workspace.git, ci_credentials=clean)
        config.workspaces[config.workspaces.index(workspace)] = workspace
        self.store.save(config)
        logger.info("Recorded %d CI credential(s) for %s", len(clean), workspace.name)
        return workspace

    def save_ci_selection(
        self, workspace: Workspace, selected: set[str], *, push: bool = False
    ) -> Workspace:
        """Persist the pipeline selection and optionally commit/push it."""
        ci.write_selection(workspace.workflows_dir, set(selected))
        if push:
            self._commit_and_push(
                workspace, "n8n-launcher: mettre à jour les tests GitHub Actions"
            )
        return workspace

    def ci_credentials_payload(
        self, workspace: Workspace, selected: list[dict[str, Any]]
    ) -> str:
        """Build the JSON pasted as the ``N8N_CI_CREDENTIALS`` GitHub secret.

        Reads the credential values live from the workspace's n8n instance
        (the public list endpoint never exposes ``data``) and returns a JSON
        document of ``{name, type, data}`` items. The workspace must have an
        API key for the values to be readable.
        """
        if not workspace.api_key:
            raise WorkspaceError(
                "Le workspace doit avoir été démarré une fois pour exporter ses credentials."
            )
        api = self.api_factory(workspace, workspace.api_key)
        wanted = {(item.get("name"), item.get("type")) for item in selected}
        listed = api.list_credentials()
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for item in listed:
            key = (item.get("name"), item.get("type"))
            if key in wanted and key not in found:
                found[key] = item
        missing = wanted - set(found)
        if missing:
            names = ", ".join(f"{name} ({ctype})" for name, ctype in sorted(missing))
            raise WorkspaceError(f"Credentials introuvables dans n8n : {names}")
        payload: list[dict[str, Any]] = []
        for (name, ctype), item in found.items():
            detail = api.get_credential(str(item["id"]))
            payload.append({"name": name, "type": ctype, "data": detail.get("data") or {}})
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def start(self, workspace_id: str) -> Workspace:
        """Write Compose, bring the stack up, and apply managed migrations."""
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
        """Stop a workspace, skipping ``docker down`` when already stopped."""
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