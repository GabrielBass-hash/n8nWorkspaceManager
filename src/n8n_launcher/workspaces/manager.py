"""Workspace CRUD and lifecycle orchestration."""

from __future__ import annotations

import json
import logging
import secrets
import shlex
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import ConfigStore
from ..core.models import (
    AppConfig,
    DbConfig,
    DbMode,
    GitConfig,
    ServerConfig,
    Workspace,
    WorkspaceState,
)
from ..core.paths import compose_file
from ..database import (
    DATA_DATABASE,
    DATA_USER,
    MigrationRunner,
    configure_db_credential,
    detect_migrations,
    has_db_layout,
)
from ..docker.compose import compose_project_name, render_remote_compose, write_compose
from ..docker.manager import DockerManager, parse_compose_status
from ..git import (
    GitError,
    ensure_gitignore,
    ensure_workspace_branch,
    git_add,
    git_add_remote,
    git_commit,
    git_current_branch,
    git_has_remote,
    git_has_unpushed_commits,
    git_init,
    git_is_repo,
    git_pull,
    git_pull_new_repo,
    git_push,
    git_push_ref,
    git_remote_url,
    git_remove_remote,
    git_set_remote_url,
    git_ssh_env,
    tokenize_remote_url,
    workspace_branch,
    workspace_git_lock,
)
from ..n8n.api import N8nApiClient, N8nApiError
from ..n8n.owner import OwnerSetup
from ..n8n.workflows import SyncRunner
from ..platform.ports import suggest_port
from ..remote import (
    bare_dir,
    build_secrets_document,
    chmod_remote,
    marker_path,
    mkdir_remote,
    render_deploy_script,
    render_hook,
    resolve_base,
    server_remote_url,
    test_connection,
    write_remote_file,
)
from ..remote.ssh import ssh_run
from . import ci

logger = logging.getLogger(__name__)


def _is_push_rejection(exc: GitError) -> bool:
    """Return True when *exc* looks like a remote rejecting a non-fast-forward."""
    detail = str(exc).lower()
    return any(
        marker in detail
        for marker in ("non-fast-forward", "[rejected]", "fetch first", "stale info")
    )


def _project_live_state(projects: dict[str, dict[str, str]], project: str) -> WorkspaceState:
    """Map one Compose project's service states to a launcher state."""
    n8n_state = (projects.get(project) or {}).get("n8n")
    if n8n_state == "running":
        return WorkspaceState.RUNNING
    if n8n_state in {"created", "restarting", "starting", "paused"}:
        return WorkspaceState.STARTING
    return WorkspaceState.STOPPED


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

        Returns the number of workspaces whose state changed. All live states
        are probed in **one** ``docker ps`` batch (``list_project_states``)
        *before* taking the config lock — a single spawn instead of one
        ``compose ps`` per workspace — then the changes are applied through one
        locked ``store.mutate`` so a concurrent edit is never overwritten by a
        stale full-save.
        """
        try:
            projects = self.docker.list_project_states()
        except Exception:
            return 0
        try:
            config = self.store.load()
        except Exception:
            return 0
        updates: dict[str, WorkspaceState] = {}
        for workspace in config.workspaces:
            # Workspaces whose Compose file is gone (e.g. a partial folder
            # deletion) keep their stored state and are never force-stopped.
            if not compose_file(workspace.id).exists():
                continue
            live = _project_live_state(projects, compose_project_name(workspace))
            if live is not workspace.state:
                updates[workspace.id] = live
        if not updates:
            return 0

        def apply(config: AppConfig) -> None:
            for workspace in config.workspaces:
                live = updates.get(workspace.id)
                if live is not None and live is not workspace.state:
                    workspace.state = live

        try:
            self.store.mutate(apply)
        except Exception:
            return 0
        return len(updates)

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
        reserved = {workspace.port for workspace in self.store.load().workspaces}
        workspace_id = uuid4().hex[:8]
        migrations = detect_migrations(workflows_dir)
        if db is None:
            db = self._managed_db_config() if migrations else DbConfig(DbMode.NONE)
        if db.mode is DbMode.MANAGED:
            # Preserve any explicitly provided DB fields, filling only the
            # missing ones with the launcher defaults (name/user constants and
            # a freshly generated password).
            db = DbConfig(
                mode=DbMode.MANAGED,
                database_name=db.database_name or DATA_DATABASE,
                username=db.username or DATA_USER,
                password=db.password or secrets.token_hex(16),
            )
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

        def append(config: AppConfig) -> Workspace:
            # Re-check under the lock: a concurrent creation may have claimed
            # the port suggested from an earlier snapshot.
            if workspace.port in {item.port for item in config.workspaces}:
                raise WorkspaceError(f"Port is already used by another workspace: {workspace.port}")
            config.workspaces.append(workspace)
            return workspace

        return self.store.mutate(append)

    def clone_from_git(
        self,
        url: str,
        dest: Path,
        *,
        name: str | None = None,
        branch: str | None = None,
        db: DbConfig | None = None,
        port: int | None = None,
        n8n_version: str = "2.40.0",
        token: str | None = None,
    ) -> Workspace:
        """Clone *url* into *dest* and register the result as a workspace.

        Unlike :meth:`create`, the folder is produced by ``git clone`` — nothing
        is scaffolded before the clone runs, and *dest* must be empty (or
        missing). After a successful clone the launcher's folders are created
        idempotently (``n8nPipelines/``, and ``db/migrations/`` in managed mode)
        and the git remote is recorded so the auto-pull/auto-push flows work
        immediately. When *db* is omitted, a cloned ``db/`` layout means MANAGED,
        exactly like :meth:`create`.

        When *token* is provided the clone authenticates with a one-shot
        tokenized URL, then ``origin`` is rewritten to the clean *url* so the
        credential never lands in ``.git/config`` — the same discipline as
        :func:`git/manager.py::git_seed_remote` for created repos.
        """
        workflows_dir = Path(dest)
        if workflows_dir.exists() and any(workflows_dir.iterdir()):
            raise WorkspaceError(
                f"Le dossier « {workflows_dir} » n'est pas vide : clonez dans un dossier vide."
            )
        workflows_dir.parent.mkdir(parents=True, exist_ok=True)

        clone_url = tokenize_remote_url(url, token) if token else url
        git_pull_new_repo(clone_url, workflows_dir, branch=branch)
        if token:
            # ``git clone`` wrote the tokenized URL into origin; put the clean
            # URL back so ``git push`` from the workspace never leaks the token.
            git_set_remote_url(workflows_dir, "origin", url)

        if db is None:
            db = (
                self._managed_db_config() if has_db_layout(workflows_dir) else DbConfig(DbMode.NONE)
            )
        if db.mode is DbMode.MANAGED:
            # Same preservation rule as :meth:`create`: keep provided DB
            # fields, defaulting only the missing ones.
            db = DbConfig(
                mode=DbMode.MANAGED,
                database_name=db.database_name or DATA_DATABASE,
                username=db.username or DATA_USER,
                password=db.password or secrets.token_hex(16),
            )
        self._scaffold(workflows_dir, db)

        reserved = {workspace.port for workspace in self.store.load().workspaces}
        selected_port = port or suggest_port(reserved=reserved)
        if selected_port in reserved:
            raise WorkspaceError(f"Port is already used by another workspace: {selected_port}")

        # ``git clone`` checks out the remote's default branch; the launcher
        # then works on its own ``n8n/<id>`` branch (switched right after the
        # workspace is registered), so the recorded GitConfig stays truthful.
        workspace_id = uuid4().hex[:8]
        workspace = Workspace(
            id=workspace_id,
            name=(name or workflows_dir.name).strip(),
            workflows_dir=workflows_dir,
            port=selected_port,
            db=db,
            git=GitConfig(enabled=True, remote_url=url, branch=workspace_branch(workspace_id)),
            n8n_version=n8n_version,
        )

        def append(config: AppConfig) -> Workspace:
            # Re-check under the lock: a concurrent creation may have claimed
            # the port suggested from an earlier snapshot.
            if workspace.port in {item.port for item in config.workspaces}:
                raise WorkspaceError(f"Port is already used by another workspace: {workspace.port}")
            config.workspaces.append(workspace)
            return workspace

        result = self.store.mutate(append)
        self._ensure_workspace_branch(result)
        return result

    def update(self, workspace_id: str, **changes: object) -> Workspace:
        """Update the allowed workspace fields and flag a restart when needed."""
        allowed = {"name", "workflows_dir", "port", "db", "git", "n8n_version", "server"}
        unknown = set(changes) - allowed
        if unknown:
            raise WorkspaceError(f"Unsupported workspace fields: {', '.join(sorted(unknown))}")

        if "server" in changes:
            server = changes["server"]
            if not isinstance(server, ServerConfig):
                raise WorkspaceError("server must be a ServerConfig")
            config = self.store.load()
            if conflict := self._server_port_conflict(config, workspace_id, server):
                raise WorkspaceError(
                    f"« {conflict} » utilise déjà le port {server.n8n_port} sur {server.host}."
                )

        def apply(config: AppConfig) -> Workspace:
            current = self._find(config, workspace_id)
            updated = replace(current, **changes)
            if any(key in changes for key in ("workflows_dir", "port", "db", "n8n_version")):
                updated.restart_required = True
            config.workspaces[config.workspaces.index(current)] = updated
            return updated

        return self.store.mutate(apply)

    def delete(self, workspace_id: str) -> None:
        """Remove a stopped workspace from the configuration."""

        def remove(config: AppConfig) -> None:
            workspace = self._find(config, workspace_id)
            if workspace.state is not WorkspaceState.STOPPED:
                raise WorkspaceError("Workspace must be stopped before deletion")
            config.workspaces.remove(workspace)

        self.store.mutate(remove)

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
            api_key = self.owner_booter(
                f"http://127.0.0.1:{workspace.port}",
                config.owner_email,
                config.owner_password,
            )
            workspace.api_key = api_key

            def save_key(config: AppConfig) -> None:
                self._find(config, workspace_id).api_key = api_key

            self.store.mutate(save_key)
        self._ensure_db_credentials(workspace)
        self._import_workflows(workspace)
        return self._find(self.store.load(), workspace_id)

    def _import_workflows(self, workspace: Workspace) -> None:
        """Import n8n workflow exports from the workspace folder into n8n."""
        pipelines_dir = workspace.workflows_dir / "n8nPipelines"
        if workspace.git.enabled and git_is_repo(workspace.workflows_dir):
            with workspace_git_lock(workspace.workflows_dir):
                self._ensure_workspace_branch(workspace)
                try:
                    git_pull(workspace.workflows_dir)
                except GitError as exc:
                    logger.warning("git pull failed for %s: %s", workspace.name, exc)
        if not pipelines_dir.is_dir():
            return
        # Import files stored at the folder root as well, so a folder that
        # simply contains workflow exports is picked up too.
        if workspace.api_key is None:
            raise RuntimeError("cannot import workflows without an API key")
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
        with workspace_git_lock(workspace.workflows_dir):
            self._ensure_workspace_branch(workspace)
            message = (
                f"n8n-launcher: sync workflows [{datetime.now().isoformat(timespec='seconds')}]"
            )
            self._commit_and_push(workspace, message, push=push)

    def _commit_and_push(self, workspace: Workspace, message: str, *, push: bool = True) -> bool:
        """Stage everything, commit with *message*, and push when requested.

        The whole cycle runs under the workspace git lock, so a background
        pull (startup) and a push (close) can never interleave. A push rejected
        by a concurrent writer is retried once after a ``pull --rebase`` — the
        natural reaction a single-user desktop app should have to a remote that
        moved on — before degrading to the ``git_push_failed`` flag the UI
        chip surfaces. Mirrors ``sync_git``'s semantics: git failures never
        raise. Returns True when a commit was actually created.
        """
        committed = False
        with workspace_git_lock(workspace.workflows_dir):
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
                    self._set_git_push_failed(workspace, True)
                    if not _is_push_rejection(exc):
                        logger.warning("git push failed for %s: %s", workspace.name, exc)
                        return committed
                    try:
                        logger.info(
                            "Push rejected for %s; pulling --rebase and retrying once",
                            workspace.name,
                        )
                        git_pull(workspace.workflows_dir)
                        git_push(workspace.workflows_dir)
                    except GitError as retry_exc:
                        logger.warning(
                            "git push still failed for %s: %s", workspace.name, retry_exc
                        )
                        return committed
                    logger.info("Retried push succeeded for %s", workspace.name)
                self._set_git_push_failed(workspace, False)
        return committed

    def _set_git_push_failed(self, workspace: Workspace, failed: bool) -> None:
        """Persist the push-failed flag (and mirror it on the in-memory object)."""
        workspace.git_push_failed = failed

        def apply(config: AppConfig) -> None:
            current = self._find(config, workspace.id)
            current.git_push_failed = failed

        self.store.mutate(apply)

    def git_init_workspace(self, workspace: Workspace, *, remote_url: str | None = None) -> None:
        """Initialize a git repository in the workspace folder."""
        if not git_is_repo(workspace.workflows_dir):
            git_init(workspace.workflows_dir, branch=workspace_branch(workspace.id))
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

        def apply(config: AppConfig) -> None:
            current = self._find(config, workspace.id)
            # Keep the existing CI metadata: reconfiguring the remote must not
            # silently turn the GitHub Actions workflow "off" in the app while
            # the tracked workflow file keeps running on every push.
            current.git = replace(
                current.git,
                enabled=True,
                remote_url=remote_url,
                branch=workspace_branch(current.id),
            )
            current.git_push_failed = False

        self.store.mutate(apply)

    def git_remote_url(self, workspace: Workspace) -> str | None:
        """Return the current origin URL of the workspace repository."""
        if not git_is_repo(workspace.workflows_dir):
            return None
        return git_remote_url(workspace.workflows_dir)

    def github_token(self) -> str | None:
        """Return the persisted GitHub token override, or ``None``.

        Empty in the normal case: the token is resolved from the OS Git
        credential helper / ``gh`` CLI, so nothing is configured by hand.
        """
        try:
            return self.store.load().github_token
        except Exception:
            return None

    def set_github_token(self, token: str | None) -> None:
        """Persist (or clear, when falsy) the GitHub token override."""

        def apply(config: AppConfig) -> None:
            config.github_token = token or None

        self.store.mutate(apply)
        logger.info("Updated the stored GitHub token override")

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
                db.database_name = db.database_name or DATA_DATABASE
                db.username = db.username or DATA_USER
                db.password = db.password or secrets.token_hex(16)
                self._scaffold(workspace.workflows_dir, db)
        return self.update(workspace.id, db=db)

    def configure_git(self, workspace: Workspace, *, remote_url: str | None = None) -> None:
        """Attach, update or detach the remote of an existing git repository."""
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

        def apply(config: AppConfig) -> None:
            current = self._find(config, workspace.id)
            # Keep CI metadata (enabled state + credential names) intact:
            # toggling the remote must not desync the tracked GitHub Actions
            # workflow from what the app believes is configured.
            current.git = replace(
                current.git,
                enabled=True,
                remote_url=remote_url,
                branch=workspace_branch(current.id),
            )
            current.git_push_failed = False

        self.store.mutate(apply)
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

        def apply(config: AppConfig) -> Workspace:
            current = self._find(config, workspace.id)
            current.git = replace(current.git, ci_enabled=True)
            return current

        workspace = self.store.mutate(apply)
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

        def apply(config: AppConfig) -> Workspace:
            current = self._find(config, workspace.id)
            current.git = replace(current.git, ci_enabled=False)
            return current

        workspace = self.store.mutate(apply)
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
        clean = [{"name": item.get("name"), "type": item.get("type")} for item in credentials]

        def apply(config: AppConfig) -> Workspace:
            current = self._find(config, workspace.id)
            current.git = replace(current.git, ci_credentials=clean)
            return current

        workspace = self.store.mutate(apply)
        logger.info("Recorded %d CI credential(s) for %s", len(clean), workspace.name)
        return workspace

    def save_ci_selection(
        self, workspace: Workspace, selected: set[str], *, push: bool = False
    ) -> Workspace:
        """Persist the pipeline selection and optionally commit/push it."""
        ci.write_selection(workspace.workflows_dir, set(selected))
        if push:
            self._commit_and_push(workspace, "n8n-launcher: mettre à jour les tests GitHub Actions")
        return workspace

    def ci_credentials_payload(self, workspace: Workspace, selected: list[dict[str, Any]]) -> str:
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
        wanted = {
            (name, ctype)
            for (name, ctype) in ((item.get("name"), item.get("type")) for item in selected)
            if isinstance(name, str) and isinstance(ctype, str)
        }
        listed = api.list_credentials()
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for item in listed:
            name = item.get("name")
            ctype = item.get("type")
            if not isinstance(name, str) or not isinstance(ctype, str):
                continue
            key = (name, ctype)
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

    def _ensure_workspace_branch(self, workspace: Workspace) -> None:
        """Make sure the workspace's own ``n8n/<id>`` branch is active when git is configured.

        Each workspace syncs on its own per-id branch, so a legacy checkout
        (``dev``/``main`` from before the branch policy) is switched to
        ``n8n/<id>`` on the fly, and the recorded :class:`GitConfig` branch is
        kept in sync. Failures are logged and never block startup.
        """
        if not workspace.git.enabled or not git_is_repo(workspace.workflows_dir):
            return
        branch = workspace_branch(workspace.id)
        try:
            active = ensure_workspace_branch(workspace.workflows_dir, branch)
        except GitError as exc:
            logger.warning("ensure_workspace_branch failed for %s: %s", workspace.name, exc)
            return
        if active and active != workspace.git.branch:
            workspace.git = replace(workspace.git, branch=branch)

            def apply(config: AppConfig) -> None:
                current = self._find(config, workspace.id)
                current.git = replace(current.git, branch=branch)

            self.store.mutate(apply)

    def install_server(
        self, workspace: Workspace, server: ServerConfig, *, test: bool = True
    ) -> None:
        """Install the deployment listener on the server and wire the git remote.

        The bare repository (``git init --bare``, idempotent), its
        ``post-receive`` hook and the self-contained ``deploy.py`` are generated
        and streamed over SSH (no server-side package). The local repository
        gets a ``server`` remote pointing at the bare repo, so a later
        :meth:`publish` can push ``n8n/<id>:main``. Nothing is persisted here —
        the dialog saves the :class:`ServerConfig` itself.
        """
        if conflict := self._server_port_conflict(self.store.load(), workspace.id, server):
            raise WorkspaceError(
                f"« {conflict} » utilise déjà le port {server.n8n_port} sur {server.host}."
            )
        if test:
            test_connection(server)
        base = resolve_base(server, workspace.id)
        # The post-receive hook lives in ``<base>.git/hooks``; create the bare
        # repository first so that path exists. ``--bare`` init is idempotent.
        ssh_run(server, f"git init --bare {shlex.quote(bare_dir(server, workspace.id))}")
        mkdir_remote(server, base)
        write_remote_file(
            server, f"{base}.git/hooks/post-receive", render_hook(server, workspace.id)
        )
        chmod_remote(server, f"{base}.git/hooks/post-receive")
        write_remote_file(server, f"{base}/deploy.py", render_deploy_script())
        chmod_remote(server, f"{base}/deploy.py")
        # Re-add the remote only when missing so a re-run is idempotent
        # (``git remote add`` would fail on an existing ``server`` remote).
        if (
            workspace.git.enabled
            and git_is_repo(workspace.workflows_dir)
            and git_remote_url(workspace.workflows_dir, "server") is None
        ):
            git_add_remote(
                workspace.workflows_dir, "server", server_remote_url(server, workspace.id)
            )
        logger.info("Installed server listener for %s on %s", workspace.name, server.host)

    def disable_server(self, workspace: Workspace) -> Workspace:
        """Turn the server deployment off without touching the server.

        The remote ``server`` is removed locally so nothing is pushed anymore;
        the server-side repository, hook and data are left intact (idempotent).
        """
        if (
            workspace.git.enabled
            and git_is_repo(workspace.workflows_dir)
            and git_remote_url(workspace.workflows_dir, "server") is not None
        ):
            git_remove_remote(workspace.workflows_dir, "server")

        def apply(config: AppConfig) -> Workspace:
            current = self._find(config, workspace.id)
            current.server = replace(current.server, enabled=False)
            return current

        workspace = self.store.mutate(apply)
        logger.info("Disabled server deployment for %s", workspace.name)
        return workspace

    def publish(self, workspace: Workspace) -> None:
        """Export the local state, push it to the server as ``main`` and track the result.

        Deploys the *current* state of the local n8n instance: workflows are
        exported, the remote Compose definition is committed, the credentials
        secrets are re-uploaded (they never travel through the repository) and
        the workspace branch is pushed to the ``server`` remote as ``main``
        (``n8n/<id>:main``). The push fails fast on a non-fast-forward server
        branch so production main is never rewritten.
        """
        server = workspace.server
        if not server or not server.enabled:
            raise WorkspaceError(
                "Aucun serveur configuré pour ce workspace : configurez-le avant de publier."
            )
        if not workspace.git.enabled or not git_is_repo(workspace.workflows_dir):
            raise WorkspaceError("La publication nécessite un dépôt Git configuré.")
        if not workspace.api_key:
            raise WorkspaceError(
                "Le workspace doit avoir été démarré une fois pour exporter ses credentials."
            )

        if workspace.state is WorkspaceState.RUNNING and workspace.api_key:
            try:
                pipelines_dir = workspace.workflows_dir / "n8nPipelines"
                SyncRunner(
                    self.api_factory(workspace, workspace.api_key), pipelines_dir
                ).export_all(mirror=workspace.workflows_dir)
            except Exception as exc:
                logger.warning("Export before publish failed for %s: %s", workspace.name, exc)

        # The repository hosts the *remote* Compose definition (relative mount,
        # loopback port); the local config_dir file is only for ``docker up``.
        (workspace.workflows_dir / "compose.yml").write_text(
            render_remote_compose(workspace), encoding="utf-8"
        )

        credentials = self._export_all_credentials(workspace)
        config = self.store.load()
        document = build_secrets_document(config.owner_email, config.owner_password, credentials)
        write_remote_file(
            server,
            marker_path(server, workspace.id).rsplit("/", 1)[0] + "/secrets.json",
            json.dumps(document, indent=2, ensure_ascii=False),
        )
        chmod_remote(server, self._secrets_remote_path(workspace), mode="600")

        with workspace_git_lock(workspace.workflows_dir):
            self._ensure_workspace_branch(workspace)
            self._commit_and_push(workspace, "n8n-launcher: publication")
            branch = git_current_branch(workspace.workflows_dir) or workspace_branch(workspace.id)
            try:
                git_push_ref(
                    workspace.workflows_dir,
                    "server",
                    branch,
                    "main",
                    env=git_ssh_env(server.key_path) if server.key_path else None,
                )
            except GitError as exc:
                detail = str(exc)
                if _is_push_rejection(exc):
                    message = (
                        "Le serveur refuse le push (branche main distante modifiée). "
                        "Passez en force uniquement après vérification : la branche main "
                        "du serveur est la référence de production."
                    )
                else:
                    message = f"Le push vers le serveur a échoué : {detail}"
                self._record_server_status(workspace, "error", {"error": message})
                raise WorkspaceError(message) from exc

        status, marker = self._poll_deploy(server, workspace.id)
        self._record_server_status(workspace, status, marker)
        if status == "error":
            raise WorkspaceError(marker.get("error") or "Le déploiement a échoué sur le serveur.")
        if status == "timeout":
            raise WorkspaceError(
                "Le serveur n'a pas confirmé le déploiement avant la limite de temps "
                "(vérifiez le log serveur)."
            )
        logger.info("Published %s (%s) to %s", workspace.name, branch, server.host)

    def _secrets_remote_path(self, workspace: Workspace) -> str:
        """Remote path of the workspace's secrets document."""
        base = resolve_base(workspace.server, workspace.id)
        return f"{base}/secrets.json"

    def _export_all_credentials(self, workspace: Workspace) -> list[dict[str, Any]]:
        """Export every credential's ``{name, type, data}`` from the local n8n.

        Required for a publish: the generated ``deploy.py`` recreates remote
        credentials first and rewrites the workflow node references. The values
        are read live — never persisted in the launcher — and travel only to
        ``secrets.json`` on the server.
        """
        if not workspace.api_key:
            raise WorkspaceError(
                "Le workspace doit avoir été démarré une fois pour exporter ses credentials."
            )
        api = self.api_factory(workspace, workspace.api_key)
        payload: list[dict[str, Any]] = []
        for item in api.list_credentials():
            name = item.get("name")
            ctype = item.get("type")
            if not isinstance(name, str) or not isinstance(ctype, str):
                continue
            detail = api.get_credential(str(item["id"]))
            payload.append({"name": name, "type": ctype, "data": detail.get("data") or {}})
        return payload

    def _poll_deploy(
        self,
        cfg: ServerConfig,
        workspace_id: str,
        *,
        timeout: float = 180.0,
        interval: float = 0.3,
        backoff_factor: float = 2.0,
        max_interval: float = 1.2,
    ) -> tuple[str, dict[str, Any]]:
        """Wait for the server's ``last-deploy.json`` to reach a final state.

        Returns ``("ok" | "error", marker)`` or ``("timeout", {})``. The marker
        is written by the hook (compose failures) or ``deploy.py`` (n8n/code
        failures); an absent or unparsable marker means the deployment is still
        in flight.

        The poll cadence backs off geometrically: waits start at ``interval``
        and double up to ``max_interval`` (0.3s → 0.6s → 1.2s → 1.2s…). Early
        polls are the likely winners — the hook usually flips the marker in a
        second or two — so they stay cheap, while a hung server no longer
        hammers ssh at full speed for the whole timeout.
        """
        remote_marker = marker_path(cfg, workspace_id)
        deadline = time.monotonic() + timeout
        delay = interval
        while time.monotonic() < deadline:
            result = ssh_run(cfg, f"cat {shlex.quote(remote_marker)}", check=False)
            try:
                marker = json.loads(result.stdout) if result.stdout else {}
            except ValueError:
                marker = {}
            if marker.get("status") == "ok":
                return "ok", marker
            if marker.get("status") == "error":
                return "error", marker
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_interval)
        return "timeout", {}

    def _record_server_status(
        self, workspace: Workspace, status: str, marker: dict[str, Any]
    ) -> None:
        """Persist the deploy outcome on the workspace (``server_last_*``)."""

        def apply(config: AppConfig) -> None:
            current = self._find(config, workspace.id)
            current.server_last_deploy = json.dumps(marker, ensure_ascii=False) if marker else None
            if status == "ok":
                current.server_last_error = None
            elif status == "error":
                current.server_last_error = marker.get("error") or "Échec du déploiement."
            else:
                current.server_last_error = "Le serveur n'a pas confirmé le déploiement."

        self.store.mutate(apply)

    def start(self, workspace_id: str) -> Workspace:
        """Write Compose, bring the stack up, and apply managed migrations."""
        if self._workspace_db_mode(workspace_id) is DbMode.MANAGED:
            self._ensure_managed_db_parameters(workspace_id)
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

            def mark_error(config: AppConfig) -> None:
                self._find(config, workspace_id).state = WorkspaceState.ERROR

            self.store.mutate(mark_error)
            raise

        def mark_running(config: AppConfig) -> None:
            current = self._find(config, workspace_id)
            current.state = WorkspaceState.RUNNING
            current.restart_required = False

        self.store.mutate(mark_running)
        return self._find(self.store.load(), workspace_id)

    def _workspace_db_mode(self, workspace_id: str) -> DbMode:
        """Return the persisted DB mode of a workspace (for pre-start checks)."""

        def current(config: AppConfig) -> DbMode:
            return self._find(config, workspace_id).db.mode

        return self.store.mutate(current)

    def stop_with_sync(self, workspace_id: str) -> Workspace:
        """Stop a workspace after exporting and Git-syncing its workflows.

        Mirrors the GUI close sequence (``CloseController._sync``) so that
        stopping a single workspace from its row button also persists the
        latest n8n state to the repository — without this, manual stops left
        exported changes committed only when the whole app was closed. Exports
        run best-effort: a failure is logged as a warning and never blocks the
        stop itself.
        """
        workspace = self._find(self.store.load(), workspace_id)
        if workspace.state is WorkspaceState.RUNNING and workspace.api_key:
            try:
                pipelines_dir = workspace.workflows_dir / "n8nPipelines"
                SyncRunner(
                    self.api_factory(workspace, workspace.api_key), pipelines_dir
                ).export_all(mirror=workspace.workflows_dir)
            except Exception as exc:
                logger.warning("Export before stop failed for %s: %s", workspace.name, exc)
            self.sync_git(workspace, push=True)
        return self.stop(workspace_id)

    def stop(self, workspace_id: str) -> Workspace:
        """Stop a workspace, skipping ``docker down`` when already stopped."""
        config = self.store.load()
        workspace = self._find(config, workspace_id)
        if workspace.state is WorkspaceState.STOPPED:
            return workspace
        # Surface a transient "stopping" state before the Docker teardown, so
        # the UI reflects that the workspace is not usable while ``docker down``
        # runs (the same heavier teardown as a close/stop lifecycle).
        if workspace.state is WorkspaceState.RUNNING:

            def mark_stopping(config: AppConfig) -> Workspace:
                current = self._find(config, workspace_id)
                current.state = WorkspaceState.STOPPING
                return current

            self.store.mutate(mark_stopping)
        compose = compose_file(workspace.id)
        # Skip `docker down` when already stopped: makes the call idempotent so
        # the atexit stop_all() pass after a GUI close does not tear containers
        # down a second time.
        if compose.exists():
            self.docker.down(workspace, compose, remove_orphans=True)

        def mark_stopped(config: AppConfig) -> Workspace:
            current = self._find(config, workspace_id)
            current.state = WorkspaceState.STOPPED
            return current

        return self.store.mutate(mark_stopped)

    def _ensure_db_credentials(self, workspace: Workspace) -> None:
        if workspace.db.mode is DbMode.NONE:
            return
        if workspace.api_key is None:
            # No key at all: defer to the caller (``ensure_running``) which
            # bootstraps one before reaching this point.
            return
        try:
            self._configure_credentials(workspace, workspace.api_key)
            return
        except N8nApiError as exc:
            if exc.status_code not in (401, 403):
                raise
        # The stored key is stale (401/403): rotate it once, then retry. The
        # rotation is persisted atomically so a concurrent poll never
        # resurrects the invalid key.
        config = self.store.load()
        rotated = self.owner_booter(
            f"http://127.0.0.1:{workspace.port}",
            config.owner_email,
            config.owner_password,
        )
        workspace.api_key = rotated

        def apply(config: AppConfig) -> None:
            self._find(config, workspace.id).api_key = rotated

        self.store.mutate(apply)
        self._configure_credentials(workspace, rotated)

    def _configure_credentials(self, workspace: Workspace, api_key: str) -> None:
        api = self.api_factory(workspace, api_key)
        configure_db_credential(api, workspace)

    def _ensure_managed_db_parameters(self, workspace_id: str) -> None:
        """Fill missing managed DB credentials, persisting them atomically."""

        def apply(config: AppConfig) -> None:
            workspace = self._find(config, workspace_id)
            if workspace.db.mode is not DbMode.MANAGED:
                return
            if not workspace.db.database_name:
                workspace.db.database_name = DATA_DATABASE
            if not workspace.db.username:
                workspace.db.username = DATA_USER
            if not workspace.db.password:
                workspace.db.password = secrets.token_hex(16)

        self.store.mutate(apply)

    @staticmethod
    def _managed_db_config() -> DbConfig:
        return DbConfig(
            mode=DbMode.MANAGED,
            database_name=DATA_DATABASE,
            username=DATA_USER,
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

    @staticmethod
    def _server_port_conflict(
        config: AppConfig, workspace_id: str, server: ServerConfig
    ) -> str | None:
        """Return the *name* of another workspace bound to the same host:port."""
        if not server.enabled:
            return None
        for other in config.workspaces:
            if (
                other.id != workspace_id
                and other.server.enabled
                and (other.server.host, other.server.n8n_port) == (server.host, server.n8n_port)
            ):
                return other.name
        return None
