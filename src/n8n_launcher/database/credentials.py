"""Per-workspace data-database connection parameters and n8n credentials."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import (
    DbMode,
    Workspace,
    WORKSPACE_DATA_DB_NAME,
    WORKSPACE_DATA_DB_PASSWORD,
    WORKSPACE_DATA_DB_USER,
)
from ..n8n.api import N8nApiClient


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
            database=workspace.db.database_name or WORKSPACE_DATA_DB_NAME,
            user=workspace.db.username or WORKSPACE_DATA_DB_USER,
            password=workspace.db.password or WORKSPACE_DATA_DB_PASSWORD,
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
