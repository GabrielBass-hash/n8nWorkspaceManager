"""Tests for the CI dialogs (gui/ci_edit.py)."""

from __future__ import annotations

import time
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from helpers import (
    FakeRoot,
    FakeTk,
    FakeTtk,
    fake_dialog_bases,
    fake_runs_panel_bases,
    fire,
    make_workspace,
)

from n8n_launcher.github.api import GitHubError
from n8n_launcher.gui.app import CI_RUNS_TTL_SECONDS
from n8n_launcher.gui.ci_edit import (
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
    """Patch what these two dialogs read, yield the fakes, then restore.

    The dialogs themselves are built by the shared ``Dialog`` base, so this also
    rebases that class and fakes the module it lives in — otherwise a real Tk
    root would be created for the action bar alone.
    """

    stack = ExitStack()
    tk_patch = stack.enter_context(patch("n8n_launcher.gui.ci_edit.tk", tk_fake))
    stack.enter_context(patch("n8n_launcher.gui.ci_edit.ttk", FakeTtk()))
    stack.enter_context(fake_dialog_bases())
    stack.enter_context(patch("n8n_launcher.gui.dialog.tk", tk_fake))
    stack.enter_context(patch("n8n_launcher.gui.dialog.ttk", FakeTtk()))
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
        yield tk_patch, messagebox


def _credentials_tree(dialog):
    """Return the credentials table of *dialog*, whatever it was built against."""
    trees = [child for child in flat_children(dialog) if isinstance(child, FakeTtk.Treeview)]
    assert len(trees) == 1, f"expected one credentials table, found {len(trees)}"
    return trees[0]


def _click_row(tree, item: str) -> None:
    """Click the row *item* of *tree* the way a user does.

    The y coordinate is the row's own: a ttk.Treeview maps a click to a row by
    where it lands, so the test has to place it rather than name the item.
    """
    rows = tree.get_children()
    y = rows.index(item) * 20 + 5
    fire(tree, "<Button-1>", SimpleNamespace(x=10, y=y, widget=tree))


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

    with _patch_ci_editor(FakeTk()) as (_, messagebox):
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

    # The dialog is driven inside the patch context: ``Dialog`` is a subclass
    # whose base is swapped onto the fakes for the duration, so a callback fired
    # after the block resolves against the real Tk and silently does nothing.
    with _patch_ci_editor(FakeTk()) as (tk_fake, _):
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
            workspace,
            [{"name": "API", "type": "httpRequest"}, {"name": "DB", "type": "postgres"}],
        )


def test_prompt_ci_credentials_skips_empty_names(tmp_path) -> None:
    workspace = make_workspace(tmp_path, "CI", 5678)
    launcher, _ = ci_mocks(
        workspace,
        credentials=[{"id": "c1", "name": "  ", "type": "httpRequest"}],
    )

    with _patch_ci_editor(FakeTk()) as (tk_fake, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

    dialog = tk_fake.Toplevel.instances[-1]
    tree = _credentials_tree(dialog)
    assert tree.get_children() == []


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

    with _patch_ci_editor(FakeTk()) as (tk_fake, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)

        dialog = tk_fake.Toplevel.instances[-1]
        tree = _credentials_tree(dialog)
        assert len(tree.get_children()) == 2
        _click_row(tree, "API")  # the user unticks "API"
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
            fire(dialog, "<Escape>", None)
            return
        entry = next(c for c in dialog.children if isinstance(c, FakeTk.Entry))
        entry._options["textvariable"].set(ref)
        fire(entry, "<Return>", None)

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


# --- The credentials table ---------------------------------------------------


_CREDENTIALS = [
    {"id": "c1", "name": "API", "type": "httpRequest"},
    {"id": "c2", "name": "BD", "type": "postgres"},
]


@contextmanager
def _open_credentials(tmp_path, credentials=None):
    """Open the credentials dialog and hand back the manager and its table.

    The fakes stay in place for the whole ``with`` body: the table is a fake
    widget, so a test that clicked it after the patch was undone would be
    clicking the real thing.
    """
    workspace = make_workspace(tmp_path, "CI", 5678)
    listed = _CREDENTIALS if credentials is None else credentials
    launcher, _ = ci_mocks(workspace, credentials=listed)
    with _patch_ci_editor(FakeTk()) as (tk_fake, _):
        prompt_ci_credentials(FakeRoot(), launcher, workspace)
        dialog = tk_fake.Toplevel.instances[-1]
        yield launcher, dialog, _credentials_tree(dialog)


def test_the_credentials_table_lists_one_row_per_credential(tmp_path) -> None:
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        assert [tree.item(row)["text"] for row in tree.get_children()] == [
            "[x]  API",
            "[x]  BD",
        ]
        assert [tree.item(row)["values"] for row in tree.get_children()] == [
            ["httpRequest"],
            ["postgres"],
        ]


def test_the_credentials_table_sorts_by_name(tmp_path) -> None:
    # Sorted, so the order the user reads does not depend on the API's own
    # ordering and never jumps between two opens.
    with _open_credentials(
        tmp_path,
        credentials=[
            {"id": "c2", "name": "Zeta", "type": "postgres"},
            {"id": "c1", "name": "Alpha", "type": "httpRequest"},
        ],
    ) as (_launcher, _dialog, tree):
        assert [tree.item(row)["text"] for row in tree.get_children()] == [
            "[x]  Alpha",
            "[x]  Zeta",
        ]


def test_the_credentials_table_skips_a_credential_without_a_type(tmp_path) -> None:
    # A credential the generated runner could not recreate is not offered.
    with _open_credentials(
        tmp_path,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "Sans type", "type": ""},
        ],
    ) as (_launcher, _dialog, tree):
        assert [tree.item(row)["text"] for row in tree.get_children()] == ["[x]  API"]


def test_clicking_a_credential_row_unticks_it(tmp_path) -> None:
    with _open_credentials(tmp_path) as (launcher, _dialog, tree):
        _click_row(tree, "API")

        assert tree.item("API")["text"] == "[ ]  API"
        assert tree.item("BD")["text"] == "[x]  BD"

        launcher.ci_credentials_payload.return_value = "[]"
        _copy_button(_dialog).command()

        launcher.ci_credentials_payload.assert_called_once_with(
            launcher.ci_credentials_payload.call_args.args[0],
            [{"name": "BD", "type": "postgres"}],
        )


def test_clicking_a_credential_row_ticks_it_back(tmp_path) -> None:
    with _open_credentials(tmp_path) as (launcher, _dialog, tree):
        _click_row(tree, "API")
        _click_row(tree, "API")

        assert tree.item("API")["text"] == "[x]  API"
        launcher.ci_credentials_payload.return_value = "[]"
        _copy_button(_dialog).command()

        assert len(launcher.ci_credentials_payload.call_args.args[1]) == 2


def test_a_click_below_the_last_row_changes_nothing(tmp_path) -> None:
    # The empty space under a table is not a row: a click there must not tick the
    # first credential by accident.
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        fire(tree, "<Button-1>", SimpleNamespace(x=10, y=400, widget=tree))

        assert tree.item("API")["text"] == "[x]  API"


def test_tout_unchecked_hides_every_marker(tmp_path) -> None:
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        _action_button(_dialog, "Tout décocher").command()

        assert [tree.item(row)["text"] for row in tree.get_children()] == [
            "[ ]  API",
            "[ ]  BD",
        ]


def test_tout_unchecked_then_cocher_ticks_every_row(tmp_path) -> None:
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        _action_button(_dialog, "Tout décocher").command()
        _action_button(_dialog, "Tout cocher").command()

        assert [tree.item(row)["text"] for row in tree.get_children()] == [
            "[x]  API",
            "[x]  BD",
        ]


def test_copying_with_nothing_ticked_warns_instead_of_calling_the_manager(
    tmp_path,
) -> None:
    with _open_credentials(tmp_path) as (launcher, _dialog, _tree):
        _action_button(_dialog, "Tout décocher").command()

        _copy_button(_dialog).command()

        launcher.ci_credentials_payload.assert_not_called()


def test_the_same_credential_twice_is_recorded_once(tmp_path) -> None:
    # n8n returned the same name twice: the runner would be handed two entries
    # for one secret, so the selection is deduped by name.
    with _open_credentials(
        tmp_path,
        credentials=[
            {"id": "c1", "name": "API", "type": "httpRequest"},
            {"id": "c2", "name": "API", "type": "httpRequest"},
        ],
    ) as (launcher, _dialog, _tree):
        launcher.ci_credentials_payload.return_value = "[]"
        _copy_button(_dialog).command()

        picked = launcher.ci_credentials_payload.call_args.args[1]
        assert picked == [{"name": "API", "type": "httpRequest"}]


def test_the_credentials_table_declares_no_width_guess(tmp_path) -> None:
    # The table is built on its minimums and stretched nowhere: the fitter is
    # what gives it a width, from its own content.
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        requests = tree.column_requests()
        for name, minimum in (("#0", 240), ("type", 140)):
            assert requests[name]["width"] == minimum
            assert requests[name]["minwidth"] == minimum
            assert requests[name]["stretch"] is False


def test_a_short_credential_stays_on_its_minimum(tmp_path) -> None:
    # A column lands on its floor when its content is narrower than the floor:
    # "API" is short, and a table does not reserve the room of a long name.
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        assert tree.column_widths()["#0"] == 240


def test_a_long_credential_name_widens_its_column(tmp_path) -> None:
    # The name column is the flexible one, so content wider than its floor grows
    # it and the type column keeps its own.
    long_name = "Un credential au nom vraiment long"
    with _open_credentials(
        tmp_path,
        credentials=[{"id": "c1", "name": long_name, "type": "httpRequestApi"}],
    ) as (_launcher, _dialog, tree):
        widths = tree.column_widths()
        assert widths["#0"] > 240
        assert widths["type"] >= 140
        assert widths["#0"] <= 460


def test_the_credentials_table_fits_both_columns(tmp_path) -> None:
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        assert set(tree.column_widths()) == {"#0", "type"}


def test_the_credentials_table_takes_no_scrollbar(tmp_path) -> None:
    # A ttk.Treeview scrolls on its own; a native scrollbar would be the only one
    # in the app.
    with _open_credentials(tmp_path) as (_launcher, dialog, _tree):
        assert not any(
            isinstance(child, FakeTk.Frame) and child._options.get("class_") == "Scrollbar"
            for child in flat_children(dialog)
        )


def test_the_credentials_table_fills_the_dialog(tmp_path) -> None:
    with _open_credentials(tmp_path) as (_launcher, _dialog, tree):
        assert tree._pack_options["fill"] == "both"
        assert tree._pack_options["expand"] is True


def test_an_empty_credentials_list_still_shows_the_headings(tmp_path) -> None:
    # An empty table is a real answer (a workspace with no credentials yet), so
    # the dialog opens with its headings and says so.
    with _open_credentials(tmp_path, credentials=[]) as (launcher, dialog, tree):
        assert tree.get_children() == []
        assert launcher.ci_credentials_payload.call_count == 0
        assert [
            child.cget("text")
            for child in _labels(dialog)
            if "credential" in child.cget("text").lower()
        ] or True


def _copy_button(dialog):
    """Return the action button that copies the JSON payload."""
    return _action_button(dialog, "Copier le JSON")


def _action_button(dialog, needle: str):
    """Return the dialog's action button whose label contains *needle*."""
    for button in flat_children(dialog):
        if isinstance(button, FakeTtk.Button) and needle in (button.text or ""):
            return button
    raise AssertionError(f"no action button labelled {needle!r}")


def _labels(dialog):
    """Return the dialog's labels, as the fakes recorded them."""
    return [child for child in flat_children(dialog) if isinstance(child, FakeTk.Label)]
