"""Meta-tests for the CI harness the launcher generates (workspaces/ci.py).

The two generated Python scripts are treated as real code: ``validate.py`` is
run as a subprocess against fixture repositories and ``runner.py`` is imported
from disk and exercised in-process against a fake HTTP client.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from n8n_launcher.workspaces import ci


def render_harness_at(tmp_path: Path, version: str = "2.35.0"):
    """Write the generated harness into *tmp_path* and return the folder."""
    for rel, content in ci.render_harness(version).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def load_runner(root: Path):
    """Import the generated runner.py without running main()."""
    spec = importlib.util.spec_from_file_location("ci_runner_under_test", root / ci.RUNNER_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_export(root: Path, rel: str, nodes: list, name: str | None = None) -> dict:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    export = {
        "name": name or path.stem,
        "nodes": nodes,
        "connections": {},
        "settings": {},
    }
    path.write_text(json.dumps(export), encoding="utf-8")
    return export


def write_selection(root: Path, selected: list[str]) -> None:
    path = root / ".n8n-tests" / "tests.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"selected": selected}), encoding="utf-8")


class FakeHttp:
    """Replaces the generated Http client with canned (code, body) responses."""

    def __init__(
        self,
        responses: dict[tuple[str, str], tuple[int, object]],
        *,
        cookies: tuple[str, ...] = (),
    ):
        self.responses = responses
        self.calls: list[tuple[str, str, object, object]] = []
        self.cookies = [type("Cookie", (), {"name": name})() for name in cookies]

    def request(self, method: str, path: str, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        return self.responses.get((method, path), (200, {"data": []}))


# --- generated validate.py (subprocess) ------------------------------------


def test_generated_validate_rejects_duplicate_names(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    write_export(root, "n8nPipelines/a.json", [{"name": "B", "type": "n8n-nodes-base.noOp", "typeVersion": 1}], name="B")
    write_export(root, "n8nPipelines/b.json", [{"name": "B", "type": "n8n-nodes-base.noOp", "typeVersion": 1}], name="B")

    result = subprocess.run(
        [sys.executable, str(root / ci.VALIDATE_FILE)], capture_output=True, text=True
    )

    assert result.returncode == 1
    assert "dupliqué" in result.stderr
    assert "SystemExit" not in result.stderr
    assert "Traceback" not in result.stderr


def test_generated_validate_rejects_unknown_selection(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    write_export(root, "n8nPipelines/a.json", [{"name": "A", "type": "n8n-nodes-base.noOp", "typeVersion": 1}])
    write_selection(root, ["n8nPipelines/ghost.json"])

    result = subprocess.run(
        [sys.executable, str(root / ci.VALIDATE_FILE)], capture_output=True, text=True
    )

    assert result.returncode == 1
    assert "ne correspond à aucun export" in result.stderr
    assert "SystemExit" not in result.stderr
    assert "Traceback" not in result.stderr


def test_generated_validate_accepts_known_selection(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    write_export(root, "n8nPipelines/a.json", [{"name": "A", "type": "n8n-nodes-base.noOp", "typeVersion": 1}])
    write_selection(root, ["n8nPipelines/a.json"])

    result = subprocess.run(
        [sys.executable, str(root / ci.VALIDATE_FILE)], capture_output=True, text=True
    )

    assert result.returncode == 0
    assert "OK" in result.stdout


# --- generated runner.py (in-process with a fake HTTP client) --------------

_MANUAL = [{"name": "Bouton", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}]
_SCHEDULE = [
    {"name": "Cron", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1},
    {"name": "Pull", "type": "n8n-nodes-base.httpRequest", "typeVersion": 1},
]


def test_generated_runner_run_payload_variants(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)

    manual = write_export(root, "n8nPipelines/m.json", _MANUAL)
    assert runner.run_payload(manual) == (
        "trigger", {"triggerToStartFrom": {"name": "Bouton"}},
    )

    schedule = write_export(root, "n8nPipelines/s.json", _SCHEDULE)
    assert runner.run_payload(schedule) == (
        "destination", {"destinationNode": {"nodeName": "Pull", "mode": "inclusive"}},
    )

    webhook = write_export(
        root,
        "n8nPipelines/w.json",
        [{"name": "Hook", "type": "n8n-nodes-base.webhookTrigger", "typeVersion": 1}],
    )
    assert runner.run_payload(webhook) == (None, None)


def test_generated_runner_import_workflows_posts_missing_only(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    write_export(root, "n8nPipelines/a.json", _MANUAL, name="a")
    write_export(root, "n8nPipelines/b.json", [{"name": "New", "type": "n8n-nodes-base.noOp", "typeVersion": 1}], name="b")
    http = FakeHttp(
        {
            ("GET", "/api/v1/workflows"): (200, {"data": [{"id": "w1", "name": "a"}]}),
            ("POST", "/api/v1/workflows"): (200, {"id": "w2"}),
        }
    )

    mapping = runner.import_workflows(http, "api-key")

    assert mapping == {"n8nPipelines/a.json": "w1", "n8nPipelines/b.json": "w2"}
    posts = [call for call in http.calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["name"] == "b"
    assert "active" not in posts[0][2]  # whitelist drops server fields


def test_generated_runner_configure_credentials_from_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    monkeypatch.setenv(
        "N8N_CI_CREDENTIALS", json.dumps([{"name": "API", "type": "httpRequest", "data": {"u": "x"}}])
    )
    http = FakeHttp(
        {
            ("GET", "/api/v1/credentials"): (200, {"data": []}),
            ("POST", "/api/v1/credentials"): (200, {"id": "c1"}),
        }
    )

    created = runner.configure_credentials(http, "api-key")

    assert created == 1
    posts = [call for call in http.calls if call[0] == "POST"]
    assert posts[0][2] == {"name": "API", "type": "httpRequest", "data": {"u": "x"}}


def test_generated_runner_missing_secret_is_non_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    monkeypatch.delenv("N8N_CI_CREDENTIALS", raising=False)
    http = FakeHttp({})

    assert runner.configure_credentials(http, "api-key") == 0
    assert http.calls == []


def test_generated_runner_wait_execution_polls_to_finish(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp(
        {
            ("GET", "/rest/executions/e1"): (
                200,
                {"data": {"status": "success", "finished": True, "resultData": {}, "lastNodeExecuted": "Pull"}},
            )
        }
    )

    status, detail = runner.wait_execution(http, "e1")

    assert status == "success"
    assert "Pull" in detail
    assert http.calls[0][3] is None  # auth par cookie de session, pas d'en-tête


def test_generated_runner_wait_execution_treats_waiting_as_webhook(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp({("GET", "/rest/executions/e1"): (200, {"data": {"status": "waiting"}})})

    assert runner.wait_execution(http, "e1")[0] == "waiting"
    assert http.calls[0][3] is None


def test_generated_runner_summarize_counts_failures(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)

    assert runner.summarize([("a", "success"), ("b", "waiting")]) == 0
    assert runner.summarize([("a", "success"), ("b", "error")]) == 1


def test_generated_runner_trigger_run_returns_waiting_for_webhook(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    waiting_http = FakeHttp({("POST", "/rest/workflows/w1/run"): (200, {"waitingForWebhook": True})})
    running_http = FakeHttp({("POST", "/rest/workflows/w1/run"): (200, {"data": {"executionId": "e9"}})})

    assert runner.trigger_run(waiting_http, "w1", {}) is None
    assert runner.trigger_run(running_http, "w1", {}) == "e9"
    assert running_http.calls[0][3] is None


def test_generated_runner_trigger_run_accepts_flat_execution_id(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp({("POST", "/rest/workflows/w1/run"): (200, {"executionId": "e7"})})

    assert runner.trigger_run(http, "w1", {}) == "e7"


def test_generated_runner_login_succeeds_with_cookie(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp(
        {("POST", "/rest/login"): (200, {"data": {}})},
        cookies=("n8n-auth",),
    )

    assert runner.login(http) is None
    assert http.calls[0][2] == {
        "emailOrLdapLoginId": runner.OWNER_EMAIL,
        "password": runner.OWNER_PASSWORD,
    }


def test_generated_runner_login_accepts_body_token(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp({("POST", "/rest/login"): (200, {"data": {"token": "jwt-abc"}})})

    assert runner.login(http) is None


def test_generated_runner_login_without_session_raises(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp({("POST", "/rest/login"): (200, {"data": {}})})

    with pytest.raises(RuntimeError, match="connexion au propriétaire impossible"):
        runner.login(http)


def test_generated_runner_create_api_key_uses_cookie_session(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    http = FakeHttp(
        {("POST", "/rest/api-keys"): (200, {"data": {"rawApiKey": "n8n_key_1"}})}
    )

    secret = runner.create_api_key(http)

    assert secret == "n8n_key_1"
    assert http.calls[0][3] is None


def test_generated_runner_template_uses_cookie_auth_over_http(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    source = (root / ci.RUNNER_FILE).read_text(encoding="utf-8")

    # Le conteneur renonce au cookie « Secure » (rejeu en HTTP plain) et
    # l'auth /rest repose sur la session : jamais d'en-tête Authorization.
    assert "N8N_SECURE_COOKIE=false" in source
    assert "Authorization" not in source
    assert "_rest_headers" not in source


def test_generated_runner_read_selection_limits_to_selected(tmp_path: Path) -> None:
    root = render_harness_at(tmp_path)
    runner = load_runner(root)
    write_export(root, "n8nPipelines/a.json", _MANUAL)
    write_selection(root, ["n8nPipelines/a.json"])

    assert runner.read_selection() == {"n8nPipelines/a.json"}