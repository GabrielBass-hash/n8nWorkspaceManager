"""Tests for the read-only GitHub Actions runs panel (gui/ci_runs.py)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from helpers import FakeTk, FakeTtk, fake_runs_panel_bases

from n8n_launcher.gui.ci_runs import RunsPanel, _format_fetched_at, runs_summary_text
from n8n_launcher.workspaces import ci_runs


def _run(
    run_id: int = 11,
    *,
    conclusion: str | None = "success",
    status: str = "completed",
    created_at: str | None = "2026-01-01T00:00:00Z",
):
    return ci_runs.RunSummary(
        id=run_id,
        run_number=run_id,
        branch="main",
        head_sha="a" * 40,
        status=status,
        conclusion=conclusion,
        created_at=created_at,
        url=f"https://github.com/octo/repo/actions/runs/{run_id}",
    )


def _job(
    job_id: int = 21,
    name: str = "validate",
    *,
    conclusion: str | None = "success",
    status: str = "completed",
    steps=(),
    started_at: str | None = None,
    completed_at: str | None = None,
):
    return ci_runs.JobSummary(
        id=job_id,
        name=name,
        status=status,
        conclusion=conclusion,
        url=f"https://github.com/octo/repo/actions/jobs/{job_id}",
        steps=tuple(steps),
        started_at=started_at,
        completed_at=completed_at,
    )


def _pipeline(
    rel: str = "n8nPipelines/a.json",
    status: str = "success",
    detail: str = "",
    timestamp: str | None = None,
):
    mark = {"success": "+", "failure": "!", "waiting": "*"}[status]
    return ci_runs.PipelineResult(
        rel=rel, status=status, detail=detail, mark=mark, timestamp=timestamp
    )


def _snapshot(
    *,
    runs=(),
    jobs=None,
    pipelines=None,
    error=None,
    fetched_at=None,
    warnings=(),
    partial_errors=(),
):
    return ci_runs.RunsSnapshot(
        repo_path="octo/repo",
        runs=tuple(runs),
        jobs=dict(jobs or {}),
        pipelines=dict(pipelines or {}),
        error=error,
        fetched_at=fetched_at,
        warnings=tuple(warnings),
        partial_errors=tuple(partial_errors),
    )


def _build(refresh=None, open_run=None, run=None):
    """Create a panel wired to recording callbacks and return both."""
    refresh = refresh or MagicMock()
    open_run = open_run or MagicMock()
    panel = RunsPanel(FakeTk.Frame(None), refresh=refresh, open_run=open_run, run=run)
    return panel, refresh, open_run


def _local_timestamp(iso_timestamp: str) -> str:
    """Return the machine-local timestamp rendered by the runs panel."""
    return datetime.fromisoformat(iso_timestamp).astimezone().strftime("%d/%m/%Y %H:%M")


def test_apply_renders_runs_jobs_and_pipelines() -> None:
    snapshot = _snapshot(
        runs=[_run(11)],
        jobs={11: (_job(21),)},
        pipelines={21: (_pipeline(detail="3 nœuds"),)},
    )

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    run_row = panel.tree.item("run-11")
    assert run_row["text"].startswith("+")
    assert "#11" in run_row["text"]
    assert "main" in run_row["text"]
    assert run_row["values"] == ["success"]
    assert run_row["tags"] == ["success"]

    job_row = panel.tree.item("job-11-21")
    assert job_row["text"].strip().startswith("+")
    assert "validate" in job_row["text"]
    assert job_row["values"] == ["réussi"]

    pipeline_id = panel.tree.get_children("job-11-21")[0]
    pipeline_row = panel.tree.item(pipeline_id)
    assert pipeline_row["text"].strip().startswith("+")
    assert "n8nPipelines/a.json" in pipeline_row["text"]
    assert pipeline_row["values"] == ["3 nœuds"]


def test_apply_marks_failed_run_and_waiting_pipeline() -> None:
    snapshot = _snapshot(
        runs=[_run(11, conclusion="failure")],
        jobs={11: (_job(21, conclusion="failure"),)},
        pipelines={21: (_pipeline(status="waiting"),)},
    )

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    assert panel.tree.item("run-11")["text"].startswith("!")
    assert panel.tree.item("run-11")["tags"] == ["failure"]
    pipeline_id = panel.tree.get_children("job-11-21")[0]
    assert panel.tree.item(pipeline_id)["tags"] == ["muted"]


def test_apply_empty_snapshot_shows_placeholder() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot())

    assert panel.tree.get_children() == []
    assert panel._empty._options["text"] == "Aucun run GitHub Actions pour ce workspace."
    assert panel._summary._options["text"] == "Aucun run GitHub Actions pour ce workspace."


def test_apply_error_snapshot_shows_message() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot(error="HTTP 401"))

    assert "GitHub Actions indisponible : HTTP 401" in panel._empty._options["text"]
    assert not panel.tree.get_children()


def test_apply_preserves_expansion_state() -> None:
    snapshot = _snapshot(runs=[_run(11)], jobs={11: (_job(21),)})

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)
        assert panel.tree.item("run-11")["open"] is False

        panel.remember_expansion("run-11", True)
        assert panel.expanded == {"run-11"}
        panel.apply(snapshot)
        assert panel.tree.item("run-11")["open"] is True

        panel.remember_expansion("run-11", False)
        panel.apply(snapshot)
        assert panel.tree.item("run-11")["open"] is False


def test_apply_replaces_previous_rows() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot(runs=[_run(11), _run(12)]))
        assert set(panel.tree.get_children()) == {"run-11", "run-12"}

        panel.apply(_snapshot(runs=[_run(13)]))
        assert panel.tree.get_children() == ["run-13"]


def test_apply_noops_on_destroyed_panel() -> None:
    # The real-runs reproduction: a background fetch delivered while the CI
    # dialog was being closed calls apply() on a frame whose interpreter is
    # gone — every tree op would raise ``TclError: invalid command name``.
    # apply() must bail out quietly instead of touching the dead widgets.
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot(runs=[_run(11)]))
        assert panel.tree.get_children() == ["run-11"]

        panel.destroy()
        panel.apply(_snapshot(runs=[_run(12)]))

        # No new rows were rendered after destruction.
        assert panel.tree.get_children() == ["run-11"]


def test_refresh_forwards_to_host_callback() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, refresh, _open = _build()
        panel.refresh()

    refresh.assert_called_once_with()


def test_run_button_absent_without_host_callback() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()

    assert not hasattr(panel, "_run_btn")


def test_run_button_forwards_to_host_callback() -> None:
    run = MagicMock()
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build(run=run)
        assert panel._run_btn.text == "Lancer la CI"
        panel._run_btn.command()
        panel.run()

    assert run.call_count == 2


def test_double_click_opens_selected_run() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, open_run = _build()
        panel.apply(_snapshot(runs=[_run(11)]))
        panel.tree.selection_set("run-11")
        panel.tree._bindings["<Double-1>"](None)

    open_run.assert_called_once()
    assert open_run.call_args.args[0].id == 11


def test_open_without_selection_does_nothing() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, open_run = _build()
        panel.apply(_snapshot(runs=[_run(11)]))
        panel._open_selected()

    open_run.assert_not_called()


def test_runs_summary_text_variants() -> None:
    assert runs_summary_text(_snapshot(error="boom")) == "GitHub Actions indisponible : boom"
    assert runs_summary_text(_snapshot()) == "Aucun run GitHub Actions pour ce workspace."
    summary = runs_summary_text(
        _snapshot(
            runs=[
                _run(11, conclusion="success"),
                _run(12, conclusion=None, status="in_progress"),
            ]
        )
    )
    assert summary == "2 run(s) · 1 terminé(s) · 1 en cours"


def test_runs_summary_text_includes_last_poll_time() -> None:
    summary = runs_summary_text(_snapshot(runs=[_run(11)], fetched_at="2026-09-18T14:03:05+02:00"))

    assert summary == "1 run(s) · 1 terminé(s) — à jour 14:03:05"


def test_has_active_run_tracks_last_snapshot() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        assert panel.has_active_run is False

        panel.apply(_snapshot(runs=[_run(11, conclusion=None, status="in_progress")]))
        assert panel.has_active_run is True

        panel.apply(_snapshot(runs=[_run(11)]))
        assert panel.has_active_run is False


def test_run_button_state_follows_snapshot() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build(run=MagicMock())

        panel.apply(_snapshot(runs=[_run(11, conclusion=None, status="in_progress")]))
        assert panel._run_btn.state == "disabled"

        panel.apply(_snapshot(runs=[_run(11, conclusion="failure")]))
        assert panel._run_btn.state == "normal"


def test_run_button_toggle_is_noop_without_host() -> None:
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.set_run_enabled(False)
        assert not hasattr(panel, "_run_btn")


def test_apply_auto_expands_active_run_and_live_job() -> None:
    snapshot = _snapshot(
        runs=[_run(11, conclusion=None, status="in_progress")],
        jobs={
            11: (
                _job(21, "validate", conclusion="success"),
                _job(
                    22,
                    "test",
                    conclusion=None,
                    status="in_progress",
                    steps=[
                        ("Checkout", "success"),
                        ("Lancer le runner", "in_progress"),
                        ("Cleanup", "queued"),
                    ],
                ),
            )
        },
    )

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    assert panel.tree.item("run-11")["open"] is True
    # Completed job of the active run stays collapsed; live job is opened.
    assert panel.tree.item("job-11-21")["open"] is False
    assert panel.tree.item("job-11-21")["values"] == ["réussi"]
    live_job = panel.tree.item("job-11-22")
    assert live_job["open"] is True
    assert live_job["values"] == ["en cours — étape : Lancer le runner"]

    step_iids = panel.tree.get_children("job-11-22")
    step_texts = [panel.tree.item(iid)["text"] for iid in step_iids]
    assert step_texts[0].strip().startswith("+")
    assert "Checkout" in step_texts[0]
    assert step_texts[1].strip().startswith("*")
    assert "Lancer le runner" in step_texts[1]
    assert panel.tree.item(step_iids[1])["values"] == ["en cours"]
    assert step_texts[2].strip().startswith("-")
    assert panel.tree.item(step_iids[2])["values"] == ["en attente"]


def test_format_fetched_at_helpers() -> None:
    assert _format_fetched_at("2026-09-18T14:03:05+02:00") == "14:03:05"
    assert _format_fetched_at("garbage") == ""
    assert _format_fetched_at("") == ""


def test_apply_renders_partial_snapshot_without_dropping_runs() -> None:
    snapshot = _snapshot(
        runs=[_run(11)],
        jobs={11: (_job(21),)},
        warnings=["Les logs de certains jobs sont indisponibles."],
        partial_errors=["Le job 21 n'a pas pu être détaillé."],
    )

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    assert panel.tree.get_children() == ["run-11"]
    assert "Les logs de certains jobs sont indisponibles." in panel._empty._options["text"]
    assert "Le job 21 n'a pas pu être détaillé." in panel._empty._options["text"]
    assert "Avertissement" in panel._summary._options["text"]


def test_apply_renders_pipeline_label_timestamp_and_failure_detail() -> None:
    run_created_at = "2026-09-18T14:03:05+02:00"
    job_completed_at = "2026-09-18T14:05:05+02:00"
    pipeline_timestamp = "2026-09-18T14:05:00+02:00"
    snapshot = _snapshot(
        runs=[_run(11, created_at=run_created_at)],
        jobs={
            11: (
                _job(
                    21,
                    conclusion="failure",
                    completed_at=job_completed_at,
                ),
            )
        },
        pipelines={
            21: (
                _pipeline(
                    status="failure",
                    detail="HTTP 500",
                    timestamp=pipeline_timestamp,
                ),
            )
        },
    )

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    run_row = panel.tree.item("run-11")
    assert _local_timestamp(run_created_at) in run_row["text"]
    job_row = panel.tree.item("job-11-21")
    assert _local_timestamp(job_completed_at) in job_row["values"][0]
    pipeline_id = panel.tree.get_children("job-11-21")[0]
    pipeline_row = panel.tree.item(pipeline_id)
    assert "en échec" in pipeline_row["text"]
    assert "HTTP 500" in pipeline_row["values"][0]
    assert _local_timestamp(pipeline_timestamp) in pipeline_row["values"][0]
    assert pipeline_row["tags"] == ["failure"]


def test_double_click_on_pipeline_opens_its_run() -> None:
    snapshot = _snapshot(runs=[_run(11)], jobs={11: (_job(21),)}, pipelines={21: (_pipeline(),)})

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, open_run = _build()
        panel.apply(snapshot)
        pipeline_id = panel.tree.get_children("job-11-21")[0]
        panel.tree.selection_set(pipeline_id)
        panel.tree._bindings["<Double-1>"](None)

    open_run.assert_called_once()
    assert open_run.call_args.args[0].id == 11


def test_apply_restores_selection_and_event_expansion() -> None:
    snapshot = _snapshot(runs=[_run(11)], jobs={11: (_job(21),)})

    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)
        panel.tree.selection_set("job-11-21")
        panel.tree._bindings["<<TreeviewOpen>>"](None)
        assert "job-11-21" in panel.expanded

        panel.apply(snapshot)
        assert panel.tree.selection() == ["job-11-21"]
        assert panel.tree.item("job-11-21")["open"] is True

        panel.tree._bindings["<<TreeviewClose>>"](None)
        assert "job-11-21" not in panel.expanded
