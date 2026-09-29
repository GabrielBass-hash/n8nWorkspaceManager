"""Unit tests for the pure CI runs models/parsing (workspaces/ci_runs.py)."""

from __future__ import annotations

from datetime import datetime

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


def test_job_current_step_returns_first_unfinished_step() -> None:
    job = ci_runs.job_summary(
        {
            "id": 21,
            "name": "test",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"id": "run-tests", "status": "in_progress"},
                {"id": "after", "status": "queued"},
            ],
        }
    )

    assert job.current_step == ("run-tests", "in_progress")


def test_job_current_step_none_when_all_completed_or_empty() -> None:
    assert (
        ci_runs.job_summary(
            {
                "id": 21,
                "name": "validate",
                "steps": [{"name": "Checkout", "conclusion": "success"}],
            }
        ).current_step
        is None
    )
    assert ci_runs.job_summary({"id": 21, "name": "validate"}).current_step is None


# --- run_status_is_active ----------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["queued", "waiting", "in_progress", "pending", "requested"],
)
def test_run_status_is_active_accepts_in_flight_statuses(status: str) -> None:
    assert ci_runs.run_status_is_active(status) is True


@pytest.mark.parametrize(
    "status",
    ["completed", "success", "failure", "cancelled", "timed_out", "", None],
)
def test_run_status_is_active_rejects_finished_or_unknown(status) -> None:
    assert ci_runs.run_status_is_active(status or "") is False


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


def test_compose_snapshot_carries_a_note_without_an_error() -> None:
    # A note explains an intentionally empty read (no GitHub remote); it must
    # never read as a failure.
    snapshot = ci_runs.compose_snapshot(repo_path="", runs=[], note="  pas de dépôt  ")

    assert snapshot.note == "pas de dépôt"
    assert snapshot.error is None
    assert snapshot.has_data is False


def test_runs_snapshot_normalises_a_blank_note_to_none() -> None:
    assert ci_runs.RunsSnapshot("octo/repo", note="   ").note is None
    assert ci_runs.RunsSnapshot("octo/repo").note is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [("success", "réussie"), ("waiting", "en attente"), ("failure", "en échec")],
)
def test_pipeline_label_matches_status(status, expected) -> None:
    result = ci_runs.PipelineResult(rel="a.json", status=status, detail="", mark="?")

    assert result.label == expected


def test_parse_summary_merges_progress_failure_detail() -> None:
    log = "\n".join(
        [
            "[runner] n8nPipelines/a.json : failure (HTTP 500 sur l'API)",
            "Résultats des pipelines :",
            "  - ✘ n8nPipelines/a.json (en échec)",
        ]
    )

    result = ci_runs.parse_pipeline_lines(log)[0]

    assert result.status == ci_runs.PIPELINE_FAILURE
    assert result.detail == "HTTP 500 sur l'API"
    assert result.failure_detail == "HTTP 500 sur l'API"


def test_parse_normalises_failure_aliases_and_keeps_log_timestamp() -> None:
    log = "2026-01-01T12:34:56Z [runner] n8nPipelines/a.json : error (timeout)"

    result = ci_runs.parse_pipeline_lines(log)[0]

    assert result.status == ci_runs.PIPELINE_FAILURE
    assert result.detail == "timeout"
    assert result.timestamp == "2026-01-01T12:34:56Z"


def test_parse_failure_detail_line_survives_without_progress() -> None:
    log = "échec : n8nPipelines/a.json (nœud HTTP : connexion refusée)"

    result = ci_runs.parse_pipeline_lines(log)[0]

    assert result.rel == "n8nPipelines/a.json"
    assert result.status == ci_runs.PIPELINE_FAILURE
    assert result.detail == "nœud HTTP : connexion refusée"


def test_run_and_job_timestamps_are_normalised() -> None:
    run = ci_runs.run_summary(
        run_payload(
            updated_at="2026-01-01T00:01:00Z",
            run_started_at="2026-01-01T00:00:30Z",
        )
    )
    job = ci_runs.job_summary(
        {
            "id": 21,
            "name": "test",
            "run_id": 11,
            "started_at": "2026-01-01T00:02:00Z",
            "completed_at": "2026-01-01T00:03:00Z",
        }
    )

    assert run.updated_at == "2026-01-01T00:01:00Z"
    assert run.started_at == "2026-01-01T00:00:30Z"
    assert job.run_id == 11
    assert job.started_at == "2026-01-01T00:02:00Z"
    assert job.completed_at == "2026-01-01T00:03:00Z"


def test_compose_snapshot_retains_runs_and_exposes_partial_messages() -> None:
    snapshot = ci_runs.compose_snapshot(
        repo_path="octo/repo",
        runs=[run_payload()],
        warnings=["Les jobs n'ont pas pu être récupérés."],
        partial_errors=["Le log du job 21 est indisponible."],
    )

    assert snapshot.has_data is True
    assert snapshot.is_partial is True
    assert snapshot.messages == (
        "Les jobs n'ont pas pu être récupérés.",
        "Le log du job 21 est indisponible.",
    )


def test_compose_snapshot_keeps_runs_with_top_level_error() -> None:
    snapshot = ci_runs.compose_snapshot(
        repo_path="octo/repo",
        runs=[run_payload()],
        error="La liste est partiellement obsolète.",
    )

    assert snapshot.has_data is True
    assert snapshot.messages == ("La liste est partiellement obsolète.",)


def test_pipeline_display_detail_contains_label_and_detail() -> None:
    result = ci_runs.PipelineResult(rel="a.json", status="failure", detail="HTTP 500", mark="!")

    assert result.display_detail == "en échec — HTTP 500"


# --- human-readable summary --------------------------------------------------


def snapshot(**overrides) -> ci_runs.RunsSnapshot:
    """Return a snapshot with sensible empty defaults, overridden per test."""
    fields = {
        "repo_path": "octo/repo",
        "runs": (),
        "jobs": {},
        "pipelines": {},
        "error": None,
        "fetched_at": None,
        "warnings": (),
        "partial_errors": (),
        "note": None,
    }
    fields.update(overrides)
    return ci_runs.RunsSnapshot(**fields)


def run_summary(run_id: int = 11, **overrides) -> ci_runs.RunSummary:
    fields = {
        "id": run_id,
        "run_number": run_id,
        "branch": "dev",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-01-01T00:00:00Z",
        "url": f"https://github.com/octo/repo/actions/runs/{run_id}",
    }
    fields.update(overrides)
    return ci_runs.RunSummary(**fields)


def test_runs_summary_reports_a_read_failure_and_no_runs() -> None:
    assert ci_runs.runs_summary_text(snapshot(error="boom")) == (
        "GitHub Actions indisponible : boom"
    )


def test_runs_summary_reports_an_empty_snapshot_with_its_messages() -> None:
    assert ci_runs.runs_summary_text(snapshot()) == "Aucun run GitHub Actions pour ce workspace."
    with_messages = snapshot(partial_errors=("Rate limited.",))
    assert ci_runs.runs_summary_text(with_messages).endswith("Rate limited.")


def test_runs_summary_lets_a_note_outrank_an_error() -> None:
    # A note explains an empty view nobody asked for: a configuration, not a
    # failure, so it must not be reported as one.
    assert ci_runs.runs_summary_text(snapshot(note="pas de dépôt")) == "pas de dépôt"
    assert ci_runs.runs_summary_text(snapshot(error="boom", note="pas de dépôt")) == "pas de dépôt"


def test_runs_summary_counts_finished_and_in_flight_runs() -> None:
    text = ci_runs.runs_summary_text(
        snapshot(
            runs=[
                run_summary(11, conclusion="success"),
                run_summary(12, conclusion=None, status="in_progress"),
            ]
        )
    )
    assert text == "2 run(s) · 1 terminé(s) · 1 en cours"


def test_runs_summary_omits_the_in_flight_count_when_nothing_is_running() -> None:
    text = ci_runs.runs_summary_text(snapshot(runs=[run_summary(11)]))
    assert text == "1 run(s) · 1 terminé(s)"


def test_runs_summary_appends_the_last_poll_time() -> None:
    text = ci_runs.runs_summary_text(
        snapshot(runs=[run_summary(11)], fetched_at="2026-09-18T14:03:05+02:00")
    )
    assert text == "1 run(s) · 1 terminé(s) — à jour 14:03:05"


def test_runs_summary_appends_partial_failures_without_hiding_the_runs() -> None:
    text = ci_runs.runs_summary_text(
        snapshot(runs=[run_summary(11)], partial_errors=("Le log du job 21 est indisponible.",))
    )
    assert text.startswith("1 run(s) · 1 terminé(s)")
    assert "Avertissement : Le log du job 21 est indisponible." in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-18T14:03:05+02:00", "14:03:05"),
        ("2026-09-18T12:03:05Z", "12:03:05"),
        ("2026-09-18T14:03:05", "14:03:05"),
        ("garbage", ""),
        ("", ""),
    ],
)
def test_format_fetched_at_renders_or_gives_up(raw: str, expected: str) -> None:
    assert ci_runs.format_fetched_at(raw) == expected


@pytest.mark.parametrize(
    # A naive timestamp has no zone to shift, so these are exact everywhere.
    ("raw", "expected"),
    [
        ("2026-09-18T14:03:05", "18/09/2026 14:03"),
        ("garbage", ""),
        (None, ""),
    ],
)
def test_format_timestamp_renders_or_gives_up(raw: str | None, expected: str) -> None:
    assert ci_runs.format_timestamp(raw) == expected


def test_format_timestamp_converts_an_aware_timestamp_to_local_time() -> None:
    # GitHub sends UTC with a ``Z``, which older fromisoformat rejects; the
    # rendered value is local, so it is compared against the same conversion
    # rather than a hardcoded hour.
    rendered = ci_runs.format_timestamp("2026-01-01T00:00:00Z")
    assert rendered == datetime.fromisoformat("2026-01-01T00:00:00+00:00").astimezone().strftime(
        "%d/%m/%Y %H:%M"
    )
    # An explicit offset is honoured the same way.
    assert ci_runs.format_timestamp("2026-09-18T14:03:05+02:00") == (
        datetime.fromisoformat("2026-09-18T14:03:05+02:00").astimezone().strftime("%d/%m/%Y %H:%M")
    )
