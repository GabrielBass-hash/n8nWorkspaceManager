"""Database discovery and migration helpers."""

from __future__ import annotations

from pathlib import Path


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
