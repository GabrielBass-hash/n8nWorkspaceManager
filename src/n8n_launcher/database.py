"""Local PostgreSQL data-store setup and idempotent migration application."""

from __future__ import annotations

import time
from pathlib import Path

from .docker_manager import DockerError, DockerManager
from .models import DbConfig, DbMode, Workspace

METADATA_USER = "n8n"
METADATA_PASSWORD = "launcher-managed"
MAINTENANCE_DATABASE = "postgres"


class MigrationError(RuntimeError):
    """Raised when local data-database migrations cannot be applied."""


def data_db_parameters(workspace: Workspace) -> dict[str, str]:
    if workspace.db.mode is DbMode.MANAGED:
        return {
            "database": workspace.db.database_name or "data",
            "user": workspace.db.username or "n8ndata",
            "password": workspace.db.password or "launcher-managed-data",
        }
    raise MigrationError("Managed data-database parameters require MANAGED mode")


class MigrationRunner:
    """Create the data database and apply pending migrations idempotently."""

    def __init__(self, docker: DockerManager) -> None:
        self.docker = docker

    def ensure(self, workspace: Workspace, compose_file: Path) -> None:
        if workspace.db.mode is not DbMode.MANAGED:
            return
        parameters = data_db_parameters(workspace)
        user = parameters["user"]
        password = parameters["password"]
        database = parameters["database"]
        role_module = (
            "SELECT format('CREATE ROLE %I LOGIN SUPERUSER PASSWORD %L', "
            + _sql_literal(user)
            + ", "
            + _sql_literal(password)
            + ") WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname="
            + _sql_literal(user)
            + ");\n\\gexec\n"
        )
        database_module = (
            "SELECT format('CREATE DATABASE %I OWNER %I', "
            + _sql_literal(database)
            + ", "
            + _sql_literal(user)
            + ") WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname="
            + _sql_literal(database)
            + ");\n\\gexec\n"
        )
        self._run_with_retries(
            workspace,
            compose_file,
            database=MAINTENANCE_DATABASE,
            user=METADATA_USER,
            password=METADATA_PASSWORD,
            stdin=role_module + database_module,
        )

    def apply(self, workspace: Workspace, migrations_dir: Path, compose_file: Path) -> list[str]:
        migrations = sorted(
            (path for path in migrations_dir.glob("*.sql") if path.is_file()),
            key=lambda path: path.name,
        )
        if not migrations:
            return []
        parameters = data_db_parameters(workspace)
        applied_names = self.applied(workspace, compose_file)
        pending = [migration for migration in migrations if migration.name not in applied_names]
        for migration in pending:
            body = (
                "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
                "filename text PRIMARY KEY,\n"
                "applied_at timestamptz NOT NULL DEFAULT now());\n"
                + migration.read_text(encoding="utf-8")
                + "\nINSERT INTO public.schema_migrations(filename) VALUES ("
                + _sql_literal(migration.name)
                + ");\n"
            )
            self._run_with_retries(
                workspace,
                compose_file,
                database=parameters["database"],
                user=parameters["user"],
                password=parameters["password"],
                stdin=body,
            )
        return [migration.name for migration in pending]

    def applied(self, workspace: Workspace, compose_file: Path) -> set[str]:
        parameters = data_db_parameters(workspace)
        try:
            result = self._run_with_retries(
                workspace,
                compose_file,
                database=parameters["database"],
                user=parameters["user"],
                password=parameters["password"],
                stdin=(
                    "SELECT filename FROM public.schema_migrations ORDER BY filename;\n"
                ),
                check=False,
            )
        except DockerError:
            return set()
        # With ``check=False`` the retry helper returns the last error object
        # instead of raising it when PostgreSQL stays unreachable.
        if isinstance(result, DockerError):
            return set()
        if result.returncode != 0:
            return set()
        output = result.stdout or ""
        if not isinstance(output, str):
            return set()
        return {line.strip() for line in output.splitlines() if line.strip()}

    def _run_with_retries(
        self,
        workspace: Workspace,
        compose_file: Path,
        *,
        database: str,
        user: str,
        password: str,
        stdin: str,
        check: bool = True,
        attempts: int = 12,
        interval: float = 2.0,
    ) -> object:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                return self.docker.exec_psql(
                    workspace,
                    compose_file,
                    database=database,
                    user=user,
                    password=password,
                    stdin=stdin,
                    check=check,
                )
            except DockerError as exc:
                last_error = exc
                detail = str(exc).lower()
                if "starting up" in detail or "refused" in detail or "not reachable" in detail:
                    time.sleep(interval)
                    continue
                if check:
                    raise
                time.sleep(interval)
                continue
        if last_error is None:
            raise DockerError("PostgreSQL did not become reachable for migrations")
        if check:
            raise last_error
        return last_error


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"