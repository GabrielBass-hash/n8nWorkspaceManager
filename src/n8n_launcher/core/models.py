"""Domain models used by the launcher."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DbMode(StrEnum):
    """How a workspace accesses its database.

    ``MANAGED`` runs a local Postgres service inside the Compose project;
    ``NONE`` runs n8n without any database service.
    """

    MANAGED = "managed"
    NONE = "none"


class WorkspaceState(StrEnum):
    """Lifecycle states the launcher can persist or display."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


DEFAULT_POSTGRES_IMAGE = "postgres:16"


@dataclass
class GitConfig:
    enabled: bool = False
    remote_url: str | None = None
    branch: str = "main"
    ci_enabled: bool = False
    ci_credentials: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GitConfig:
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            remote_url=data.get("remote_url"),
            branch=data.get("branch", "main"),
            ci_enabled=bool(data.get("ci_enabled", False)),
            # Legacy configs have no credential metadata; they degrade to empty.
            ci_credentials=list(data.get("ci_credentials") or []),
        )


@dataclass
class DbConfig:
    """Database mode plus the parameters needed for a managed data database."""

    mode: DbMode
    database_name: str | None = None
    username: str | None = None
    password: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict with the mode as its string value."""
        return asdict(self) | {"mode": self.mode.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DbConfig:
        """Deserialize, mapping any unknown legacy mode to ``NONE``."""
        raw_mode = data.get("mode", DbMode.NONE)
        try:
            mode = DbMode(raw_mode)
        except ValueError:
            # Legacy "external" configs fall back to NONE.
            mode = DbMode.NONE
        return cls(
            mode=mode,
            database_name=data.get("database_name"),
            username=data.get("username"),
            password=data.get("password"),
        )


@dataclass
class Workspace:
    """A user-defined n8n instance bound to a folder of workflow exports."""

    id: str
    name: str
    workflows_dir: Path
    port: int
    db: DbConfig
    git: GitConfig = field(default_factory=GitConfig)
    git_push_failed: bool = False
    n8n_version: str = "2.40.0"
    postgres_image: str | None = None
    postgres_preload_timescaledb: bool = False
    state: WorkspaceState = WorkspaceState.STOPPED
    restart_required: bool = False
    api_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict (paths and enums as strings)."""
        data = asdict(self)
        data["workflows_dir"] = str(self.workflows_dir)
        data["db"] = self.db.to_dict()
        data["git"] = self.git.to_dict()
        data["state"] = self.state.value
        data["postgres_image"] = self.postgres_image
        data["postgres_preload_timescaledb"] = self.postgres_preload_timescaledb
        data["git_push_failed"] = self.git_push_failed
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        """Deserialize, defaulting every optional/legacy field gracefully.

        Unknown states degrade to ``STOPPED`` and a missing ``db`` block to
        ``DbMode.NONE`` so a partially-written entry keeps reading instead of
        making the whole config unreadable (which used to push the app into
        the first-launch wizard and silently wipe the workspace list).
        """
        try:
            state = WorkspaceState(data.get("state", WorkspaceState.STOPPED))
        except (TypeError, ValueError):
            state = WorkspaceState.STOPPED
        db_data = data.get("db")
        if not isinstance(db_data, dict):
            db_data = {}
        return cls(
            id=data["id"],
            name=data["name"],
            workflows_dir=Path(data["workflows_dir"]),
            port=int(data["port"]),
            db=DbConfig.from_dict(db_data),
            git=GitConfig.from_dict(data.get("git")),
            n8n_version=data.get("n8n_version", "2.33.3"),
            postgres_image=data.get("postgres_image"),
            postgres_preload_timescaledb=bool(data.get("postgres_preload_timescaledb", False)),
            state=state,
            restart_required=bool(data.get("restart_required", False)),
            api_key=data.get("api_key"),
            git_push_failed=bool(data.get("git_push_failed", False)),
        )


@dataclass
class AppConfig:
    """Global launcher configuration: owner identity, work dir, workspaces."""

    owner_email: str
    owner_password: str
    work_dir: Path
    workspaces: list[Workspace] = field(default_factory=list)
    # Optional GitHub PAT override. Left empty in the normal case: the token is
    # resolved from the OS Git credential helper (the one ``git push`` uses) or
    # the ``gh`` CLI, and only stored here when the user ticks "Se souvenir".
    github_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "owner_email": self.owner_email,
            "owner_password": self.owner_password,
            "work_dir": str(self.work_dir),
            "workspaces": [workspace.to_dict() for workspace in self.workspaces],
            "github_token": self.github_token,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        """Deserialize an :class:`AppConfig` from parsed JSON.

        Workspace parsing is tolerant per entry: a malformed workspace is
        skipped with a warning (the healthy ones still load) and a missing or
        non-list ``workspaces`` key behaves as an empty list. Without this, a
        single broken entry made the whole file unreadable and ``main()``
        treated it as a first launch, wiping the list via ``store.save()``.
        """
        raw_workspaces = data.get("workspaces") or []
        if not isinstance(raw_workspaces, list):
            raw_workspaces = []
        workspaces: list[Workspace] = []
        for item in raw_workspaces:
            if not isinstance(item, dict):
                logger.warning("Skipping non-object workspace entry: %r", item)
                continue
            try:
                workspaces.append(Workspace.from_dict(item))
            except Exception as exc:
                logger.warning("Skipping malformed workspace %r: %s", item.get("id"), exc)
        return cls(
            owner_email=data["owner_email"],
            owner_password=data["owner_password"],
            work_dir=Path(data["work_dir"]),
            workspaces=workspaces,
            github_token=data.get("github_token"),
        )
