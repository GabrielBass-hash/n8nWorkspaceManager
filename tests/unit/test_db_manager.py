from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from n8n_launcher.database import MigrationRunner
from n8n_launcher.db_manager import (
    DatabaseError,
    apply_migrations,
    detect_migrations,
    has_db_layout,
    validate_external,
)
from n8n_launcher.models import DbConfig, DbMode, Workspace


def test_detect_migrations_is_sorted_and_ignores_other_files(tmp_path: Path) -> None:
    migrations = tmp_path / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "002-second.sql").write_text("select 2;", encoding="utf-8")
    (migrations / "001-first.sql").write_text("select 1;", encoding="utf-8")
    (migrations / "README.md").write_text("ignored", encoding="utf-8")

    assert [path.name for path in detect_migrations(tmp_path)] == ["001-first.sql", "002-second.sql"]


def test_has_db_layout_detects_schema_and_migrations(tmp_path: Path) -> None:
    assert has_db_layout(tmp_path) is False

    (tmp_path / "db").mkdir(exist_ok=True)
    (tmp_path / "db" / "schema.sql").write_text("select 1;", encoding="utf-8")
    assert has_db_layout(tmp_path) is True

    (tmp_path / "db" / "schema.sql").unlink()
    assert has_db_layout(tmp_path) is False


def test_validate_external_executes_select_one() -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value

    with patch("n8n_launcher.db_manager.psycopg.connect", return_value=connection) as connect:
        validate_external("postgresql://user:secret@db.example/app")

    connect.assert_called_once_with("postgresql://user:secret@db.example/app", connect_timeout=5)
    cursor.execute.assert_called_once_with("SELECT 1")


def test_apply_migrations_runs_in_order(tmp_path: Path) -> None:
    first = tmp_path / "001-first.sql"
    second = tmp_path / "002-second.sql"
    first.write_text("create table first;", encoding="utf-8")
    second.write_text("create table second;", encoding="utf-8")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value

    apply_migrations("postgresql://db/app", [first, second], connect=MagicMock(return_value=connection))

    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "create table first;",
        "create table second;",
    ]
    connection.commit.assert_called_once_with()


def test_empty_external_connection_is_rejected() -> None:
    with pytest.raises(DatabaseError, match="empty"):
        validate_external("  ")


def test_ensure_sends_semicolon_terminated_gexec_scripts(tmp_path: Path) -> None:
    docker = MagicMock()
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(
            mode=DbMode.MANAGED,
            database_name="data",
            username="n8ndata",
            password="p'w",
        ),
    )

    MigrationRunner(docker).ensure(workspace, tmp_path / "compose.yaml")

    sent = docker.exec_psql.call_args.kwargs["stdin"]
    assert ";\n\\gexec\n" in sent
    assert sent.count("\n\\gexec\n") == 2
    assert "format('CREATE ROLE %I LOGIN SUPERUSER PASSWORD %L'," in sent
    assert "format('CREATE DATABASE %I OWNER %I'," in sent
    assert "'n8ndata'" in sent
    assert "p''w" in sent
    assert docker.exec_psql.call_args.kwargs["database"] == "postgres"
    assert docker.exec_psql.call_args.kwargs["user"] == "n8n"
