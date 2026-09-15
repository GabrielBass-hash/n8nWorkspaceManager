"""Automatic n8n-side setup for workspace databases (credentials + connection info)."""

from __future__ import annotations

from dataclasses import dataclass

from .api_client import N8nApiClient
from .models import DbConfig, DbMode, Workspace


@dataclass(frozen=True)
class DatabaseTarget:
    """Connection parameters for a workspace's managed Postgres service."""

    host: str
    port: int
    database: str
    user: str
    password: str


def data_db_target(workspace: Workspace) -> DatabaseTarget | None:
    """Return the Postgres connection target for a MANAGED workspace, else None."""
    if workspace.db.mode is DbMode.MANAGED:
        return DatabaseTarget(
            host="postgres",
            port=5432,
            database=workspace.db.database_name or "data",
            user=workspace.db.username or "n8ndata",
            password=workspace.db.password or "launcher-managed-data",
        )
    return None


def configure_db_credential(api: N8nApiClient, workspace: Workspace) -> bool:
    """Ensure the workspace's Postgres credential exists inside n8n.

    Returns True when a credential was created or already existed.
    """
    target = data_db_target(workspace)
    if target is None:
        return False
    api.ensure_postgres_credential(
        f"workspace-{workspace.name}",
        host=target.host,
        port=target.port,
        database=target.database,
        user=target.user,
        password=target.password,
    )
    return True