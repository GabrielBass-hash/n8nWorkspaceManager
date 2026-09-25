"""Tests for the generated server listener (post-receive hook + deploy.py).

The launcher ships the deploy logic as *generated* files installed on the
server (same discipline as the CI harness). The pure functions of the
generated ``deploy.py`` are exercised by ``exec``-ing its source, so the code
that actually runs in production is the code under test.
"""

import json
from pathlib import Path

import pytest

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
        "import subprocess",
    ):
        assert forbidden not in source


def test_render_hook_contains_marker_and_filters_main() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert deploy.GENERATED_MARKER in hook
    assert "refs/heads/main" in hook
    assert "git archive" in hook
    assert "docker compose -f" in hook
    assert '-p "$PROJECT"' in hook
    assert "up -d" in hook
    assert '--git-dir="$BARE"' in hook
    assert "n8n-launcher/abc123.git" in hook
    assert "last-deploy.json" in hook


def test_render_hook_serializes_deploys_with_flock() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert "flock -x 9" in hook
    assert '9>>"$BASE/.deploy.lock"' in hook


def test_render_hook_anchors_paths_on_home() -> None:
    # Git sets the hook cwd to the bare repo; because BASE/BARE/WORKFLOW are
    # home-relative, the hook must cd "$HOME" first or the flock file (and
    # every compose/deploy path) resolves inside the bare repo.
    hook = deploy.render_hook(CFG, "abc123")

    assert 'cd "$HOME"' in hook
    assert hook.index('cd "$HOME"') < hook.index("flock -x 9")


def test_render_hook_pins_the_compose_project() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert "PROJECT='n8n-ws-abc123'" in hook
    assert "docker compose -f" in hook
    assert '"$WORKFLOW/compose.yml"' in hook
    assert 'docker compose -f "$WORKFLOW/compose.yml" -p "$PROJECT" up -d' in hook
    assert "DEPLOY_PROJECT" not in hook


def test_render_hook_writes_marker_atomically() -> None:
    hook = deploy.render_hook(CFG, "abc123")

    assert "marker + '.tmp'" in hook or '".tmp"' in hook
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


def test_deploy_does_not_execute_local_data_migrations() -> None:
    source = deploy.render_deploy_script()

    assert "db/migrations" not in source
    assert "run_migrations" not in source
    assert "migration_files" not in source
    assert "psql" not in source


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


def test_deploy_write_marker_is_atomic(tmp_path: Path) -> None:
    ns = _exec_deploy()
    ns["MARKER"] = str(tmp_path / "last-deploy.json")
    # ``write_marker`` also appends to the deploy history: point it at tmp_path
    # too, or the generated script writes into the developer's working directory.
    ns["HISTORY"] = str(tmp_path / "deploy-history.jsonl")
    ns["SHA"] = "deadbeef"

    ns["write_marker"]("ok")

    assert '"status": "ok"' in (tmp_path / "last-deploy.json").read_text(encoding="utf-8")
    assert not (tmp_path / "last-deploy.json.tmp").exists()
    assert (tmp_path / "deploy-history.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_deploy_write_marker_records_the_error(tmp_path: Path) -> None:
    ns = _exec_deploy()
    ns["MARKER"] = str(tmp_path / "last-deploy.json")
    ns["HISTORY"] = str(tmp_path / "deploy-history.jsonl")
    ns["SHA"] = "cafebabe"

    ns["write_marker"]("error", "compose failed")

    marker = json.loads((tmp_path / "last-deploy.json").read_text(encoding="utf-8"))
    assert marker["status"] == "error"
    assert marker["error"] == "compose failed"
    history = (tmp_path / "deploy-history.jsonl").read_text(encoding="utf-8")
    assert "compose failed" in history


def test_build_secrets_document(tmp_path: Path) -> None:
    document = deploy.build_secrets_document(
        owner_email="o@test",
        owner_password="pw",
        credentials=[{"name": "API", "type": "httpRequest", "data": {"url": "x"}}],
    )

    assert document["owner_email"] == "o@test"
    assert document["credentials"][0]["name"] == "API"
    assert document["credentials"][0]["data"] == {"url": "x"}


# -------------------------------------------------------------------------
# Execution status: the generated script and the launcher must agree
# -------------------------------------------------------------------------


def test_generated_script_carries_the_launcher_execution_vocabulary() -> None:
    """The status token is injected, not duplicated in the template.

    The generated script derives ``finished`` from n8n's own status enum; if
    the sets were written twice, the server and the launcher would eventually
    disagree on what a finished execution is.
    """
    source = deploy.render_deploy_script()
    assert "__TERMINAL_STATUSES__" not in source
    assert "__PENDING_STATUSES__" not in source
    namespace = _exec_deploy()
    assert namespace["TERMINAL_STATUSES"] == set(deploy.TERMINAL_EXECUTION_STATUSES)
    assert namespace["PENDING_STATUSES"] == set(deploy.PENDING_EXECUTION_STATUSES)
    # Upstream (packages/workflow/src/execution-status.ts) is the reference.
    assert namespace["TERMINAL_STATUSES"] == {"canceled", "crashed", "error", "success"}
    # waiting and unknown are not terminal upstream: waiting is paused, and an
    # unknown execution may still be rewritten to crashed by recovery.
    assert namespace["PENDING_STATUSES"] == {"new", "running", "waiting", "unknown"}


def test_generated_execution_summary_derives_finished_from_the_status() -> None:
    summary = _exec_deploy()["_execution_summary"]
    assert summary({"id": "1", "status": "success"})["finished"] is True
    assert summary({"id": "1", "status": "error"})["finished"] is True
    assert summary({"id": "1", "status": "crashed"})["finished"] is True
    assert summary({"id": "1", "status": "canceled"})["finished"] is True
    assert summary({"id": "1", "status": "running"})["finished"] is False
    assert summary({"id": "1", "status": "waiting"})["finished"] is False
    # n8n's own vocabulary has no terminal status for "unknown" (recovery may
    # still rewrite it), and an unknown status must not be guessed either way.
    assert summary({"id": "1", "status": "unknown"})["finished"] is False
    assert "finished" not in summary({"id": "1", "status": "brand-new-status"})


def test_generated_execution_summary_keeps_an_explicit_finished_flag() -> None:
    summary = _exec_deploy()["_execution_summary"]
    # The server's explicit answer wins over the derivation, including a
    # finished=False attached to a status that would otherwise look terminal.
    assert summary({"id": "1", "status": "success", "finished": False})["finished"] is False
    assert summary({"id": "1", "status": "unknown", "finished": True})["finished"] is True


def test_generated_and_launcher_parsers_agree_on_every_status() -> None:
    """One table, two implementations: the same verdict is mandatory."""
    from n8n_launcher.remote.ssh import _execution_from_payload

    generated = _exec_deploy()["_execution_summary"]
    for status in (
        "success",
        "error",
        "crashed",
        "canceled",
        "running",
        "new",
        "waiting",
        "unknown",
        "SUCCESS",
        "future-status",
    ):
        server_side = generated({"id": "1", "status": status}).get("finished")
        launcher_side = _execution_from_payload({"id": "1", "status": status}).finished
        assert server_side == launcher_side, status


# -------------------------------------------------------------------------
# API key bootstrap (n8n 2.x paginates /rest/api-keys)
# -------------------------------------------------------------------------


class _FakeHttp:
    """Minimal stand-in for the generated script's Http object."""

    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, object]] = []
        self.api_key = ""

    def request(self, method, path, data=None, auth="none", timeout=30.0):
        self.calls.append((method, path))
        self.requests.append((method, path, data))
        return self.responses.pop(0)


def test_generated_ensure_api_key_handles_the_paginated_2x_listing() -> None:
    """n8n 2.x answers ``{"data": {"items": [...]}}``, not ``{"data": [...]}``.

    Iterating the mapping directly used to yield its keys and blow up with
    ``'str' object has no attribute 'get'`` on a real 2.40 server.
    """
    http = _FakeHttp(
        [
            (200, {"data": {"items": [{"id": "7", "label": "n8n-launcher"}], "counts": {}}}),
            (200, {}),  # the DELETE
            (200, {"data": {"rawApiKey": "n8n_api_fresh"}}),
        ]
    )
    _exec_deploy()["ensure_api_key"](http)
    assert http.api_key == "n8n_api_fresh"
    assert http.calls == [
        ("GET", "/rest/api-keys"),
        ("DELETE", "/rest/api-keys/7"),
        ("POST", "/rest/api-keys"),
    ]


def test_generated_ensure_api_key_still_accepts_the_legacy_listing() -> None:
    http = _FakeHttp(
        [
            (200, {"data": [{"id": "7", "label": "n8n-launcher"}, {"id": "8", "label": "other"}]}),
            (200, {}),  # the DELETE
            (201, {"data": {"apiKey": "n8n_api_legacy"}}),
        ]
    )
    _exec_deploy()["ensure_api_key"](http)
    assert http.api_key == "n8n_api_legacy"
    # Only the launcher's own key is revoked; somebody else's stays.
    assert ("DELETE", "/rest/api-keys/7") in http.calls
    assert ("DELETE", "/rest/api-keys/8") not in http.calls


def test_generated_ensure_api_key_reports_a_failed_creation() -> None:
    http = _FakeHttp([(200, {"data": {"items": []}}), (403, {"error": "Forbidden"})])
    with pytest.raises(RuntimeError, match="création d'API key a échoué"):
        _exec_deploy()["ensure_api_key"](http)


def test_generated_ensure_api_key_reports_a_failed_listing() -> None:
    http = _FakeHttp([(500, {"error": "boom"})])
    with pytest.raises(RuntimeError, match="GET /rest/api-keys a échoué"):
        _exec_deploy()["ensure_api_key"](http)


def test_generated_deploy_requests_the_shared_api_key_scopes() -> None:
    """The generated key must ask for the same scopes as the local bootstrap.

    n8n answers ``400 Invalid scopes for user role`` when the list holds a
    scope its owner role does not grant, so the two callers cannot keep private
    copies of the contract — the template receives it through a token.
    """
    from n8n_launcher.n8n.scopes import REQUIRED_WORKFLOW_SCOPES

    assert "__API_KEY_SCOPES__" not in deploy.render_deploy_script()
    http = _FakeHttp([(200, {"data": {"items": []}}), (200, {"data": {"rawApiKey": "k"}})])
    _exec_deploy()["ensure_api_key"](http)
    posted = [data for method, path, data in http.requests if method == "POST"]
    assert posted == [{"label": "n8n-launcher", "scopes": REQUIRED_WORKFLOW_SCOPES, "expiresAt": 0}]


# -------------------------------------------------------------------------
# Workflow activation is best effort: n8n decides what can be active
# -------------------------------------------------------------------------


def _deploy_with_checkout(directory: Path):
    """Exec the generated script with its checkout pointed at *directory*."""
    namespace = _exec_deploy()
    namespace["CHECKOUT"] = str(directory)
    return namespace


def _export(directory: Path, *, node_type: str) -> None:
    """Write one workflow export the generated script will collect."""
    pipelines = directory / "n8nPipelines"
    pipelines.mkdir(parents=True, exist_ok=True)
    (pipelines / "flow.json").write_text(
        json.dumps(
            {
                "name": "Flow",
                "nodes": [
                    {
                        "parameters": {"path": "hook"},
                        "id": "trigger",
                        "name": "Trigger",
                        "type": node_type,
                        "typeVersion": 2,
                        "position": [0, 0],
                    }
                ],
                "connections": {},
                "settings": {},
            }
        ),
        encoding="utf-8",
    )


def test_generated_sync_workflows_activates_through_the_owner_session(
    tmp_path: Path, capsys
) -> None:
    """The API key cannot always activate; the owner session can.

    n8n 2.x answers a public ``/activate`` with "ask the owner to share it
    with you", and its internal endpoint wants the current ``versionId``.
    """
    _export(tmp_path, node_type="n8n-nodes-base.webhook")
    namespace = _deploy_with_checkout(tmp_path)
    http = _FakeHttp(
        [
            (200, {"data": []}),  # GET existing workflows
            (200, {"data": {"id": "w1"}}),  # POST the workflow
            (200, {"data": {"id": "w1", "versionId": "v7"}}),  # GET the current version
            (200, {"id": "w1", "active": True}),  # activate through the session
        ]
    )
    logins: list[int] = []
    namespace["sync_workflows"](http, {}, lambda: logins.append(1))

    assert ("POST", "/rest/workflows/w1/activate") in http.calls
    assert http.requests[-1][2] == {"versionId": "v7"}
    assert logins == []
    assert "Flow actif" in capsys.readouterr().out


def test_generated_sync_workflows_retries_a_workflow_the_session_cannot_see_yet() -> None:
    """Right after a public-API create, n8n can answer 404 for a short while."""
    namespace = _exec_deploy()
    namespace["VERSION_LOOKUP_DELAY"] = 0.0
    http = _FakeHttp(
        [
            (404, {"message": "Could not load the workflow"}),
            (200, {"data": {"id": "w1", "versionId": "v7"}}),
        ]
    )
    assert namespace["workflow_version"](http, "w1") == "v7"


def test_generated_sync_workflows_falls_back_to_the_api_key(tmp_path: Path, capsys) -> None:
    """Without an internal versionId (older n8n), the API key is the fallback."""
    _export(tmp_path, node_type="n8n-nodes-base.webhook")
    namespace = _deploy_with_checkout(tmp_path)
    http = _FakeHttp(
        [
            (200, {"data": []}),
            (200, {"data": {"id": "w1"}}),
            (200, {"data": {"id": "w1"}}),  # no versionId before the relogin
            (200, {"data": {"id": "w1"}}),  # still none after it
            (200, {"id": "w1", "active": True}),
        ]
    )
    logins: list[int] = []
    namespace["sync_workflows"](http, {}, lambda: logins.append(1))

    assert ("POST", "/api/v1/workflows/w1/activate") in http.calls
    # The session could not answer, so it was renewed before the fallback.
    assert logins == [1]
    assert "Flow actif" in capsys.readouterr().out


def test_generated_sync_workflows_reports_an_activation_refusal(tmp_path: Path, capsys) -> None:
    """A workflow n8n refuses to activate must not fail the deployment.

    A manual-only export can never be activated on n8n 2.x ("no trigger node").
    The import already succeeded, so the reason goes to the server log.
    """
    _export(tmp_path, node_type="n8n-nodes-base.manualTrigger")
    namespace = _deploy_with_checkout(tmp_path)
    http = _FakeHttp(
        [
            (200, {"data": []}),
            (200, {"data": {"id": "w1"}}),
            (200, {"data": {"id": "w1", "versionId": "v1"}}),
            (
                400,
                {"message": "Workflow cannot be activated because it has no trigger node."},
            ),
        ]
    )
    logins: list[int] = []
    namespace["sync_workflows"](http, {}, lambda: logins.append(1))

    out = capsys.readouterr().out
    assert "Flow laissé inactif" in out
    assert "no trigger node" in out


def test_generated_sync_workflows_still_fails_on_a_refused_import(tmp_path: Path) -> None:
    """A refused *import* is fatal: the deployment would be incomplete."""
    _export(tmp_path, node_type="n8n-nodes-base.webhook")
    namespace = _deploy_with_checkout(tmp_path)
    http = _FakeHttp(
        [(200, {"data": []}), (400, {"message": "must NOT have additional properties"})]
    )
    with pytest.raises(RuntimeError, match="must NOT have additional properties"):
        namespace["sync_workflows"](http, {}, lambda: None)
