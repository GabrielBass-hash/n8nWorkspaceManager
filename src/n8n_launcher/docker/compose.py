"""Render an isolated Docker Compose project for one workspace."""

from __future__ import annotations

from pathlib import Path

from ..core.models import DEFAULT_POSTGRES_IMAGE, DbMode, Workspace


def compose_project_name(workspace: Workspace) -> str:
    """Return the isolated ``docker compose`` project name for a workspace."""
    return f"n8n-ws-{workspace.id}"


def render_compose(workspace: Workspace) -> str:
    """Return the complete Docker Compose YAML for the given workspace."""
    workflows_dir = _compose_path(workspace.workflows_dir)
    n8n_image = f"n8nio/n8n:{workspace.n8n_version}"
    db_environment, db_service, db_dependency = _database_parts(workspace)
    data_volumes = f"  n8ndata-{workspace.id}:\n"
    if workspace.db.mode is DbMode.MANAGED:
        data_volumes += f"  pgdata-{workspace.id}:\n"
    service_block = f"""  n8n:
    image: {n8n_image}
    restart: unless-stopped
    ports:
      - \"{workspace.port}:5678\"
    environment:
{db_environment}      N8N_PORT: \"5678\"
      N8N_PROTOCOL: http
      N8N_SECURE_COOKIE: \"false\"
    volumes:
      - n8ndata-{workspace.id}:/home/node/.n8n
      - \"{workflows_dir}:/workflows\"
{db_dependency}    healthcheck:
      test: [\"CMD-SHELL\", \"wget --spider -q http://127.0.0.1:5678/healthz || exit 1\"]
      interval: 5s
      timeout: 3s
      retries: 20
"""
    return f"""services:
{db_service}{service_block}
volumes:
{data_volumes}"""


def write_compose(workspace: Workspace, output: Path) -> Path:
    """Render and write the Compose YAML, creating intermediate directories."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_compose(workspace), encoding="utf-8")
    return output


def _database_parts(workspace: Workspace) -> tuple[str, str, str]:
    if workspace.db.mode is not DbMode.MANAGED:
        return "", "", ""
    environment = """      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: postgres
      DB_POSTGRESDB_PORT: \"5432\"
      DB_POSTGRESDB_DATABASE: n8n
      DB_POSTGRESDB_USER: n8n
      DB_POSTGRESDB_PASSWORD: launcher-managed
"""
    postgres_image = workspace.postgres_image or DEFAULT_POSTGRES_IMAGE
    # TimescaleDB must be listed in shared_preload_libraries before the
    # server starts; without this the CREATE EXTENSION call fails hard.
    command_block = ""
    if workspace.postgres_preload_timescaledb:
        command_block = '    command: ["postgres", "-c", "shared_preload_libraries=timescaledb"]\n'
    service = f"""  postgres:
    image: {postgres_image}
    restart: unless-stopped
{command_block}    environment:
      POSTGRES_DB: n8n
      POSTGRES_USER: n8n
      POSTGRES_PASSWORD: launcher-managed
    volumes:
      - pgdata-{workspace.id}:/var/lib/postgresql/data
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U n8n -d n8n\"]
      interval: 5s
      timeout: 3s
      retries: 20
"""
    dependency = "    depends_on:\n      postgres:\n        condition: service_healthy\n"
    return environment, service, dependency


def _compose_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')
