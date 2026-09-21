"""Unit tests for the CI harness builder (workspaces/ci.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from n8n_launcher.workspaces import ci


def write_export(workflows_dir: Path, rel: str, nodes, **extra) -> dict:
    path = workflows_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    export = {
        "name": path.stem,
        "nodes": nodes,
        "connections": {},
        "settings": {},
        **extra,
    }
    path.write_text(json.dumps(export), encoding="utf-8")
    return export


def manual_trigger(name: str = "Quand j'appuie sur un bouton") -> dict:
    return {"name": name, "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}


def schedule_trigger(name: str = "Tous les jours") -> dict:
    return {"name": name, "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1}


def webhook_trigger(name: str = "Webhook") -> dict:
    return {"name": name, "type": "n8n-nodes-base.webhookTrigger", "typeVersion": 1}


def http_node(name: str = "API", credential: dict | None = None) -> dict:
    node = {"name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 1}
    if credential is not None:
        node["credentials"] = credential
    return node


# --- URL ---------------------------------------------------------------


def test_github_repo_path_accepts_https_bare_and_git_suffix() -> None:
    assert ci.github_repo_path("https://github.com/owner/repo") == "owner/repo"
    assert ci.github_repo_path("https://github.com/owner/repo.git") == "owner/repo"
    assert ci.github_repo_path("https://github.com/owner/repo.git/") == "owner/repo"


def test_github_repo_path_accepts_ssh_shapes() -> None:
    assert ci.github_repo_path("git@github.com:owner/repo.git") == "owner/repo"
    assert ci.github_repo_path("ssh://git@github.com/owner/repo.git") == "owner/repo"


def test_github_repo_path_rejects_non_github_targets() -> None:
    assert ci.github_repo_path(None) is None
    assert ci.github_repo_path("") is None
    assert ci.github_repo_path("https://gitlab.com/owner/repo.git") is None
    assert ci.github_repo_path("/home/user/projects/repo") is None
    assert ci.github_repo_path("https://github.example.com/owner/repo.git") is None


def test_actions_url_builds_github_page() -> None:
    assert ci.actions_url("owner/repo") == "https://github.com/owner/repo/actions"


# --- Collection / selection --------------------------------------------


def test_collect_workflows_mirrors_import_layout(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    sub = root / "n8nPipelines"
    sub.mkdir(parents=True)
    (sub / "Beta.json").write_text("{}", encoding="utf-8")
    (sub / "Alpha.json").write_text("{}", encoding="utf-8")
    (root / "root.json").write_text("{}", encoding="utf-8")
    (root / "package.json").write_text("{}", encoding="utf-8")

    found = ci.collect_workflows(root)

    assert found == ["n8nPipelines/Alpha.json", "n8nPipelines/Beta.json", "root.json"]
    assert "package.json" not in found


def test_load_export_returns_none_for_unreadable_or_invalid(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "bad.json").write_text("{not json", encoding="utf-8")
    (root / "no-nodes.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")

    assert ci.load_export(root, "missing.json") is None
    assert ci.load_export(root, "bad.json") is None
    assert ci.load_export(root, "no-nodes.json") is None
    assert ci.load_export(root, "good.json") is None


def test_selection_roundtrip_persists_sorted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ci.write_selection(root, {"n8nPipelines/zz.json", "n8nPipelines/aa.json"})

    persisted = ci.selection_path(root).read_text(encoding="utf-8")
    assert '"selected"' in persisted
    assert persisted.index("aa.json") < persisted.index("zz.json")
    assert ci.read_selection(root) == {"n8nPipelines/aa.json", "n8nPipelines/zz.json"}


def test_read_selection_defaults_to_empty_on_garbage(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    assert ci.read_selection(root) == set()
    ci.selection_path(root).parent.mkdir(parents=True)
    ci.selection_path(root).write_text("nope", encoding="utf-8")
    assert ci.read_selection(root) == set()


def test_provided_credentials_keys_by_type_name() -> None:
    provided = ci.provided_credentials([{"name": "API", "type": "httpRequest"}])
    assert provided == {"httpRequest/API"}


def test_provided_credentials_skips_empty_parts() -> None:
    provided = ci.provided_credentials([{"name": "  ", "type": "httpRequest"}])
    assert provided == set()


# --- Credentials --------------------------------------------------------


def test_missing_credentials_ignores_pinned_nodes(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [manual_trigger(), http_node("Pinned API", {"httpHeaderAuth": {"name": "API", "type": "httpRequest"}})],
        pinData={"Pinned API": {}},
    )

    assert ci.missing_credentials(export, set()) == []


def test_missing_credentials_reports_uncovered_nodes(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [manual_trigger(), http_node("Live API", {"httpHeaderAuth": {"name": "API", "type": "httpRequest"}})],
    )

    assert ci.missing_credentials(export, set()) == ["API (httpRequest)"]
    assert ci.missing_credentials(export, {"httpRequest/API"}) == []


# --- Trigger selection ---------------------------------------------------


def test_default_start_trigger_prefers_manual(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [webhook_trigger("Hook"), manual_trigger("Bouton")],
        pinData={"Hook": {}},
    )
    assert ci.default_start_trigger(export) == "Bouton"


def test_default_start_trigger_falls_back_to_pinned(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [webhook_trigger("Hook")],
        pinData={"Hook": {}},
    )
    assert ci.default_start_trigger(export) == "Hook"


def test_default_start_trigger_none_without_startable_trigger(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [schedule_trigger("Cron")],
    )
    assert ci.default_start_trigger(export) is None


# --- Eligibility ---------------------------------------------------------


def test_workflow_eligibility_requires_a_trigger(tmp_path: Path) -> None:
    export = write_export(tmp_path, "n8nPipelines/p.json", [http_node()])
    ok, reason = ci.workflow_eligibility(export, set())
    assert ok is False
    assert reason == "aucun déclencheur"


def test_workflow_eligibility_rejects_unpinned_webhook(tmp_path: Path) -> None:
    export = write_export(tmp_path, "n8nPipelines/p.json", [webhook_trigger("Hook")])
    ok, reason = ci.workflow_eligibility(export, set())
    assert ok is False
    assert "non épinglé" in reason


def test_workflow_eligibility_accepts_pinned_webhook(tmp_path: Path) -> None:
    export = write_export(
        tmp_path, "n8nPipelines/p.json", [webhook_trigger("Hook")], pinData={"Hook": {}}
    )
    assert ci.workflow_eligibility(export, set()) == (True, "")


def test_workflow_eligibility_blocked_by_missing_credentials(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [manual_trigger(), http_node("API", {"httpHeaderAuth": {"name": "API", "type": "httpRequest"}})],
    )
    ok, reason = ci.workflow_eligibility(export, set())
    assert ok is False
    assert reason == "credentials manquantes : API (httpRequest)"


def test_workflow_eligibility_ok_with_schedule_and_covered_credentials(tmp_path: Path) -> None:
    export = write_export(
        tmp_path,
        "n8nPipelines/p.json",
        [schedule_trigger(), http_node("API", {"httpHeaderAuth": {"name": "API", "type": "httpRequest"}})],
    )
    assert ci.workflow_eligibility(export, {"httpRequest/API"}) == (True, "")


# --- Descriptions --------------------------------------------------------


def test_start_description_reports_manual_then_schedule_then_pinned(tmp_path: Path) -> None:
    assert ci.start_description(
        write_export(tmp_path, "a.json", [manual_trigger("Bouton")])
    ) == "déclencheur manuel « Bouton »"
    assert ci.start_description(write_export(tmp_path, "b.json", [schedule_trigger()])) == (
        "déclencheur programmé"
    )
    assert (
        ci.start_description(
            write_export(tmp_path, "c.json", [webhook_trigger("Hook")], pinData={"Hook": {}})
        )
        == "déclencheur épinglé « Hook »"
    )
    assert ci.start_description(write_export(tmp_path, "d.json", [webhook_trigger()])) == (
        "aucun déclencheur testable"
    )


def test_node_detail_reports_pinned_and_credential_style(tmp_path: Path) -> None:
    export = write_export(tmp_path, "p.json", [manual_trigger("Bouton")])
    node = {"name": "Bouton", "type": "n8n-nodes-base.manualTrigger"}
    assert ci.node_detail(export, node) == ("n8n-nodes-base.manualTrigger", "warn")
    export["pinData"] = {"Bouton": {}}
    assert ci.node_detail(export, node) == ("n8n-nodes-base.manualTrigger, épinglé", "muted")


# --- Counters ------------------------------------------------------------


def test_ci_counts_covers_selection(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    write_export(root, "n8nPipelines/ok.json", [manual_trigger()])
    write_export(root, "n8nPipelines/hook.json", [webhook_trigger()])
    ci.write_selection(root, {"n8nPipelines/ok.json", "n8nPipelines/hook.json"})

    counts = ci.ci_counts(root, set())

    assert counts == {"eligible": 1, "selected": 2, "selected_eligible": 1}


# --- Rendering ------------------------------------------------------------


def test_render_harness_emits_all_generated_files() -> None:
    rendered = ci.render_harness("2.35.0")

    assert set(rendered) == {
        ci.WORKFLOW_FILE,
        ci.VALIDATE_FILE,
        ci.RUNNER_FILE,
        f"{ci.CI_DIR}/{ci.SELECTION_FILE}",
    }
    assert ci.GENERATED_MARKER in rendered[ci.WORKFLOW_FILE]
    assert "__N8N_IMAGE__" not in rendered[ci.RUNNER_FILE]


def test_render_harness_pins_safer_image_for_weird_version() -> None:
    rendered = ci.render_harness("2.35.0")
    assert "docker.n8n.io/n8nio/n8n:2.35.0" in rendered[ci.RUNNER_FILE]

    rendered_weird = ci.render_harness("$(rm -rf /) && malicious")
    assert "docker.n8n.io/n8nio/n8n:2.40.0" in rendered_weird[ci.RUNNER_FILE]


def test_rendered_workflow_wires_secret_and_var() -> None:
    workflow = ci.render_harness("2.35.0")[ci.WORKFLOW_FILE]

    assert "N8N_CI_CREDENTIALS" in workflow
    assert "vars.N8N_IMAGE" in workflow
    assert "workflow_dispatch" in workflow
    assert "python .n8n-tests/runner.py" in workflow


def test_rendered_workflow_skips_test_job_when_empty_selection() -> None:
    workflow = ci.render_harness("2.35.0")[ci.WORKFLOW_FILE]

    assert "Compter les pipelines sélectionnées" in workflow
    assert "steps.selection.outputs.count != '0'" in workflow
    assert "python .n8n-tests/runner.py" in workflow


def test_rendered_scripts_compile_and_validate_static_exports(tmp_path: Path) -> None:
    rendered = ci.render_harness("2.35.0")
    root = tmp_path / "ws"
    for rel, content in rendered.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    import py_compile

    for rel in (ci.VALIDATE_FILE, ci.RUNNER_FILE):
        py_compile.compile(root / rel, doraise=True)

    result = subprocess.run(
        [sys.executable, str(root / ci.VALIDATE_FILE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "SystemExit" not in result.stderr