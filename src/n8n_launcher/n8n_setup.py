"""Automatic n8n-side setup for workspace databases (credentials + connection info)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

from .api_client import N8nApiClient
from .models import DbConfig, DbMode, Workspace


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str
    user: str
    password: str


def build_external_connection_string(
    *,
    host: str,
    port: int | str,
    database: str,
    user: str,
    password: str,
) -> str:
    """Build a PostgreSQL connection string from individual fields.

    Percent-encodes credentials so special characters are handled safely by
    both Docker Compose and the ``urlparse``-based parser.
    """
    port_int = int(port) if str(port).strip() else 5432
    encoded_user = quote(str(user), safe="")
    encoded_password = quote(str(password), safe="")
    return f"postgresql://{encoded_user}:{encoded_password}@{host}:{port_int}/{database}"


def data_db_target(workspace: Workspace) -> DatabaseTarget | None:
    if workspace.db.mode is DbMode.MANAGED:
        return DatabaseTarget(
            host="postgres",
            port=5432,
            database=workspace.db.database_name or "data",
            user=workspace.db.username or "n8ndata",
            password=workspace.db.password or "launcher-managed-data",
        )
    if workspace.db.mode is DbMode.EXTERNAL:
        if not (workspace.db.connection_string and workspace.db.connection_string.strip()):
            return None
        parsed = urlparse(workspace.db.connection_string)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            return None
        return DatabaseTarget(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=unquote(parsed.path.lstrip("/")) or "postgres",
            user=unquote(parsed.username or "postgres"),
            password=unquote(parsed.password or ""),
        )
    return None


def configure_db_credential(api: N8nApiClient, workspace: Workspace) -> bool:
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