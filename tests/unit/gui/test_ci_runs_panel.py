"""Tests for the read-only GitHub Actions runs panel (gui/ci_runs.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from n8n_launcher.gui.ci_runs import RunsPanel, runs_summary_text
from n8n_launcher.workspaces import ci_runs

from helpers import FakeTk, FakeTtk, fake_runs_panel_bases  # noqa: E402


def _run(run_id: int = 11, *, conclusion: str | None = "success", status: str = "completed"):
    return ci_runs.RunSummary(
        id=run_id,
        run_number=run_id,
        branch="main",
        head_sha="a" * 40,
        status=status,
        conclusion=conclusion,
        created_at="2026-01-01T00:00:00Z",
        url=f"https://github.com/octo/repo/actions/runs/{run_id}",
    )


def _job(job_id: int = 21, name: str = "validate", *, conclusion: str | None = "success"):
    return ci_runs.JobSummary(
        id=job_id,
        name=name,
        status="completed",
        conclusion=conclusion,
        url=f"https://github.com/octo/repo/actions/jobs/{job_id}",
    )


def _pipeline(rel: str = "n8nPipelines/a.json", status: str = "success", detail: str = ""):
    mark = {"success": "\u2714", "failure": "\u2718", "waiting": "\u25fb"}[status]
    return ci_runs.PipelineResult(rel=rel, status=status, detail=detail, mark=mark)


def _snapshot(*, runs=(), jobs=None, pipelines=None, error=None):
    return ci_runs.RunsSnapshot(
        repo_path="octo/repo",
        runs=tuple(runs),
        jobs=dict(jobs or {}),
        pipelines=dict(pipelines or {}),
        error=error,
    )


def _build(refresh=None, open_run=None, run=None):
    """Create a panel wired to recording callbacks and return both."""
    refresh = refresh or MagicMock()
    open_run = open_run or MagicMock()
    panel = RunsPanel(
        FakeTk.Frame(None), refresh=refresh, open_run=open_run, run=run
    )
    return panel, refresh, open_run


def test_apply_renders_runs_jobs_and_pipelines() -> None:
    snapshot = _snapshot(
        runs=[_run(11)],
        jobs={11: (_job(21),)},
        pipelines={21: (_pipeline(detail="3 nœuds"),)},
    )

    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    run_row = panel.tree.item("run-11")
    assert run_row["text"].startswith("\u2714")
    assert "#11" in run_row["text"]
    assert "main" in run_row["text"]
    assert run_row["values"] == ["success"]
    assert run_row["tags"] == ["success"]

    job_row = panel.tree.item("job-11-21")
    assert job_row["text"].strip().startswith("\u2714")
    assert "validate" in job_row["text"]
    assert job_row["values"] == ["réussi"]

    pipeline_id = panel.tree.get_children("job-11-21")[0]
    pipeline_row = panel.tree.item(pipeline_id)
    assert pipeline_row["text"].strip().startswith("\u2714")
    assert "n8nPipelines/a.json" in pipeline_row["text"]
    assert pipeline_row["values"] == ["3 nœuds"]


def test_apply_marks_failed_run_and_waiting_pipeline() -> None:
    snapshot = _snapshot(
        runs=[_run(11, conclusion="failure")],
        jobs={11: (_job(21, conclusion="failure"),)},
        pipelines={21: (_pipeline(status="waiting"),)},
    )

    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()
        panel.apply(snapshot)

    assert panel.tree.item("run-11")["text"].startswith("\u2718")
    assert panel.tree.item("run-11")["tags"] == ["failure"]
    pipeline_id = panel.tree.get_children("job-11-21")[0]
    assert panel.tree.item(pipeline_id)["tags"] == ["muted"]


def test_apply_empty_snapshot_shows_placeholder() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot())

    assert panel.tree.get_children() == []
    assert panel._empty._options["text"] == "Aucun run GitHub Actions pour ce workspace."
    assert panel._summary._options["text"] == "Aucun run GitHub Actions pour ce workspace."


def test_apply_error_snapshot_shows_message() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot(error="HTTP 401"))

    assert "GitHub Actions indisponible : HTTP 401" in panel._empty._options["text"]
    assert not panel.tree.get_children()


def test_apply_preserves_expansion_state() -> None:
    snapshot = _snapshot(runs=[_run(11)], jobs={11: (_job(21),)})

    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
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
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()
        panel.apply(_snapshot(runs=[_run(11), _run(12)]))
        assert set(panel.tree.get_children()) == {"run-11", "run-12"}

        panel.apply(_snapshot(runs=[_run(13)]))
        assert panel.tree.get_children() == ["run-13"]


def test_refresh_forwards_to_host_callback() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, refresh, _open = _build()
        panel.refresh()

    refresh.assert_called_once_with()


def test_run_button_absent_without_host_callback() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build()

    assert not hasattr(panel, "_run_btn")


def test_run_button_forwards_to_host_callback() -> None:
    run = MagicMock()
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, _open = _build(run=run)
        assert panel._run_btn.text == "Lancer la CI"
        panel._run_btn.command()
        panel.run()

    assert run.call_count == 2


def test_double_click_opens_selected_run() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, open_run = _build()
        panel.apply(_snapshot(runs=[_run(11)]))
        panel.tree.selection_set("run-11")
        panel.tree._bindings["<Double-1>"](None)

    open_run.assert_called_once()
    assert open_run.call_args.args[0].id == 11


def test_open_without_selection_does_nothing() -> None:
    with fake_runs_panel_bases(), patch("n8n_launcher.gui.ci_runs.tk", FakeTk()), patch(
        "n8n_launcher.gui.ci_runs.ttk", FakeTtk()
    ):
        panel, _refresh, open_run = _build()
        panel.apply(_snapshot(runs=[_run(11)]))
        panel._open_selected()

    open_run.assert_not_called()


def test_runs_summary_text_variants() -> None:
    assert (
        runs_summary_text(_snapshot(error="boom"))
        == "GitHub Actions indisponible : boom"
    )
    assert (
        runs_summary_text(_snapshot())
        == "Aucun run GitHub Actions pour ce workspace."
    )
    summary = runs_summary_text(
        _snapshot(
            runs=[
                _run(11, conclusion="success"),
                _run(12, conclusion=None, status="in_progress"),
            ]
        )
    )
    assert summary == "2 run(s) · 1 terminé(s)"
