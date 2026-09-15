from pathlib import Path
from unittest.mock import MagicMock, patch

from n8n_launcher.database import MigrationRunner
from n8n_launcher.db_manager import detect_migrations, has_db_layout
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
    (migrations_dir / "002-more.sql").write_text("CREATE TABLE app.more (id int);\n", encoding="utf-8")

    applied = MigrationRunner(docker).apply(workspace, migrations_dir, tmp_path / "compose.yaml")

    assert applied == ["001-init.sql", "002-more.sql"]
    bodies = [
        call.kwargs["stdin"]
        for call in docker.exec_psql.call_args_list
        if "CREATE TABLE IF NOT EXISTS public.schema_migrations" in call.kwargs["stdin"]
    ]
    assert len(bodies) == 2
    assert "CREATE TABLE IF NOT EXISTS public.schema_migrations (" in bodies[0]
    assert "\nINSERT INTO public.schema_migrations(filename) VALUES ('001-init.sql');\n" in bodies[0]
    assert "set_config('search_path'" not in bodies[0]
    assert "app.things" in bodies[0]
    assert "app.more" in bodies[1]


def test_applied_queries_qualified_schema_migrations(tmp_path: Path) -> None:
    from n8n_launcher.docker_manager import DockerError

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
    docker.exec_psql.return_value = MagicMock(returncode=0, stdout="001-init.sql\n002-more.sql\n")

    applied = MigrationRunner(docker).applied(workspace, tmp_path / "compose.yaml")

    assert applied == {"001-init.sql", "002-more.sql"}
    sent = docker.exec_psql.call_args.kwargs["stdin"]
    assert "SELECT filename FROM public.schema_migrations ORDER BY filename;\n" in sent


def test_applied_returns_empty_on_docker_error(tmp_path: Path) -> None:
    from n8n_launcher.docker_manager import DockerError

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

    # check=False makes the retry helper *return* the last error rather than raise.
    with patch.object(MigrationRunner, "_run_with_retries", return_value=DockerError("boom")):
        assert MigrationRunner(docker).applied(workspace, tmp_path / "compose.yaml") == set()
