from n8n_launcher.compose import ComposeError, render_compose
from n8n_launcher.models import DbConfig, DbMode, Workspace


def test_managed_compose_is_isolated(managed_workspace) -> None:
    rendered = render_compose(managed_workspace)

    assert "image: postgres:16" in rendered
    assert "pgdata-demo123:/var/lib/postgresql/data" in rendered
    assert "n8ndata-demo123:/home/node/.n8n" in rendered
    assert '"5678:5678"' in rendered
    assert "condition: service_healthy" in rendered
    volumes = rendered.split("volumes:", 1)[1]
    assert "pgdata-demo123:" in volumes
    assert "n8ndata-demo123:" in volumes


def test_external_database_values_are_quoted_and_dollar_escaped(tmp_path) -> None:
    workspace = Workspace(
        id="external1",
        name="External",
        workflows_dir=tmp_path,
        port=5681,
        db=DbConfig(
            mode=DbMode.EXTERNAL,
            connection_string="postgresql://user:p$ss@db.example/app",
        ),
    )

    rendered = render_compose(workspace)

    assert 'DB_POSTGRESDB_PASSWORD: "p$$ss"' in rendered
    assert "image: postgres:16" not in rendered
    assert "DB_POSTGRESDB_HOST: \"db.example\"" in rendered
    volumes = rendered.split("volumes:", 1)[1]
    assert "pgdata-external1:" not in volumes


def test_external_database_requires_connection_string(managed_workspace) -> None:
    managed_workspace.db = DbConfig(mode=DbMode.EXTERNAL)

    try:
        render_compose(managed_workspace)
    except ComposeError as error:
        assert "connection string" in str(error)
    else:
        raise AssertionError("Expected ComposeError")


def test_none_database_omits_postgres_and_db_environment(tmp_path) -> None:
    workspace = Workspace(
        id="none1",
        name="NoneDb",
        workflows_dir=tmp_path,
        port=5682,
        db=DbConfig(mode=DbMode.NONE),
    )

    rendered = render_compose(workspace)

    assert "image: postgres:16" not in rendered
    assert "DB_TYPE:" not in rendered
    assert "condition: service_healthy" not in rendered
    assert "depends_on:" not in rendered
    volumes = rendered.split("volumes:", 1)[1]
    assert "pgdata-none1:" not in volumes
    assert "n8ndata-none1:" in volumes
