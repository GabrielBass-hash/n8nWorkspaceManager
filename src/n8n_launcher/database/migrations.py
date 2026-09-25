"""Creation of the data database and idempotent migration application."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..core.models import (
    MAINTENANCE_DATABASE,
    N8N_METADATA_DB_PASSWORD,
    N8N_METADATA_DB_USER,
    DbMode,
    Workspace,
)
from ..docker.manager import DockerError, DockerManager
from .credentials import data_db_target
from .layout import migrate_files


class MigrationError(RuntimeError):
    """Raised when local data-database migrations cannot be applied."""


_BOOKKEEPING_DDL = (
    "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
    "filename text PRIMARY KEY,\n"
    "applied_at timestamptz NOT NULL DEFAULT now());\n"
)


class MigrationRunner:
    """Create the data database and apply pending migrations idempotently."""

    def __init__(self, docker: DockerManager) -> None:
        self.docker = docker

    def ensure(self, workspace: Workspace, compose_file: Path) -> None:
        """Create the data role and database if they do not exist yet."""
        if workspace.db.mode is not DbMode.MANAGED:
            return
        target = data_db_target(workspace)
        if target is None:
            raise MigrationError("cannot run migrations without a managed database target")
        # A plain LOGIN role owning its database is enough for schema/migration
        # work. TimescaleDB nevertheless requires a SUPERUSER to run
        # ``CREATE EXTENSION timescaledb``, so keep the privilege in that case.
        privileges = "LOGIN SUPERUSER" if workspace.postgres_preload_timescaledb else "LOGIN"
        role_module = (
            "SELECT format('CREATE ROLE %I "
            + privileges
            + " PASSWORD %L', "
            + _sql_literal(target.user)
            + ", "
            + _sql_literal(target.password)
            + ") WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname="
            + _sql_literal(target.user)
            + ");\n\\gexec\n"
        )
        reconcile_role_module = (
            "SELECT format('ALTER ROLE %I WITH "
            + privileges
            + " PASSWORD %L', "
            + _sql_literal(target.user)
            + ", "
            + _sql_literal(target.password)
            + ");\n\\gexec\n"
        )
        database_module = (
            "SELECT format('CREATE DATABASE %I OWNER %I', "
            + _sql_literal(target.database)
            + ", "
            + _sql_literal(target.user)
            + ") WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname="
            + _sql_literal(target.database)
            + ");\n\\gexec\n"
        )
        self._run_with_retries(
            workspace,
            compose_file,
            database=MAINTENANCE_DATABASE,
            user=N8N_METADATA_DB_USER,
            password=N8N_METADATA_DB_PASSWORD,
            stdin=role_module + reconcile_role_module + database_module,
        )

    def apply(self, workspace: Workspace, migrations_dir: Path, compose_file: Path) -> list[str]:
        """Apply pending migrations and return their filenames in order."""
        migrations = migrate_files(migrations_dir)
        if not migrations:
            return []
        target = data_db_target(workspace)
        if target is None:
            raise MigrationError("cannot run migrations without a managed database target")
        applied_names = self.applied(workspace, compose_file)
        pending = [migration for migration in migrations if migration.name not in applied_names]
        for migration in pending:
            body = (
                "BEGIN;\n"
                + _BOOKKEEPING_DDL
                + migration.read_text(encoding="utf-8")
                + "\nINSERT INTO public.schema_migrations(filename) VALUES ("
                + _sql_literal(migration.name)
                + ");\nCOMMIT;\n"
            )
            self._run_with_retries(
                workspace,
                compose_file,
                database=target.database,
                user=target.user,
                password=target.password,
                stdin=body,
            )
        return [migration.name for migration in pending]

    def applied(self, workspace: Workspace, compose_file: Path) -> set[str]:
        """Return the set of migration filenames already recorded as applied."""
        target = data_db_target(workspace)
        if target is None:
            raise MigrationError("cannot list applied migrations without a managed database target")
        self._run_with_retries(
            workspace,
            compose_file,
            database=target.database,
            user=target.user,
            password=target.password,
            stdin=_BOOKKEEPING_DDL,
        )
        result = self._run_with_retries(
            workspace,
            compose_file,
            database=target.database,
            user=target.user,
            password=target.password,
            stdin="SELECT filename FROM public.schema_migrations ORDER BY filename;\n",
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise MigrationError(f"cannot list applied migrations: {detail}")
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _run_with_retries(
        self,
        workspace: Workspace,
        compose_file: Path,
        *,
        database: str,
        user: str,
        password: str,
        stdin: str,
        attempts: int = 12,
        interval: float = 2.0,
    ) -> subprocess.CompletedProcess[str]:
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
                    check=True,
                )
            except DockerError as exc:
                last_error = exc
                detail = str(exc).lower()
                if "starting up" in detail or "refused" in detail or "not reachable" in detail:
                    time.sleep(interval)
                    continue
                raise MigrationError(str(exc)) from exc
        if last_error is None:
            raise MigrationError("PostgreSQL n'est pas devenu joignable pour les migrations")
        raise MigrationError(str(last_error)) from last_error


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
