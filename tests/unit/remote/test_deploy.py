"""Tests for the generated server listener (post-receive hook + deploy.py).

The launcher ships the deploy logic as *generated* files installed on the
server (same discipline as the CI harness). The pure functions of the
generated ``deploy.py`` are exercised by ``exec``-ing its source, so the code
that actually runs in production is the code under test.
"""

import os
from pathlib import Path
from unittest.mock import patch

from n8n_launcher.core.models import DbConfig, DbMode, ServerConfig, Workspace
from n8n_launcher.remote import deploy

CFG = ServerConfig(
    enabled=True,
    host="prod.example.test",
    ssh_port=22,
    user="deploy",
    key_path="/home/me/.ssh/id_ed25519",
    base_dir="n8n-launcher/abc123",
    n8n_port=5689,
)


def _exec_deploy():
    """Exec the generated deploy.py and return its module namespace."""
    source = deploy.render_deploy_script()
    namespace: dict = {"__name__": "deploy_generated"}
    exec(compile(source, "deploy_generated.py", "exec"), namespace)
    return namespace


# -------------------------------------------------------------------------
# Path / URL helpers
# -------------------------------------------------------------------------


def test_server_remote_url_uses_scp_syntax() -> None:
    assert deploy.server_remote_url(CFG) == "deploy@prod.example.test:n8n-launcher/abc123.git"


def test_resolve_base_uses_configured_base_dir() -> None:
    assert deploy.resolve_base(CFG, "ignored") == "n8n-launcher/abc123"


def test_resolve_base_defaults_to_workspace_id() -> None:
    workspace = Workspace(
        id="ws42",
        name="W",
        workflows_dir=Path("/tmp/w"),
        port=5678,
        db=DbConfig(mode=DbMode.NONE),
        server=ServerConfig(enabled=True, host="h", user="u"),
    )
    assert deploy.resolve_base(workspace.server, workspace.id) == "n8n-launcher/ws42"


def test_resolve_base_normalizes_tilde_and_trailing_slash() -> None:
    cfg = ServerConfig(
        enabled=True,
        host="h",
        user="u",
        base_dir="~/apps/prod-ws/",
    )
    assert deploy.resolve_base(cfg, "x") == "apps/prod-ws"


def test_path_helpers_build_server_layout() -> None:
    assert deploy.checkout_dir(CFG) == "n8n-launcher/abc123/workflow"
    assert deploy.secrets_path(CFG) == "n8n-launcher/abc123/secrets.json"
    assert deploy.log_path(CFG) == "n8n-launcher/abc123/server.log"
    assert deploy.marker_path(CFG) == "n8n-launcher/abc123/last-deploy.json"


# -------------------------------------------------------------------------
# Generated script surface
# -------------------------------------------------------------------------


def test_render_deploy_script_contains_marker_and_stdlib_only() -> None:
    source = deploy.render_deploy_script()

    assert deploy.GENERATED_MARKER in source
    for forbidden in (
        "import requests",
        "import paramiko",
        "docker.client",
        "requests.",
    ):
        assert forbidden not in source


def test_render_hook_contains_marker_and_filters_main() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert deploy.GENERATED_MARKER in hook
    assert "refs/heads/main" in hook
    assert "git archive" in hook
    assert "docker compose -p" in hook
    assert "up -d" in hook
    assert '--git-dir="$BARE"' in hook
    assert "n8n-launcher/abc123.git" in hook
    assert "last-deploy.json" in hook


def test_render_hook_serializes_deploys_with_flock() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert "flock -x 9" in hook
    assert '9>>"$BASE/.deploy.lock"' in hook


def test_render_hook_pins_the_compose_project() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert "PROJECT='n8n-ws-abc123'" in hook
    assert 'docker compose -p "$PROJECT" up -d' in hook
    assert 'DEPLOY_PROJECT="$PROJECT"' in hook


def test_render_hook_writes_marker_atomically() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert 'path + ".tmp"' in hook or '".tmp"' in hook
    assert "os.replace" in hook
    assert "last-deploy.json" in hook


def test_render_hook_quotes_paths_for_bash() -> None:
    cfg = ServerConfig(enabled=True, host="h", user="u", base_dir="dir with spaces/ws")
    hook = deploy.render_hook(cfg)

    assert "dir with spaces/ws" in hook


# -------------------------------------------------------------------------
# Generated deploy.py pure functions (exec-based meta-tests)
# -------------------------------------------------------------------------


def test_deploy_collect_workflow_files(tmp_path: Path) -> None:
    ns = _exec_deploy()
    pipelines = tmp_path / "n8nPipelines"
    pipelines.mkdir()
    (pipelines / "a.json").write_text("{}", encoding="utf-8")
    (pipelines / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "root.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    (tmp_path / "compose.yml").write_text("ignored", encoding="utf-8")
    # The publish mirror also copies pipelines files at the root; the canonical
    # pipeline copy wins so the workflow is not uploaded twice.
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")

    files = ns["collect_workflow_files"](str(tmp_path))

    names = [Path(f).name for f in files]
    assert sorted(names) == ["a.json", "b.json", "root.json"]
    assert [Path(f).name for f in files].count("b.json") == 1


def test_deploy_collect_workflow_files_missing_pipelines(tmp_path: Path) -> None:
    ns = _exec_deploy()
    (tmp_path / "root.json").write_text("{}", encoding="utf-8")

    files = ns["collect_workflow_files"](str(tmp_path))

    assert [Path(f).name for f in files] == ["root.json"]


def test_deploy_create_payload_whitelist() -> None:
    ns = _exec_deploy()
    raw = {
        "name": "Flow",
        "nodes": ["n1"],
        "connections": {},
        "settings": {"executionTimeout": 0},
        "active": True,
        "triggerCount": 3,
        "shared": True,
        "pinData": {"n1": []},
    }

    payload = ns["create_payload"](raw)

    assert payload["name"] == "Flow"
    assert payload["nodes"] == ["n1"]
    assert payload["settings"] == {"executionTimeout": 0}
    assert "active" not in payload
    assert "triggerCount" not in payload
    assert "shared" not in payload


def test_deploy_create_payload_defaults_settings() -> None:
    ns = _exec_deploy()
    payload = ns["create_payload"]({"name": "Flow", "nodes": []})

    assert payload["settings"] == {}


def test_deploy_find_by_name() -> None:
    ns = _exec_deploy()
    workflows = [
        {"id": "1", "name": "Alpha"},
        {"id": "2", "name": "Beta"},
    ]

    assert ns["find_by_name"](workflows, "Beta") == {"id": "2", "name": "Beta"}
    assert ns["find_by_name"](workflows, "Gamma") is None


def test_deploy_rewrite_credential_ids() -> None:
    ns = _exec_deploy()
    nodes = [
        {
            "name": "HTTP",
            "type": "n8n-nodes-base.httpRequest",
            "credentials": {"httpRequest": {"id": "old-local", "name": "API"}},
        },
        {
            "name": "NoCred",
            "type": "n8n-nodes-base.noOp",
        },
    ]
    id_map = {("API", "httpRequest"): "new-remote"}

    rewritten = ns["rewrite_credential_ids"](nodes, id_map)

    assert rewritten[0]["credentials"]["httpRequest"]["id"] == "new-remote"
    assert rewritten[0]["credentials"]["httpRequest"]["name"] == "API"
    assert "credentials" not in rewritten[1]


def test_deploy_rewrite_credential_ids_leaves_unknown() -> None:
    ns = _exec_deploy()
    nodes = [
        {
            "name": "HTTP",
            "type": "n8n-nodes-base.httpRequest",
            "credentials": {"httpRequest": {"id": "x", "name": "Unknown"}},
        }
    ]

    rewritten = ns["rewrite_credential_ids"](nodes, {})

    assert rewritten[0]["credentials"]["httpRequest"]["id"] == "x"


def test_deploy_migration_files_sorted(tmp_path: Path) -> None:
    ns = _exec_deploy()
    migrations = tmp_path / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "003_x.sql").write_text("", encoding="utf-8")
    (migrations / "001_y.sql").write_text("", encoding="utf-8")
    (migrations / "002_a.md").write_text("", encoding="utf-8")

    files = ns["migration_files"](str(tmp_path))

    assert files == ["001_y.sql", "003_x.sql"]  # sorted, non-SQL ignored


def test_deploy_load_secrets(tmp_path: Path) -> None:
    ns = _exec_deploy()
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        '{"owner_email": "o@test", "owner_password": "pw", "credentials": []}',
        encoding="utf-8",
    )

    data = ns["load_secrets"](str(secrets))

    assert data["owner_email"] == "o@test"
    assert data["credentials"] == []


def test_deploy_pins_project_and_uses_it_for_compose(tmp_path: Path) -> None:
    migrations = tmp_path / "db" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_x.sql").write_text("SELECT 1;\n", encoding="utf-8")
    os.environ["DEPLOY_PROJECT"] = "n8n-ws-abc123"
    os.environ["DEPLOY_CHECKOUT"] = str(tmp_path)
    try:
        ns = _exec_deploy()

        assert ns["PROJECT"] == "n8n-ws-abc123"
        assert ns["COMPOSE_CMD"] == ["docker", "compose", "-p", "n8n-ws-abc123"]

        # run_migrations resolves the postgres service via the pinned project.
        with patch.object(ns["subprocess"], "run") as run:
            run.side_effect = [
                CompletedProcessStub(["docker", "compose"], 0, "postgres\nn8n\n", ""),
                CompletedProcessStub(["docker", "compose"], 0, "", ""),
            ]
            ns["run_migrations"]()

        first, second = run.call_args_list
        assert first.args[0][:4] == ["docker", "compose", "-p", "n8n-ws-abc123"]
        assert first.args[0][4:] == ["config", "--services"]
        assert second.args[0][:4] == ["docker", "compose", "-p", "n8n-ws-abc123"]
        assert "postgres" in second.args[0]
    finally:
        os.environ.pop("DEPLOY_PROJECT", None)
        os.environ.pop("DEPLOY_CHECKOUT", None)


class CompletedProcessStub:
    """Minimal stand-in for the subprocess result used by exec-based tests."""

    def __init__(self, argv, returncode, stdout, stderr):
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_deploy_write_marker_is_atomic(tmp_path: Path) -> None:
    ns = _exec_deploy()
    ns["MARKER"] = str(tmp_path / "last-deploy.json")
    ns["SHA"] = "deadbeef"

    ns["write_marker"]("ok")

    assert '"status": "ok"' in (tmp_path / "last-deploy.json").read_text(encoding="utf-8")
    assert not (tmp_path / "last-deploy.json.tmp").exists()


def test_build_secrets_document(tmp_path: Path) -> None:
    document = deploy.build_secrets_document(
        owner_email="o@test",
        owner_password="pw",
        credentials=[{"name": "API", "type": "httpRequest", "data": {"url": "x"}}],
    )

    assert document["owner_email"] == "o@test"
    assert document["credentials"][0]["name"] == "API"
    assert document["credentials"][0]["data"] == {"url": "x"}
