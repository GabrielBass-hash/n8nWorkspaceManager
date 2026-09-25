"""Smoke test of the generated CI runner against a real n8n container.

The launcher's GitHub Actions harness is validated statically in unit tests;
this integration smoke goes one step further and runs ``.n8n-tests/runner.py``
exactly as a Linux CI job would, against a disposable n8n container on the
local daemon. The generated manual-trigger pipeline must come back green.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from n8n_launcher.docker.manager import resolve_docker_command
from n8n_launcher.workspaces.ci import CI_DIR, RUNNER_FILE, SELECTION_FILE, render_harness

pytestmark = pytest.mark.integration

_CI_IMAGE = "n8nio/n8n:2.40.0"


def _remove_runner_containers() -> None:
    listed = subprocess.run(
        [resolve_docker_command(), "ps", "-aq", "--filter", "name=n8n-ci-"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    for container in listed.stdout.split():
        subprocess.run(
            [resolve_docker_command(), "rm", "-f", container],
            capture_output=True,
            text=True,
            check=False,
            timeout=60.0,
        )


@pytest.mark.timeout(720)
def test_generated_runner_executes_a_manual_pipeline(docker_manager, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    pipelines = repo / "n8nPipelines"
    pipelines.mkdir(parents=True)
    (pipelines / "success.json").write_text(
        json.dumps(
            {
                "name": f"CI smoke {uuid4().hex[:4]}",
                "nodes": [
                    {
                        "name": "Start",
                        "type": "n8n-nodes-base.manualTrigger",
                        "typeVersion": 1,
                        "position": [0, 0],
                        "parameters": {},
                    }
                ],
                "connections": {},
                "settings": {},
            }
        ),
        encoding="utf-8",
    )

    harness = render_harness("2.40.0")
    (repo / CI_DIR).mkdir(parents=True)
    (repo / RUNNER_FILE).write_text(harness[RUNNER_FILE], encoding="utf-8")
    (repo / CI_DIR / SELECTION_FILE).write_text(
        json.dumps({"selected": ["n8nPipelines/success.json"]}), encoding="utf-8"
    )

    docker_manager.pull([_CI_IMAGE])
    env = os.environ.copy()
    env["DOCKER"] = resolve_docker_command()
    env["N8N_IMAGE"] = _CI_IMAGE
    env["N8N_CI_CREDENTIALS"] = "[]"
    try:
        result = subprocess.run(
            ["python3", RUNNER_FILE],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=600.0,
            check=False,
        )
    finally:
        _remove_runner_containers()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "réussies : 1" in result.stdout
