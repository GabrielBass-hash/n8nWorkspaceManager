from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.database import (
    DatabaseTarget,
    MigrationError,
    MigrationRunner,
    data_db_target,
    detect_migrations,
    detect_schema,
    has_db_layout,
)
from n8n_launcher.docker.manager import DockerError


def completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(["psql"], returncode, stdout, stderr)


def test_detect_migrations_is_sorted_and_ignores_other_files(tmp_path: Path) -> None:
    migrations = tmp_path / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "002-second.sql").write_text("select 2;", encoding="utf-8")
    (migrations / "001-first.sql").write_text("select 1;", encoding="utf-8")
    (migrations / "README.md").write_text("ignored", encoding="utf-8")

    assert [path.name for path in detect_migrations(tmp_path)] == [
        "001-first.sql",
        "002-second.sql",
    ]


def test_has_db_layout_detects_schema_and_migrations(tmp_path: Path) -> None:
    assert has_db_layout(tmp_path) is False

    (tmp_path / "db").mkdir(exist_ok=True)
    (tmp_path / "db" / "schema.sql").write_text("select 1;", encoding="utf-8")
    assert has_db_layout(tmp_path) is True

    (tmp_path / "db" / "schema.sql").unlink()
    assert has_db_layout(tmp_path) is False

    migrations = tmp_path / "db" / "migrations"
    migrations.mkdir()
    (migrations / "001.sql").write_text("select 1;", encoding="utf-8")
    assert has_db_layout(tmp_path) is True


def test_detect_schema_returns_file_or_none(tmp_path: Path) -> None:
    assert detect_schema(tmp_path) is None

    schema = tmp_path / "db" / "schema.sql"
    schema.parent.mkdir(parents=True)
    schema.write_text("select 1;", encoding="utf-8")

    assert detect_schema(tmp_path) == schema


def test_ensure_is_noop_without_managed_mode(tmp_path: Path) -> None:
    docker = MagicMock()
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(DbMode.NONE),
    )

    MigrationRunner(docker).ensure(workspace, tmp_path / "compose.yaml")

    docker.exec_psql.assert_not_called()


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

    docker.exec_psql.return_value = completed()
    MigrationRunner(docker).ensure(workspace, tmp_path / "compose.yaml")

    sent = docker.exec_psql.call_args.kwargs["stdin"]
    assert ";\n\\gexec\n" in sent
    assert sent.count("\n\\gexec\n") == 3
    # The data role is a plain LOGIN: ownership of its database is enough for
    # schema and migration work, there is no need for superuser privileges.
    assert "format('CREATE ROLE %I LOGIN PASSWORD %L'," in sent
    assert "format('ALTER ROLE %I WITH LOGIN PASSWORD %L'," in sent
    assert "LOGIN SUPERUSER" not in sent
    assert "format('CREATE DATABASE %I OWNER %I'," in sent
    assert "'n8ndata'" in sent
    assert "p''w" in sent
    assert docker.exec_psql.call_args.kwargs["database"] == "postgres"
    assert docker.exec_psql.call_args.kwargs["user"] == "n8n"


def test_ensure_keeps_superuser_for_timescale_workspace(tmp_path: Path) -> None:
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
            password="pw",
        ),
        postgres_preload_timescaledb=True,
    )

    docker.exec_psql.return_value = completed()
    MigrationRunner(docker).ensure(workspace, tmp_path / "compose.yaml")

    sent = docker.exec_psql.call_args.kwargs["stdin"]
    assert "CREATE ROLE %I LOGIN SUPERUSER PASSWORD %L" in sent
    assert "ALTER ROLE %I WITH LOGIN SUPERUSER PASSWORD %L" in sent


def test_apply_is_noop_without_migration_files(tmp_path: Path) -> None:
    docker = MagicMock()
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(DbMode.MANAGED),
    )

    applied = MigrationRunner(docker).apply(
        workspace, tmp_path / "db" / "migrations", tmp_path / "compose.yaml"
    )

    assert applied == []
    docker.exec_psql.assert_not_called()


def test_apply_qualifies_schema_migrations_bookkeeping(tmp_path: Path) -> None:
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
            password="pw",
        ),
    )
    migrations_dir = tmp_path / "db" / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001-init.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS app;\nCREATE TABLE app.things (id int);\n",
        encoding="utf-8",
    )
    (migrations_dir / "002-more.sql").write_text(
        "CREATE TABLE app.more (id int);\n", encoding="utf-8"
    )
    docker.exec_psql.side_effect = [completed(), completed(), completed(), completed()]

    applied = MigrationRunner(docker).apply(workspace, migrations_dir, tmp_path / "compose.yaml")

    assert applied == ["001-init.sql", "002-more.sql"]
    bodies = [
        call.kwargs["stdin"]
        for call in docker.exec_psql.call_args_list
        if call.kwargs["stdin"].startswith("BEGIN;\n")
    ]
    assert len(bodies) == 2
    assert "CREATE TABLE IF NOT EXISTS public.schema_migrations (" in bodies[0]
    assert (
        "\nINSERT INTO public.schema_migrations(filename) VALUES ('001-init.sql');\n" in bodies[0]
    )
    assert "set_config('search_path'" not in bodies[0]
    assert bodies[0].startswith("BEGIN;\n")
    assert bodies[0].endswith("COMMIT;\n")
    assert "app.things" in bodies[0]
    assert "app.more" in bodies[1]


def test_apply_skips_already_applied_migrations(tmp_path: Path) -> None:
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
            password="pw",
        ),
    )
    migrations_dir = tmp_path / "db" / "migrations"
    migrations_dir.mkdir(parents=True)
    (migrations_dir / "001-init.sql").write_text("select 1;", encoding="utf-8")
    docker.exec_psql.side_effect = [completed(), completed("001-init.sql\n")]

    applied = MigrationRunner(docker).apply(workspace, migrations_dir, tmp_path / "compose.yaml")

    assert applied == []
    assert docker.exec_psql.call_count == 2


def test_applied_queries_qualified_schema_migrations(tmp_path: Path) -> None:
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
            password="pw",
        ),
    )
    docker.exec_psql.side_effect = [
        completed(),
        completed("001-init.sql\n002-more.sql\n"),
    ]

    applied = MigrationRunner(docker).applied(workspace, tmp_path / "compose.yaml")

    assert applied == {"001-init.sql", "002-more.sql"}
    sent = docker.exec_psql.call_args.kwargs["stdin"]
    assert "SELECT filename FROM public.schema_migrations ORDER BY filename;\n" in sent
    assert "CREATE TABLE IF NOT EXISTS" in docker.exec_psql.call_args_list[0].kwargs["stdin"]


def test_applied_retries_transient_docker_error(tmp_path: Path) -> None:
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
            password="pw",
        ),
    )
    docker.exec_psql.side_effect = [
        DockerError("connection refused"),
        completed(),
        completed(),
    ]

    with patch("n8n_launcher.database.migrations.time.sleep") as sleep:
        assert MigrationRunner(docker).applied(workspace, tmp_path / "compose.yaml") == set()

    sleep.assert_called_once_with(2.0)
    assert docker.exec_psql.call_count == 3


def test_applied_raises_on_docker_error(tmp_path: Path) -> None:
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
            password="pw",
        ),
    )

    docker.exec_psql.side_effect = DockerError("boom")
    with pytest.raises(MigrationError, match="boom"):
        MigrationRunner(docker).applied(workspace, tmp_path / "compose.yaml")


def test_migrate_files_is_sorted_and_ignores_others(tmp_path: Path) -> None:
    from n8n_launcher.database.layout import migrate_files

    (tmp_path / "002-second.sql").write_text("select 2;", encoding="utf-8")
    (tmp_path / "001-first.sql").write_text("select 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    assert [path.name for path in migrate_files(tmp_path)] == ["001-first.sql", "002-second.sql"]
    assert migrate_files(tmp_path / "missing") == []


def test_data_db_target_uses_defaults_for_managed_workspace() -> None:
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=Path("/tmp"),
        port=5680,
        db=DbConfig(mode=DbMode.MANAGED),
    )

    target = data_db_target(workspace)

    assert target == DatabaseTarget(
        host="postgres",
        port=5432,
        database="data",
        user="n8ndata",
        password="launcher-managed-data",
    )


def test_data_db_target_keeps_explicit_parameters() -> None:
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=Path("/tmp"),
        port=5680,
        db=DbConfig(
            mode=DbMode.MANAGED,
            database_name="custom",
            username="custom-user",
            password="custom-pass",
        ),
    )

    target = data_db_target(workspace)

    assert target is not None
    assert (target.database, target.user, target.password) == (
        "custom",
        "custom-user",
        "custom-pass",
    )


def test_data_db_target_is_none_without_managed_mode() -> None:
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=Path("/tmp"),
        port=5680,
        db=DbConfig(DbMode.NONE),
    )

    assert data_db_target(workspace) is None


def test_configure_db_credential_ensures_postgres_credential() -> None:
    from n8n_launcher.database import configure_db_credential

    api = MagicMock()
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=Path("/tmp"),
        port=5680,
        db=DbConfig(mode=DbMode.MANAGED),
    )

    assert configure_db_credential(api, workspace) is True
    api.ensure_postgres_credential.assert_called_once_with(
        "workspace-Db",
        host="postgres",
        port=5432,
        database="data",
        user="n8ndata",
        password="launcher-managed-data",
    )


def test_configure_db_credential_skips_without_managed_mode() -> None:
    from n8n_launcher.database import configure_db_credential

    api = MagicMock()
    workspace = Workspace(
        id="db1",
        name="Db",
        workflows_dir=Path("/tmp"),
        port=5680,
        db=DbConfig(DbMode.NONE),
    )

    assert configure_db_credential(api, workspace) is False
    api.ensure_postgres_credential.assert_not_called()
