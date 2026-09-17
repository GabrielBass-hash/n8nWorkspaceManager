"""Tests for the CI dialogs (gui/ci_edit.py)."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from n8n_launcher.github.api import GitHubError
from n8n_launcher.gui.ci_edit import (
    prompt_ci_credentials,
    prompt_ci_workflows,
    prompt_run_ci,
)
from n8n_launcher.gui.ci_runs import RunsPanel
from n8n_launcher.gui.dialogs import GitHubTokenPlan
from n8n_launcher.workspaces import ci_runs

from helpers import FakeRoot, FakeTk, FakeTtk, fake_runs_panel_bases, make_workspace  # noqa: E402


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


def test_prompt_ci_workflows_escape_returns_none(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    with _patch_ci_editor(FakeTk()) as (_, _, _):
        result = prompt_ci_workflows(FakeRoot(), workspace)

    assert result is None


def test_prompt_ci_workflows_save_returns_selection_and_push(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    selection = root / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"selected": ["n8nPipelines/manual.json"]}', encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    tk_fake = _ReturnTk()
    _ReturnTk.Toplevel.press_return = True
    try:
        with _patch_ci_editor(tk_fake) as (_, _, messagebox):
            messagebox.askyesno = lambda *args, **_kwargs: True
            result = prompt_ci_workflows(FakeRoot(), workspace)
    finally:
        _ReturnTk.Toplevel.press_return = False

    assert result == ({"n8nPipelines/manual.json"}, True)


def test_prompt_ci_workflows_save_asks_push_even_when_empty(tmp_path) -> None:
    """Clearing every pipeline must still offer pushing the empty selection."""
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    tk_fake = _ReturnTk()
    _ReturnTk.Toplevel.press_return = True
    try:
        with _patch_ci_editor(tk_fake) as (_, _, messagebox):
            messagebox.askyesno.return_value = True
            result = prompt_ci_workflows(FakeRoot(), workspace)
    finally:
        _ReturnTk.Toplevel.press_return = False

    assert result == (set(), True)
    messagebox.askyesno.assert_called_once()


def test_prompt_ci_workflows_greys_ineligible_and_toggles(tmp_path) -> None:
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    # Sort key controls which row identify_row() reports: 'a' before 'z'.
    (root / "n8nPipelines" / "aa-manual.json").write_text(_MANUAL, encoding="utf-8")
    (root / "n8nPipelines" / "zz-hook.json").write_text(_HOOK, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    FakeTtk.Treeview.instances.clear()
    with _patch_ci_editor(FakeTk()):
        prompt_ci_workflows(FakeRoot(), workspace)

    tree = FakeTtk.Treeview.instances[0]
    eligible = tree.item("n8nPipelines/aa-manual.json")
    blocked = tree.item("n8nPipelines/zz-hook.json")
    assert "disabled" not in eligible.get("tags", [])
    assert "disabled" in blocked.get("tags", [])
    assert blocked["text"].startswith("\u2013")

    click = tree._bindings["<Button-1>"]
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2610")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2611")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("\u2610")


def test_prompt_ci_workflows_expander_click_does_not_toggle(tmp_path) -> None:
    """Clicking the expander triangle must expand/collapse, not tick the pipeline."""
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True)
    (root / "n8nPipelines" / "manual.json").write_text(_MANUAL, encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root

    FakeTtk.Treeview.instances.clear()
    with _patch_ci_editor(FakeTk()):
        prompt_ci_workflows(FakeRoot(), workspace)

    tree = FakeTtk.Treeview.instances[0]
    click = tree._bindings["<Button-1>"]
    rel = "n8nPipelines/manual.json"
    before = tree.item(rel)["text"]

    tree.element = "Treeitem.indicator"
    click(SimpleNamespace(x=5, y=5))

    assert tree.item(rel)["text"] == before
    # A plain row click still toggles.
    tree.element = "Treeitem.text"
    click(SimpleNamespace(x=5, y=5))
    assert tree.item(rel)["text"] != before


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
        if isinstance(child, tk_fake.Label)
        and "JSON copié" in child._options.get("text", "")
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


def test_prompt_ci_workflows_adds_runs_tab_when_source_provided(tmp_path) -> None:
    workspace = _workspace_with_manual(tmp_path)
    snapshot = _runs_snapshot()
    refreshed: list[RunsPanel] = []
    opened: list[ci_runs.RunSummary] = []
    ran: list[RunsPanel] = []

    FakeTtk.Notebook.instances.clear()
    RunsPanel.instances.clear()

    with fake_runs_panel_bases(), patch(
        "n8n_launcher.gui.ci_runs.tk", FakeTk()
    ), patch("n8n_launcher.gui.ci_runs.ttk", FakeTtk()), _patch_ci_editor(FakeTk()):
        result = prompt_ci_workflows(
            FakeRoot(),
            workspace,
            runs_source=lambda: snapshot,
            runs_refresh=refreshed.append,
            runs_open=opened.append,
            runs_run=ran.append,
        )

    assert result is None  # Escape closes the dialog
    notebook = FakeTtk.Notebook.instances[0]
    tabs = [notebook._tabs[index]["text"] for index in sorted(notebook._tabs)]
    assert tabs == ["Sélection", "Déroulement"]

    panel = RunsPanel.instances[0]
    assert panel.tree.item("run-11")["text"].startswith("\u2714")
    assert refreshed == [panel]

    panel.refresh()
    assert refreshed == [panel, panel]

    panel.open_run_cb(snapshot.runs[0])
    assert opened == [snapshot.runs[0]]

    panel.run()
    assert ran == [panel]


def test_prompt_ci_workflows_without_source_has_no_notebook(tmp_path) -> None:
    workspace = _workspace_with_manual(tmp_path)
    FakeTtk.Notebook.instances.clear()
    RunsPanel.instances.clear()

    with _patch_ci_editor(FakeTk()):
        result = prompt_ci_workflows(FakeRoot(), workspace)

    assert result is None
    assert FakeTtk.Notebook.instances == []
    assert RunsPanel.instances == []


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
    with _patch_ci_editor(FakeTk()), patch.object(
        FakeTk.Toplevel, "wait_window", _drive_run_dialog("  release  ")
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref == "release"


def test_prompt_run_ci_returns_none_on_cancel() -> None:
    with _patch_ci_editor(FakeTk()), patch.object(
        FakeTk.Toplevel, "wait_window", _drive_run_dialog(None)
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref is None


def test_prompt_run_ci_returns_none_on_blank_ref() -> None:
    with _patch_ci_editor(FakeTk()), patch.object(
        FakeTk.Toplevel, "wait_window", _drive_run_dialog("   ")
    ):
        ref = prompt_run_ci(FakeRoot(), "wf.yml", "main")

    assert ref is None


def test_show_ci_dialog_passes_runs_closures_for_github_remote(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    captured: dict[str, object] = {}

    def fake_prompt(_root, _workspace, **kwargs):
        captured.update(kwargs)
        return None

    with patch("n8n_launcher.gui.app.ci_edit.prompt_ci_workflows", fake_prompt):
        app.app._show_ci_dialog(workspace)

    source = captured["runs_source"]
    assert callable(source)
    assert source() == ci_runs.RunsSnapshot("")
    assert callable(captured["runs_refresh"])
    assert callable(captured["runs_open"])
    assert callable(captured["runs_run"])


def test_show_ci_dialog_without_github_remote_uses_single_pane(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = None
    captured: dict[str, object] = {}

    def fake_prompt(_root, _workspace, **kwargs):
        captured.update(kwargs)
        return None

    with patch("n8n_launcher.gui.app.ci_edit.prompt_ci_workflows", fake_prompt):
        app.app._show_ci_dialog(workspace)

    assert captured["runs_source"] is None
    assert captured["runs_refresh"] is None
    assert captured["runs_open"] is None
    assert captured["runs_run"] is None


def test_ci_runs_source_returns_cached_snapshot(app) -> None:
    cached_workspace = app.manager.list.return_value[1]
    other_workspace = app.manager.list.return_value[0]
    cached = _runs_snapshot()
    app.app._ci_runs_cache[cached_workspace.id] = cached

    assert app.app._ci_runs_source(cached_workspace)() is cached
    assert app.app._ci_runs_source(other_workspace)() == ci_runs.RunsSnapshot("")


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

    client_cls.assert_called_once_with("ghp_x")
    assert snapshot.repo_path == "octo/repo"
    assert snapshot.runs[0].id == 11
    assert snapshot.jobs[11][0].name == "validate"
    assert snapshot.pipelines[21][0].rel == "n8nPipelines/a.json"


def test_build_ci_runs_snapshot_without_remote_reports_error(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = None

    snapshot = app.app._build_ci_runs_snapshot(workspace)

    assert snapshot.error is not None
    assert "dépôt GitHub" in snapshot.error


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

    with patch(
        "n8n_launcher.gui.app.auth.resolve_github_token", return_value=None
    ), patch(
        "n8n_launcher.gui.app.prompt_github_token",
        return_value=GitHubTokenPlan(token="ghp_new"),
    ) as prompt, patch("n8n_launcher.gui.app.GitHubClient", return_value=client):
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

    with patch("n8n_launcher.gui.app.prompt_github_token") as prompt, patch(
        "n8n_launcher.gui.app.GitHubClient", return_value=client
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

    with patch(
        "n8n_launcher.gui.app.auth.resolve_github_token", return_value=None
    ), patch("n8n_launcher.gui.app.prompt_github_token", return_value=None), patch(
        "n8n_launcher.gui.app.GitHubClient"
    ) as client:
        app.app._refresh_ci_runs(workspace, panel)
    app.app._drain_events()

    client.assert_not_called()
    panel.apply.assert_not_called()


def test_ci_runs_open_opens_run_url(app) -> None:
    run = _runs_snapshot().runs[0]

    with patch("n8n_launcher.gui.app.open_url") as opener:
        app.app._ci_runs_open()(run)

    opener.assert_called_once_with(run.url)


def test_ci_runs_open_without_url_is_noop(app) -> None:
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
        app.app._ci_runs_open()(run)

    opener.assert_not_called()


def test_ci_latest_branch_prefers_cached_run(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.app._ci_runs_cache[workspace.id] = _runs_snapshot()

    assert app.app._ci_latest_branch(workspace) == "main"


def test_ci_latest_branch_falls_back_to_main(app) -> None:
    workspace = app.manager.list.return_value[1]

    assert app.app._ci_latest_branch(workspace) == "main"


def test_ci_runs_run_dispatches_chosen_ref_and_refreshes(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"
    client = MagicMock()
    panel = MagicMock()

    with patch(
        "n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value="release"
    ) as prompt, patch("n8n_launcher.gui.app.GitHubClient", return_value=client), patch(
        "n8n_launcher.gui.app.ci.WORKFLOW_FILE", "wf.yml"
    ), patch.object(app.app, "_refresh_ci_runs") as refresh, patch.object(
        app.app, "set_status"
    ) as status:
        app.app._ci_runs_run(workspace, panel)
        app.app._drain_events()

    prompt.assert_called_once_with(app.app.root, "wf.yml", "main")
    client.dispatch_workflow.assert_called_once_with(
        "octo/repo", "wf.yml", ref="release"
    )
    status.assert_called_once_with("CI lancée sur « release ».")
    refresh.assert_called_once_with(workspace, panel)


def test_ci_runs_run_cancelled_ref_is_noop(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = "https://github.com/octo/repo.git"
    app.app._ci_token = "ghp_x"

    with patch(
        "n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value=None
    ), patch("n8n_launcher.gui.app.GitHubClient") as client:
        app.app._ci_runs_run(workspace, MagicMock())

    client.assert_not_called()


def test_ci_runs_run_without_remote_warns(app) -> None:
    workspace = app.manager.list.return_value[1]
    app.manager.git_remote_url.return_value = None

    with patch("n8n_launcher.gui.app.ci_edit.prompt_run_ci") as prompt, patch(
        "n8n_launcher.gui.app.GitHubClient"
    ) as client:
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

    with patch(
        "n8n_launcher.gui.app.ci_edit.prompt_run_ci", return_value="main"
    ), patch("n8n_launcher.gui.app.GitHubClient", return_value=client):
        app.app._ci_runs_run(workspace, MagicMock())
        app.app._drain_events()

    message = app.mocks.messagebox.errors[0]
    assert "boom" in message