"""Validate external PostgreSQL connection and migration application."""

import subprocess
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from n8n_launcher.db_manager import DatabaseError, apply_migrations, validate_external
from n8n_launcher.ports import suggest_port

pytestmark = pytest.mark.integration


def docker_run(arguments: list[str]) -> None:
    subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=180.0,
        check=True,
    )


def wait_for_connection(connection_string: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            validate_external(connection_string, timeout=2.0)
            return
        except DatabaseError:
            time.sleep(1.0)
    raise AssertionError(f"PostgreSQL did not become reachable: {connection_string}")


@pytest.mark.timeout(180)
def test_external_postgres_validation_and_migrations(tmp_path: Path) -> None:
    port = suggest_port(requested=5400)
    container = f"n8n-launcher-pg-{uuid.uuid4().hex[:6]}"
    connection_string = f"postgresql://n8n:secret@127.0.0.1:{port}/n8n"
    docker_run([
        "run", "-d",
        "--name", container,
        "-p", f"{port}:5432",
        "-e", "POSTGRES_DB=n8n",
        "-e", "POSTGRES_USER=n8n",
        "-e", "POSTGRES_PASSWORD=secret",
        "postgres:16",
    ])
    try:
        wait_for_connection(connection_string, timeout=90.0)
        validate_external(connection_string, timeout=5.0)

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001-init.sql").write_text(
            "CREATE TABLE entries (id serial PRIMARY KEY, payload text NOT NULL);",
            encoding="utf-8",
        )
        (migrations_dir / "002-index.sql").write_text(
            "CREATE INDEX entries_payload_idx ON entries (payload);",
            encoding="utf-8",
        )
        apply_migrations(connection_string, [
            migrations_dir / "001-init.sql",
            migrations_dir / "002-index.sql",
        ])

        with psycopg.connect(connection_string) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.entries')")
                assert cursor.fetchone()[0] == "entries"
                cursor.execute("SELECT to_regclass('public.entries_payload_idx')")
                assert cursor.fetchone()[0] == "entries_payload_idx"
    finally:
        docker_run(["rm", "-f", container])