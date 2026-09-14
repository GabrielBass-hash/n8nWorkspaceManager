"""End-to-end Docker lifecycle for a managed n8n workspace."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from n8n_launcher.api_client import N8nApiClient
from n8n_launcher.compose import write_compose
from n8n_launcher.models import DbConfig, DbMode, Workspace
from n8n_launcher.owner_setup import OwnerSetup
from n8n_launcher.ports import suggest_port
from n8n_launcher.sync_runner import SyncRunner

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
        docker_manager.down(workspace, compose_file, remove_orphans=True)
        subprocess.run(
            ["docker", "volume", "rm", "-f", f"n8ndata-{workspace_id}", f"pgdata-{workspace_id}"],
            capture_output=True,
            text=True,
            timeout=60.0,
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
    api.create_workflow({
    "name": "Integration smoke",
    "nodes": [],
    "connections": {},
    "settings": {},
})
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