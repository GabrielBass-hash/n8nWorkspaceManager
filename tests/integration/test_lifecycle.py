"""End-to-end Docker lifecycle for a managed n8n workspace."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.database import MigrationError, MigrationRunner, data_db_target, has_db_layout
from n8n_launcher.docker.compose import write_compose
from n8n_launcher.docker.manager import resolve_docker_command
from n8n_launcher.n8n.api import N8nApiClient
from n8n_launcher.n8n.owner import OwnerSetup
from n8n_launcher.n8n.workflows import SyncRunner
from n8n_launcher.platform.ports import suggest_port

_DOCKER_COMMAND = resolve_docker_command()

pytestmark = pytest.mark.integration


def wait_for_n8n(base_url: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/healthz", timeout=2.0)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(2.0)
    raise AssertionError(f"n8n did not become healthy at {base_url}")


def _appraisal_table_count(docker_manager, workspace: Workspace, compose_file: Path) -> int:
    result = docker_manager.exec_psql(
        workspace,
        compose_file,
        database=data_db_target(workspace).database,
        user=data_db_target(workspace).user,
        password=data_db_target(workspace).password,
        check=False,
        stdin=(
            "SELECT COUNT(*) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'appraisal' AND c.relkind = 'r';\n"
        ),
    )
    assert result.returncode == 0
    return int(next(line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()))


@pytest.fixture(scope="module")
def running_workspace(docker_manager, tmp_path_factory: Path):
    workspace_id = f"it{uuid4().hex[:6]}"
    work = tmp_path_factory.mktemp("ws")
    workflows_dir = work / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(
        id=workspace_id,
        name="Integration",
        workflows_dir=workflows_dir,
        port=suggest_port(requested=5600),
        db=DbConfig(DbMode.MANAGED),
    )
    compose_file = work / "compose.yml"
    write_compose(workspace, compose_file)
    base_url = f"http://127.0.0.1:{workspace.port}"
    try:
        docker_manager.pull(["postgres:16", f"n8nio/n8n:{workspace.n8n_version}"])
        docker_manager.up(workspace, compose_file)
        wait_for_n8n(base_url)
        yield workspace, compose_file, base_url
    finally:
        subprocess.run(
            [
                _DOCKER_COMMAND,
                "compose",
                "-p",
                f"n8n-ws-{workspace_id}",
                "-f",
                str(compose_file),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            capture_output=True,
            text=True,
            timeout=120.0,
            check=False,
        )


@pytest.mark.timeout(240)
def test_owner_setup_api_and_sync(running_workspace) -> None:
    workspace, _compose_file, base_url = running_workspace
    owner = OwnerSetup(timeout=10.0)
    credentials = owner.bootstrap(base_url, "owner@example.test", "IntegrationPass123!")
    assert credentials.api_key

    api = N8nApiClient(f"{base_url}/api/v1", credentials.api_key, timeout=10.0)
    assert api.list_workflows() == []
    api.create_workflow(
        {
            "name": "Integration smoke",
            "nodes": [],
            "connections": {},
            "settings": {},
        }
    )
    assert len(api.list_workflows()) == 1

    report = SyncRunner(api, workspace.workflows_dir).sync_once()
    assert report.pulled == 1
    assert len(list(workspace.workflows_dir.glob("*.json"))) == 1


@pytest.mark.timeout(60)
def test_compose_ps_output_is_parseable_json(running_workspace, docker_manager) -> None:
    workspace, compose_file, _base_url = running_workspace

    status = docker_manager.status(workspace, compose_file)

    assert status.returncode == 0
    rows = [json.loads(line) for line in status.raw_output.splitlines() if line.strip()]
    assert rows
    for row in rows:
        assert isinstance(row, dict)
    serialized = [json.dumps(row) for row in rows]
    assert any("n8n" in payload for payload in serialized)
    assert any("postgres" in payload for payload in serialized)


@pytest.mark.timeout(240)
def test_local_migrations_are_idempotent_and_rollback(running_workspace, docker_manager) -> None:
    workspace, compose_file, _base_url = running_workspace
    migrations_dir = workspace.workflows_dir / "db" / "migrations"
    migrations_dir.mkdir(parents=True)
    (workspace.workflows_dir / "db" / "schema.sql").write_text("select 1;", encoding="utf-8")
    runner = MigrationRunner(docker_manager)

    assert has_db_layout(workspace.workflows_dir) is True
    target = data_db_target(workspace)
    assert target is not None

    # ensure() is idempotent: run twice, the second run cannot step on the
    # role/database it created itself a moment ago.
    runner.ensure(workspace, compose_file)
    runner.ensure(workspace, compose_file)
    assert runner.apply(workspace, migrations_dir, compose_file) == []

    (migrations_dir / "001-init.sql").write_text(
        "CREATE SCHEMA IF NOT EXISTS appraisal;\n"
        "CREATE TABLE appraisal.events (id int PRIMARY KEY);\n",
        encoding="utf-8",
    )
    assert runner.apply(workspace, migrations_dir, compose_file) == ["001-init.sql"]
    assert _appraisal_table_count(docker_manager, workspace, compose_file) == 1
    # A completed migration is not re-applied.
    assert runner.apply(workspace, migrations_dir, compose_file) == []

    (migrations_dir / "002-failing.sql").write_text(
        "CREATE TABLE appraisal.failing (id int);\n"
        "INSERT INTO appraisal.does_not_exist VALUES (1);\n",
        encoding="utf-8",
    )
    with pytest.raises(MigrationError):
        runner.apply(workspace, migrations_dir, compose_file)
    # Transactional rollback: the statement that succeeded inside the failed
    # migration is undone and the filename is never recorded.
    assert _appraisal_table_count(docker_manager, workspace, compose_file) == 1
    assert runner.applied(workspace, compose_file) == {"001-init.sql"}

    (migrations_dir / "002-failing.sql").write_text(
        "CREATE TABLE appraisal.rollback (id int);\n", encoding="utf-8"
    )
    assert runner.apply(workspace, migrations_dir, compose_file) == ["002-failing.sql"]
    assert _appraisal_table_count(docker_manager, workspace, compose_file) == 2
    assert runner.applied(workspace, compose_file) == {"001-init.sql", "002-failing.sql"}
