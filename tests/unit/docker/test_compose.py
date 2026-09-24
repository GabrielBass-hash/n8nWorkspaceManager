from n8n_launcher.core.models import DbConfig, DbMode, ServerConfig, Workspace
from n8n_launcher.docker.compose import render_compose, render_remote_compose


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


def test_managed_compose_respects_custom_postgres_image(tmp_path) -> None:
    workspace = Workspace(
        id="custom1",
        name="CustomPg",
        workflows_dir=tmp_path,
        port=5683,
        db=DbConfig(mode=DbMode.MANAGED),
        postgres_image="postgis/postgis:16",
    )

    rendered = render_compose(workspace)

    assert "image: postgis/postgis:16" in rendered
    assert "pgdata-custom1:/var/lib/postgresql/data" in rendered


def test_managed_compose_default_image_when_none(tmp_path) -> None:
    workspace = Workspace(
        id="default1",
        name="DefaultPg",
        workflows_dir=tmp_path,
        port=5684,
        db=DbConfig(mode=DbMode.MANAGED),
        postgres_image=None,
    )

    rendered = render_compose(workspace)

    assert "image: postgres:16" in rendered
    assert "shared_preload_libraries" not in rendered


def test_managed_compose_preloads_timescaledb_only_on_request(tmp_path) -> None:
    workspace = Workspace(
        id="geo1",
        name="Geo",
        workflows_dir=tmp_path,
        port=5685,
        db=DbConfig(mode=DbMode.MANAGED),
        postgres_image="imresamu/postgis:16-3.5-bundle0-bookworm",
        postgres_preload_timescaledb=True,
    )

    rendered = render_compose(workspace)

    assert "image: imresamu/postgis:16-3.5-bundle0-bookworm" in rendered
    assert 'command: ["postgres", "-c", "shared_preload_libraries=timescaledb"]' in rendered


def test_managed_compose_omits_preload_command_by_default(tmp_path) -> None:
    workspace = Workspace(
        id="plain1",
        name="Plain",
        workflows_dir=tmp_path,
        port=5686,
        db=DbConfig(mode=DbMode.MANAGED),
    )

    rendered = render_compose(workspace)

    assert "shared_preload_libraries" not in rendered


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


def test_render_remote_compose_binds_loopback_remote_port(tmp_path) -> None:
    workspace = Workspace(
        id="abc",
        name="Remote",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(mode=DbMode.NONE),
        server=ServerConfig(enabled=True, n8n_port=5689),
    )

    rendered = render_remote_compose(workspace)

    assert '"127.0.0.1:5689:5678"' in rendered
    # The remote image/name/volume isolation is identical to the local render.
    assert "image: n8nio/n8n:2.40.0" in rendered
    assert "n8ndata-abc:/home/node/.n8n" in rendered
    # The project is pinned by a top-level name so the server hook and
    # deploy.py reach the right isolated project whatever the directory name.
    assert rendered.startswith("name: n8n-ws-abc\n")


def test_render_remote_compose_defaults_loopback_port(tmp_path) -> None:
    workspace = Workspace(
        id="abc",
        name="Remote",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(mode=DbMode.NONE),
    )

    rendered = render_remote_compose(workspace)

    assert '"127.0.0.1:5678:5678"' in rendered


def test_render_remote_compose_uses_relative_workflows_mount(tmp_path) -> None:
    workspace = Workspace(
        id="abc",
        name="Remote",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(mode=DbMode.NONE),
        server=ServerConfig(enabled=True),
    )

    rendered = render_remote_compose(workspace)

    # The checkout root on the server is mapped as-is (no absolute local path).
    assert '"./:/workflows"' in rendered
    assert str(tmp_path) not in rendered


def test_render_remote_compose_keeps_managed_database(tmp_path) -> None:
    workspace = Workspace(
        id="abc",
        name="Remote",
        workflows_dir=tmp_path,
        port=5680,
        db=DbConfig(mode=DbMode.MANAGED),
        server=ServerConfig(enabled=True, n8n_port=5689),
    )

    rendered = render_remote_compose(workspace)

    assert "image: postgres:16" in rendered
    assert "pgdata-abc:/var/lib/postgresql/data" in rendered
    assert "condition: service_healthy" in rendered
    assert '"127.0.0.1:5689:5678"' in rendered
