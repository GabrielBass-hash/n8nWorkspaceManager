"""Domain models used by the launcher."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class DbMode(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"
    NONE = "none"


class WorkspaceState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class DbConfig:
    mode: DbMode
    connection_string: str | None = None
    database_name: str | None = None
    username: str | None = None
    password: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"mode": self.mode.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DbConfig":
        return cls(
            mode=DbMode(data["mode"]),
            connection_string=data.get("connection_string"),
            database_name=data.get("database_name"),
            username=data.get("username"),
            password=data.get("password"),
        )


@dataclass
class Workspace:
    id: str
    name: str
    workflows_dir: Path
    port: int
    db: DbConfig
    n8n_version: str = "2.33.3"
    state: WorkspaceState = WorkspaceState.STOPPED
    restart_required: bool = False
    api_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["workflows_dir"] = str(self.workflows_dir)
        data["db"] = self.db.to_dict()
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workspace":
        return cls(
            id=data["id"],
            name=data["name"],
            workflows_dir=Path(data["workflows_dir"]),
            port=int(data["port"]),
            db=DbConfig.from_dict(data["db"]),
            n8n_version=data.get("n8n_version", "2.33.3"),
            state=WorkspaceState(data.get("state", WorkspaceState.STOPPED)),
            restart_required=bool(data.get("restart_required", False)),
            api_key=data.get("api_key"),
        )


@dataclass
class AppConfig:
    owner_email: str
    owner_password: str
    work_dir: Path
    workspaces: list[Workspace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_email": self.owner_email,
            "owner_password": self.owner_password,
            "work_dir": str(self.work_dir),
            "workspaces": [workspace.to_dict() for workspace in self.workspaces],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        return cls(
            owner_email=data["owner_email"],
            owner_password=data["owner_password"],
            work_dir=Path(data["work_dir"]),
            workspaces=[Workspace.from_dict(item) for item in data.get("workspaces", [])],
        )
