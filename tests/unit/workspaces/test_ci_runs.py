"""Unit tests for the pure CI runs models/parsing (workspaces/ci_runs.py)."""

from __future__ import annotations

import pytest

from n8n_launcher.workspaces import ci_runs


def run_payload(**overrides) -> dict:
    payload = {
        "id": 11,
        "run_number": 7,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-01-01T00:00:00Z",
        "html_url": "https://github.com/octo/repo/actions/runs/11",
    }
    payload.update(overrides)
    return payload


# --- run_summary -------------------------------------------------------------


def test_run_summary_normalises_payload() -> None:
    summary = ci_runs.run_summary(run_payload())

    assert summary == ci_runs.RunSummary(
        id=11,
        run_number=7,
        branch="main",
        head_sha="a" * 40,
        status="completed",
        conclusion="success",
        created_at="2026-01-01T00:00:00Z",
        url="https://github.com/octo/repo/actions/runs/11",
    )
    assert summary.key == "run-11"
    assert summary.human_status == "terminé"


def test_run_summary_tolerates_missing_and_wrong_types() -> None:
    summary = ci_runs.run_summary(
        {"id": "oops", "conclusion": "", "created_at": None, "html_url": 42}
    )

    assert summary.id == 0
    assert summary.run_number == 0
    assert summary.branch == ""
    assert summary.conclusion is None
    assert summary.created_at is None
    assert summary.url is None


def test_run_human_status_falls_back_to_raw_status() -> None:
    summary = ci_runs.run_summary(run_payload(status="mystère", conclusion=None))

    assert summary.human_status == "mystère"


# --- job_summary -------------------------------------------------------------


def test_job_summary_normalises_payload_and_steps() -> None:
    summary = ci_runs.job_summary(
        {
            "id": 21,
            "name": "validate",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/octo/repo/actions/jobs/21",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"id": "run-tests", "status": "in_progress"},
                {"no_name_or_id": "", "conclusion": "success"},
                "not-a-dict",
            ],
        }
    )

    assert summary.id == 21
    assert summary.name == "validate"
    assert summary.conclusion == "failure"
    assert summary.steps == (("Checkout", "success"), ("run-tests", "in_progress"))
    assert summary.human_status == "échoué"


def test_job_summary_without_steps_has_empty_tuple() -> None:
    summary = ci_runs.job_summary({"id": 21, "name": "validate"})

    assert summary.steps == ()
    assert summary.conclusion is None


def test_job_human_status_prefers_conclusion_then_status() -> None:
    assert ci_runs.job_summary({"status": "queued"}).human_status == "en file d'attente"
    assert (
        ci_runs.job_summary({"status": "completed", "conclusion": "cancelled"}).human_status
        == "annulé"
    )
    assert ci_runs.job_summary({"status": "inconnu"}).human_status == "inconnu"


# --- parse_pipeline_lines ----------------------------------------------------


def test_parse_progress_lines_keeps_last_status_per_pipeline() -> None:
    log = "\n".join(
        [
            "[runner] n8nPipelines/a.json : waiting",
            "[runner] n8nPipelines/b.json : success (3 nodes)",
            "[runner] n8nPipelines/a.json : success",
            "[runner] n8nPipelines/c.json : failure (node HTTP)",
            "random unrelated output",
        ]
    )

    results = ci_runs.parse_pipeline_lines(log)

    by_rel = {result.rel: result for result in results}
    assert set(by_rel) == {"n8nPipelines/a.json", "n8nPipelines/b.json", "n8nPipelines/c.json"}
    assert by_rel["n8nPipelines/a.json"].status == "success"
    assert by_rel["n8nPipelines/b.json"].detail == "3 nodes"
    assert by_rel["n8nPipelines/c.json"].status == "failure"
    assert by_rel["n8nPipelines/c.json"].mark == "\u2718"
    assert by_rel["n8nPipelines/c.json"].label == "en échec"


def test_parse_ignores_unknown_progress_status() -> None:
    log = "[runner] n8nPipelines/a.json : coffee-break"

    assert ci_runs.parse_pipeline_lines(log) == []


def test_parse_summary_block_wins_over_progress() -> None:
    log = "\n".join(
        [
            "[runner] n8nPipelines/a.json : failure",
            "Résultats des pipelines :",
            "  - \u2714 n8nPipelines/a.json (réussie)",
            "  - \u2718 n8nPipelines/b.json (noeud HTTP en échec)",
            "réussies : 1/2",
        ]
    )

    results = ci_runs.parse_pipeline_lines(log)

    assert [(result.rel, result.status) for result in results] == [
        ("n8nPipelines/a.json", "success"),
        ("n8nPipelines/b.json", "failure"),
    ]
    # Default labels are dropped from the detail, custom ones are kept.
    assert results[0].detail == ""
    assert results[1].detail == "noeud HTTP en échec"


def test_parse_summary_block_supports_waiting_mark() -> None:
    log = "\n".join(
        [
            "Résultats des pipelines :",
            "  - \u25fb n8nPipelines/a.json (en attente)",
        ]
    )

    results = ci_runs.parse_pipeline_lines(log)

    assert results[0].status == "waiting"
    assert results[0].mark == "\u25fb"


def test_parse_summary_block_stops_on_malformed_row() -> None:
    log = "\n".join(
        [
            "Résultats des pipelines :",
            "  cette ligne ne commence pas par un tiret",
            "  - \u2714 n8nPipelines/late.json (réussie)",
        ]
    )

    assert ci_runs.parse_pipeline_lines(log) == []


def test_parse_empty_log_returns_empty_list() -> None:
    assert ci_runs.parse_pipeline_lines("") == []


# --- RunsSnapshot / compose_snapshot ----------------------------------------


def test_compose_snapshot_normalises_everything() -> None:
    snapshot = ci_runs.compose_snapshot(
        repo_path="octo/repo",
        runs=[run_payload(id=11), run_payload(id=12, status="in_progress", conclusion=None)],
        raw_jobs={
            11: [{"id": 21, "name": "validate", "status": "completed", "conclusion": "success"}],
            12: [{"id": 22, "name": "test", "status": "queued"}],
        },
        pipelines={
            21: [
                ci_runs.PipelineResult(
                    rel="n8nPipelines/a.json", status="success", detail="", mark="\u2714"
                )
            ]
        },
        fetched_at="2026-01-01T00:00:00Z",
    )

    assert snapshot.repo_path == "octo/repo"
    assert [run.id for run in snapshot.runs] == [11, 12]
    assert snapshot.jobs[11][0].name == "validate"
    assert snapshot.jobs[12][0].name == "test"
    assert snapshot.pipelines[21][0].rel == "n8nPipelines/a.json"
    assert snapshot.error is None
    assert snapshot.fetched_at == "2026-01-01T00:00:00Z"
    assert snapshot.has_data is True


def test_compose_snapshot_skips_non_dict_payloads() -> None:
    snapshot = ci_runs.compose_snapshot(
        repo_path="octo/repo",
        runs=[run_payload(), "not-a-dict"],
        raw_jobs={11: ["not-a-dict", {"id": 21, "name": "validate"}]},
    )

    assert [run.id for run in snapshot.runs] == [11]
    assert snapshot.jobs[11][0].id == 21


def test_compose_snapshot_error_snapshot_has_no_data() -> None:
    snapshot = ci_runs.compose_snapshot(
        repo_path="octo/repo", runs=[], error="Token GitHub manquant."
    )

    assert snapshot.runs == ()
    assert snapshot.jobs == {}
    assert snapshot.pipelines == {}
    assert snapshot.error == "Token GitHub manquant."
    assert snapshot.has_data is False


def test_runs_snapshot_without_runs_has_no_data() -> None:
    assert ci_runs.RunsSnapshot("octo/repo").has_data is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [("success", "réussie"), ("waiting", "en attente"), ("failure", "en échec")],
)
def test_pipeline_label_matches_status(status, expected) -> None:
    result = ci_runs.PipelineResult(rel="a.json", status=status, detail="", mark="?")

    assert result.label == expected
