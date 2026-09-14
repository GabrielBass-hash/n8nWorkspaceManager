"""Database discovery, validation, and migration helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psycopg


class DatabaseError(RuntimeError):
    """Raised when a database cannot be validated or migrated."""


def detect_migrations(workflows_dir: Path) -> list[Path]:
    migrations_dir = workflows_dir / "db" / "migrations"
    if not migrations_dir.is_dir():
        return []
    return sorted(
        (path for path in migrations_dir.glob("*.sql") if path.is_file()),
        key=lambda path: path.name,
    )


def detect_schema(workflows_dir: Path) -> Path | None:
    schema = workflows_dir / "db" / "schema.sql"
    if schema.is_file():
        return schema
    return None


def has_db_layout(workflows_dir: Path) -> bool:
    if detect_schema(workflows_dir) is not None:
        return True
    return bool(detect_migrations(workflows_dir))


def validate_external(connection_string: str, timeout: float = 5.0) -> None:
    if not connection_string.strip():
        raise DatabaseError("External database connection string is empty")
    try:
        with psycopg.connect(connection_string, connect_timeout=max(1, int(timeout))) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except Exception as exc:
        raise DatabaseError(f"External database validation failed: {exc}") from exc


def apply_migrations(
    connection_string: str,
    migrations: Iterable[Path],
    *,
    connect: Callable[..., Any] = psycopg.connect,
) -> None:
    migration_paths = list(migrations)
    try:
        with connect(connection_string) as connection:
            with connection.cursor() as cursor:
                for migration in migration_paths:
                    try:
                        cursor.execute(migration.read_text(encoding="utf-8"))
                    except Exception as exc:
                        connection.rollback()
                        raise DatabaseError(f"Migration failed: {migration.name}") from exc
            connection.commit()
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"Database migration failed: {exc}") from exc
