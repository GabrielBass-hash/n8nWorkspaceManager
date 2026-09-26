"""Tests for the CI dialogs (gui/ci_edit.py)."""

from __future__ import annotations

import time
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

from helpers import (
    FakeRoot,
    FakeTk,
    FakeTtk,
    fake_runs_panel_bases,
    make_workspace,
)

from n8n_launcher.github.api import GitHubError
from n8n_launcher.gui.app import CI_RUNS_TTL_SECONDS
from n8n_launcher.gui.ci_edit import (
    _finish_dialog_setup,
    prompt_ci_credentials,
    prompt_run_ci,
)
from n8n_launcher.gui.ci_runs import RunsPanel
from n8n_launcher.gui.dialogs import GitHubTokenPlan
from n8n_launcher.workspaces import ci_runs


def ci_mocks(workspace, credentials=None):
    """Build a fake manager whose n8n reports the given credentials."""
    launcher = MagicMock()
    api = MagicMock()
    api.list_credentials.return_value = credentials or []
    launcher.api_factory.return_value = api
    workspace.api_key = "key"
    return launcher, api


def test_finish_dialog_setup_registers_fonts_on_root() -> None:
    dialog = FakeTk.Toplevel(None)
    root = FakeRoot()

    with patch("n8n_launcher.gui.ci_edit.configure_fonts") as fonts:
        _finish_dialog_setup(dialog, root)

    fonts.assert_called_once_with(root)


class _ReturnTk(FakeTk):
    """Same fakes, but Toplevel.wait_window submits with <Return> when armed."""

    class Toplevel(FakeTk.Toplevel):
        press_return = False

        def wait_window(self) -> None:
            handler = self._bindings.get("<Return>")
            if handler is not None and self.press_return:
                handler(None)
                return
            super().wait_window()


@contextmanager
def _patch_ci_editor(tk_fake):
    """Patch ci_edit's tk/ttk/messagebox and yield them, then restore."""

    stack = ExitStack()
    tk_patch = stack.enter_context(patch("n8n_launcher.gui.ci_edit.tk", tk_fake))
    ttk_patch = stack.enter_context(patch("n8n_launcher.gui.ci_edit.ttk", FakeTtk()))
    messagebox = stack.enter_context(
        patch(
            "n8n_launcher.gui.ci_edit.messagebox",
            MagicMock(
                askyesno=MagicMock(return_value=False),
                showwarning=MagicMock(),
                showerror=MagicMock(),
            ),
        )
    )
    with stack:
        yield tk_patch, ttk_patch, messagebox


def flat_children(widget):
    """Yield a widget's children, recursing into container frames."""
    for child in widget.children:
        yield child
        if hasattr(child, "children"):
            yield from flat_children(child)


_MANUAL = (
    '{"name": "M", "nodes": [{"name": "Bouton",'
    ' "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}],'
    ' "connections": {}, "settings": {}}'
)
_HOOK = (
    '{"name": "H", "nodes": [{"name": "Webhook",'
    ' "type": "n8n-nodes-base.webhookTrigger", "typeVersion": 1}],'
    ' "connections": {}, "settings": {}}'
)


def test_prompt_ci_credentials_warns_when_never_started(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(workspace)
    workspace.api_key = None

    with _patch_ci_editor(FakeTk()) as (_, _, messagebox):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    assert "Démarrez ce workspace" in messagebox.showwarning.call_args.args[1]
    launcher.api_factory.assert_not_called()


def test_prompt_ci_credentials_copies_json_then_records_metadata(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "DB", "type": "postgres"},
        ],
    )
    launcher.ci_credentials_payload.return_value = '{"ok": true}'

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    children = list(flat_children(dialog))
    buttons = [child for child in children if isinstance(child, FakeTtk.Button)]
    copy = next(button for button in buttons if "Copier le JSON" in (button.text or ""))
    copy.command()

    assert dialog._clipboard == '{"ok": true}'
    status = next(
        child
        for child in children
        if isinstance(child, tk_fake.Label) and "JSON copié" in child._options.get("text", "")
    )
    assert "N8N_CI_CREDENTIALS" in status._options["text"]
    launcher.set_ci_credentials.assert_not_called()

    pasted = next(button for button in buttons if "J'ai collé" in (button.text or ""))
    pasted.command()
    launcher.set_ci_credentials.assert_called_once_with(
        workspace, [{"name": "API", "type": "httpRequest"}, {"name": "DB", "type": "postgres"}]
    )


def test_prompt_ci_credentials_skips_empty_names(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[{"id": "c1", "name": "  ", "type": "httpRequest"}],
    )

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    checkboxes = [
        child for child in flat_children(dialog) if isinstance(child, tk_fake.Checkbutton)
    ]
    assert checkboxes == []


def test_prompt_ci_credentials_excludes_unticked_from_payload(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "DB", "type": "postgres"},
        ],
    )
    launcher.ci_credentials_payload.return_value = "[]"

    with _patch_ci_editor(FakeTk()) as (tk_fake, _, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    checkboxes = [
        child for child in flat_children(dialog) if isinstance(child, tk_fake.Checkbutton)
    ]
    assert len(checkboxes) == 2
    checkboxes[0].deselect()  # user unticks "API"
    copy = next(
        button
        for button in flat_children(dialog)
        if isinstance(button, FakeTtk.Button) and "Copier le JSON" in (button.text or "")
    )
    copy.command()

    launcher.ci_credentials_payload.assert_called_once_with(
        workspace, [{"name": "DB", "type": "postgres"}]
    )
    assert dialog._clipboard == "[]"


# --- Runs tab (Notebook) and host wiring -------------------------------------


def _runs_snapshot(*, error=None) -> ci_runs.RunsSnapshot:
    run = ci_runs.RunSummary(
        id=11,
        run_number=11,
        branch="main",
        head_sha="a" * 40,
        status="completed",
        conclusion="success",
        created_at="2026-01-01T00:00:00Z",
        url="https://github.com/octo/repo/actions/runs/11",
    )
    job = ci_runs.JobSummary(
        id=21,
        name="validate",
        status="completed",
        conclusion="success",
        url="https://github.com/octo/repo/actions/jobs/21",
    )
    pipeline = ci_runs.PipelineResult(
        rel="n8nPipelines/a.json", status="success", detail="", mark="\u2714"
    )
    return ci_runs.RunsSnapshot(
        repo_path="octo/repo",
        runs=(run,),
        jobs={11: (job,)},
        pipelines={21: (pipeline,)},
        error=error,
    )


def _workspace_with_manual(tmp_path):
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root
    return workspace


def _active_run_snapshot() -> ci_runs.RunsSnapshot:
    run = ci_runs.RunSummary(
        id=12,
        run_number=12,
        branch="main",
        head_sha="b" * 40,
        status="in_progress",
        conclusion=None,
        created_at="2026-01-01T00:00:00Z",
        url="https://github.com/octo/repo/actions/runs/12",
    )
    return ci_runs.RunsSnapshot(repo_path="octo/repo", runs=(run,))


def _panel_for_poll(snapshot):
    dialog = FakeTk.Toplevel(None)
    with (
        fake_runs_panel_bases(),
        patch("n8n_launcher.gui.ci_runs.tk", FakeTk()),
        patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()),
    ):
        panel = RunsPanel(dialog, refresh=lambda: None, open_run=lambda _r: None)
        panel.apply(snapshot)
    return dialog, panel


def _drive_run_dialog(ref: str | None):
    """Return a ``wait_window`` replacement typing ``ref`` then submitting.

    ``None`` simulates the user cancelling with Escape.
    """

    def drive(dialog) -> None:
        if ref is None:
            dialog._bindings["<Escape>"](None)
            return
        entry = next(c for c in dialog.children if isinstance(c, FakeTk.Entry))
        entry._options["textvariable"].set(ref)
        entry._bindings["<Return>"](None)

    return drive


def test_prompt_run_ci_submits_prefilled_ref() -> None:
    with _patch_ci_editor(FakeTk()):
        ref = prompt_run_ci(FakeRoot(), ".github/workflows/n8n-ci.yml", "main")

    assert ref == "main"


def test_prompt_run_ci_returns_trimmed_typed_ref() -> None:
    with (
        _patch_ci_editor(FakeTk()),
        patch.object(FakeTk.Toplevel, "wait_window", _drive_run_dialog("  release  ")),
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref == "release"


def test_prompt_run_ci_returns_none_on_cancel() -> None:
    with (
        _patch_ci_editor(FakeTk()),
        patch.object(FakeTk.Toplevel, "wait_window", _drive_run_dialog(None)),
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref is None


def test_prompt_run_ci_returns_none_on_blank_ref() -> None:
    with (
        _patch_ci_editor(FakeTk()),
        patch.object(FakeTk.Toplevel, "wait_window", _drive_run_dialog("   ")),
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref is None


def test_ci_cached_snapshot_returns_the_cached_one(app) -> None:
    cached_workspace = app.manager.list.return_value[1]
    other_workspace = app.manager.list.return_value[0]
    cached = _runs_snapshot()
    app.app._ci_runs_cache[cached_workspace.id] = cached

    assert app.app._ci_cached_snapshot(cached_workspace) is cached
    assert app.app._ci_cached_snapshot(other_workspace) == ci_runs.RunsSnapshot("")


def test_build_ci_runs_snapshot_composes_payloads(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        {
            "id": 11,
            "run_number": 11,
            "head_branch": "main",
            "head_sha": "a" * 40,
            "status": "completed",
            "conclusion": "success",
        }
    ]
    client.list_run_jobs.return_value = [
        {"id": 21, "name": "validate", "status": "completed", "conclusion": "success"}
    ]
    client.fetch_job_logs.return_value = "[runner] n8nPipelines/a.json : success\n"

    with patch("n8n_launcher.gui.app.GitHubClient", return_value=client) as client_cls:
        snapshot = app.app._build_ci_runs_snapshot(workspace)

    client_cls.assert_called_once_with("ghp_x", session=app.app._github_session)
    assert snapshot.repo_path == "octo/repo"
    assert snapshot.runs[0].id == 11
    assert snapshot.jobs[11][0].name == "validate"
    assert snapshot.pipelines[21][0].rel == "n8nPipelines/a.json"


def test_build_ci_runs_snapshot_without_remote_reports_a_note(app) -> None:
    # A workspace with no GitHub remote is a configuration, not a failure: the
    # panel shows the note and the page stops polling.
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = None

    snapshot = app.app._build_ci_runs_snapshot(workspace)

    assert snapshot.error is None
    assert snapshot.note is not None
    assert "dépôt GitHub" in snapshot.note


def test_ci_runs_available_follows_the_github_remote(app) -> None:
    workspace = app.manager.list.return_value[1]

    app.manager.git_remote_url.return_value = None
    assert app.app._ci_runs_available(workspace) is False

    app.manager.git_remote_url.return_value = "https://gitlab.com/octo/repo.git"
    assert app.app._ci_runs_available(workspace) is False

    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    assert app.app._ci_runs_available(workspace) is True


def test_ci_runs_available_is_false_when_the_remote_cannot_be_read(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.side_effect = OSError("git is missing")

    assert app.app._ci_runs_available(workspace) is False


def test_build_ci_runs_snapshot_without_token_reports_error(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = None

    snapshot = app.app._build_ci_runs_snapshot(workspace)

    assert snapshot.error == "Token GitHub manquant."


def test_refresh_ci_runs_prompts_token_then_applies_on_main_thread(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.manager.github_token.return_value = None
    app.app._ci_token = None
    client = MagicMock()
    client.list_workflow_runs.return_value = []
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value=None),
        patch(
            "n8n_launcher.gui.app.prompt_github_token",
            return_value=GitHubTokenPlan(token="ghp_new"),
        ) as prompt,
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
    ):
        app.app._refresh_ci_runs(workspace, panel)

    prompt.assert_called_once()
    assert app.app._ci_token == "ghp_new"
    panel.apply.assert_not_called()  # still queued on the event loop
    app.app._drain_events()
    panel.apply.assert_called_once()
    assert panel.apply.call_args.args[0].repo_path == "octo/repo"


def test_refresh_ci_runs_reuses_cached_token(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_cached"
    client = MagicMock()
    client.list_workflow_runs.return_value = []
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.prompt_github_token") as prompt,
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
    ):
        app.app._refresh_ci_runs(workspace, panel)

    prompt.assert_not_called()
    app.app._drain_events()
    panel.apply.assert_called_once()


def test_refresh_ci_runs_skips_when_token_cancelled(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.github_token.return_value = None
    app.app._ci_token = None
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.auth.resolve_github_token", return_value=None),
        patch("n8n_launcher.gui.app.prompt_github_token", return_value=None),
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._refresh_ci_runs(workspace, panel)
    app.app._drain_events()

    client.assert_not_called()
    panel.apply.assert_not_called()


def test_ci_open_run_opens_the_url(app) -> None:
    run = _runs_snapshot().runs[0]

    with patch("n8n_launcher.gui.app.open_url") as opener:
        app.app._ci_open_run(run)

    opener.assert_called_once_with(run.url)


def test_ci_open_run_without_url_is_a_noop(app) -> None:
    run = ci_runs.RunSummary(
        id=1,
        run_number=1,
        branch="main",
        head_sha="a" * 40,
        status="queued",
        conclusion=None,
        created_at=None,
        url=None,
    )

    with patch("n8n_launcher.gui.app.open_url") as opener:
        app.app._ci_open_run(run)

    opener.assert_not_called()


def test_ci_latest_branch_prefers_cached_run(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.app._ci_runs_cache[workspace.id] = _runs_snapshot()

    assert app.app._ci_latest_branch(workspace) == "main"


def test_ci_latest_branch_falls_back_to_dev(app) -> None:
    workspace = app.manager.list.return_value[1]

    assert app.app._ci_latest_branch(workspace) == "dev"


def test_ci_runs_run_dispatches_chosen_ref_and_refreshes(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    panel = MagicMock()

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value="release") as prompt,
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
        patch("n8n_launcher.gui.app.ci.WORKFLOW_FILE", "wf.yml"),
        patch.object(app.app, "_refresh_ci_runs") as refresh,
        patch.object(app.app, "set_status") as status,
    ):
        app.app._ci_runs_run(workspace, panel)
        app.app._drain_events()

    prompt.assert_called_once_with(app.app.root, "wf.yml", "dev")
    client.dispatch_workflow.assert_called_once_with("octo/repo", "wf.yml", ref="release")
    status.assert_called_once_with("CI lancée sur « release ».")
    refresh.assert_called_once_with(workspace, panel, force=True)


def test_ci_runs_run_cancelled_ref_is_noop(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value=None),
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._ci_runs_run(workspace, MagicMock())

    client.assert_not_called()


def test_ci_runs_run_without_remote_warns(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = None

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci") as prompt,
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._ci_runs_run(workspace, MagicMock())

    prompt.assert_not_called()
    client.assert_not_called()
    assert app.mocks.messagebox.warnings


def test_ci_runs_run_reports_dispatch_error(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    client.dispatch_workflow.side_effect = GitHubError("boom")

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value="main"),
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
    ):
        app.app._ci_runs_run(workspace, MagicMock())
        app.app._drain_events()

    message = app.mocks.messagebox.errors[0]
    assert "boom" in message


def test_ci_runs_run_blocks_when_cache_has_active_run(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    app.app._ci_runs_cache[workspace.id] = ci_runs.RunsSnapshot(
        repo_path="octo/repo",
        runs=(
            ci_runs.RunSummary(
                id=13,
                run_number=13,
                branch="main",
                head_sha="a" * 40,
                status="in_progress",
                conclusion=None,
                created_at="2026-01-01T00:00:00Z",
                url="https://github.com/octo/repo/actions/runs/13",
            ),
        ),
    )

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci") as prompt,
        patch("n8n_launcher.gui.app.GitHubClient") as client,
    ):
        app.app._ci_runs_run(workspace, MagicMock())

    prompt.assert_not_called()
    client.assert_not_called()
    assert app.mocks.messagebox.warnings
    assert "déjà en cours" in app.mocks.messagebox.warnings[0]


def test_ci_runs_run_allows_dispatch_when_cache_has_only_finished_runs(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    app.app._ci_runs_cache[workspace.id] = _runs_snapshot()
    client = MagicMock()

    with (
        patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value="main"),
        patch("n8n_launcher.gui.app.GitHubClient", return_value=client),
    ):
        app.app._ci_runs_run(workspace, MagicMock())
        app.app._drain_events()

    client.dispatch_workflow.assert_called_once()
    assert not app.mocks.messagebox.warnings


def test_build_ci_runs_snapshot_records_fetch_time(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    client.list_workflow_runs.return_value = []

    with patch("n8n_launcher.gui.app.GitHubClient", return_value=client):
        snapshot = app.app._build_ci_runs_snapshot(workspace)

    assert snapshot.fetched_at is not None
    # A fresh snapshot never loses its fetch time to the panel.
    assert snapshot.fetched_at[:4].isdigit()


def _run_dict(run_id: int, status: str) -> dict:
    return {
        "id": run_id,
        "run_number": run_id,
        "head_branch": "main",
        "head_sha": "a" * 40,
        "status": status,
        "conclusion": None if status != "completed" else "success",
    }


def test_build_ci_runs_snapshot_details_only_the_active_run(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    # Old finished runs plus one in flight: only the in-flight run is watched.
    client.list_workflow_runs.return_value = [
        _run_dict(11, "completed"),
        _run_dict(12, "completed"),
        _run_dict(13, "in_progress"),
    ]
    client.list_run_jobs.return_value = [
        {"id": 21, "name": "validate", "status": "completed", "conclusion": "success"},
        {"id": 22, "name": "test", "status": "in_progress", "conclusion": None},
    ]
    client.fetch_job_logs.side_effect = lambda _repo, job_id: {
        21: "[runner] n8nPipelines/a.json : success\n",
        22: "[runner] n8nPipelines/b.json : failure\n",
    }[job_id]

    with patch("n8n_launcher.gui.app.GitHubClient", return_value=client):
        snapshot = app.app._build_ci_runs_snapshot(workspace)

    # Jobs + logs fetched for the active run only; the finished runs stay rows.
    # The log downloads run in parallel, so the recorded order is arbitrary.
    client.list_run_jobs.assert_called_once_with("octo/repo", 13)
    assert {tuple(call.args) for call in client.fetch_job_logs.call_args_list} == {
        ("octo/repo", 21),
        ("octo/repo", 22),
    }
    # Both the finished job and the in-flight job's logs are parsed.
    assert snapshot.pipelines[21][0].rel == "n8nPipelines/a.json"
    assert snapshot.pipelines[22][0].rel == "n8nPipelines/b.json"
    assert snapshot.jobs[13][1].status == "in_progress"


def test_refresh_ci_runs_is_noop_while_a_fetch_is_in_flight(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    app.app._ci_runs_fetch_in_flight = True
    panel = MagicMock()

    with patch("n8n_launcher.gui.app.GitHubClient") as client:
        app.app._refresh_ci_runs(workspace, panel)

    client.assert_not_called()
    assert app.app._ci_runs_fetch_in_flight is True
    app.app._drain_events()
    panel.apply.assert_not_called()


def test_refresh_ci_runs_serves_fresh_cache_without_network(app) -> None:
    workspace = app.manager.list.return_value[1]
    panel = MagicMock()
    snapshot = ci_runs.RunsSnapshot("octo/repo")
    app.app._ci_runs_cache[workspace.id] = snapshot
    app.app._ci_runs_fetched_at[workspace.id] = time.monotonic()

    with patch("n8n_launcher.gui.app.GitHubClient") as client_cls:
        app.app._refresh_ci_runs(workspace, panel)
        # The cache hit is applied immediately, synchronously.
        panel.apply.assert_called_once_with(snapshot)
        app.app._ci_token_declined = True  # the UI prompt never fired

    client_cls.assert_not_called()


def test_refresh_ci_runs_refetches_when_cache_is_stale(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    app.app._ci_runs_cache[workspace.id] = ci_runs.RunsSnapshot("octo/repo")
    app.app._ci_runs_fetched_at[workspace.id] = time.monotonic() - CI_RUNS_TTL_SECONDS - 1.0
    client = MagicMock()
    client.list_workflow_runs.return_value = []
    panel = MagicMock()

    with patch("n8n_launcher.gui.app.GitHubClient", return_value=client) as client_cls:
        app.app._refresh_ci_runs(workspace, panel)

    client_cls.assert_called_with("ghp_x", session=app.app._github_session)
    app.app._drain_events()
    panel.apply.assert_called_once()
    # The freshness window starts over.
    assert app.app._ci_runs_fetched_at[workspace.id] > time.monotonic() - 1.0


def test_refresh_ci_runs_force_bypasses_freshness_window(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    app.app._ci_runs_cache[workspace.id] = ci_runs.RunsSnapshot("octo/repo")
    app.app._ci_runs_fetched_at[workspace.id] = time.monotonic()
    client = MagicMock()
    client.list_workflow_runs.return_value = []
    panel = MagicMock()

    with patch("n8n_launcher.gui.app.GitHubClient", return_value=client):
        app.app._refresh_ci_runs(workspace, panel, force=True)

    # A just-dispatched run must surface now, not after the TTL.
    client.list_workflow_runs.assert_called_once_with("octo/repo")
    app.app._drain_events()
    panel.apply.assert_called_once()
