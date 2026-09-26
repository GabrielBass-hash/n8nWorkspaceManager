"""Tests for the CI page (gui/ci_page.py).

The page replaced a modal dialog, so the tree, its counter, its save flow and
the runs poll are the same behaviours tested here — through the widget instead
of a Toplevel, and with the *retarget* rules the page adds on top.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from helpers import (
    FakeTk,
    FakeTtk,
    all_labels,
    fake_ci_page_bases,
    fake_runs_panel_bases,
    make_workspace,
)

from n8n_launcher.gui import ci_page, theme
from n8n_launcher.gui.ci_page import (
    RUNS_POLL_ACTIVE_MS,
    RUNS_POLL_IDLE_MS,
    CiPage,
    CiSelectionPanel,
)
from n8n_launcher.gui.ci_runs import RunsPanel
from n8n_launcher.workspaces import ci_runs

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


def _workspace_with(tmp_path, exports: dict[str, str], *, selected: list[str] | None = None):
    """Return a workspace whose ``workflows_dir`` holds *exports*."""
    root = tmp_path / "ws"
    (root / "n8nPipelines").mkdir(parents=True, exist_ok=True)
    for rel, body in exports.items():
        (root / rel).write_text(body, encoding="utf-8")
    if selected is not None:
        tests = root / ".n8n-tests" / "tests.json"
        tests.parent.mkdir(parents=True, exist_ok=True)
        tests.write_text(f'{{"selected": {selected!r}}}'.replace("'", '"'), encoding="utf-8")
    workspace = make_workspace(tmp_path, "CI", 5678)
    workspace.workflows_dir = root
    return workspace


def _runs_snapshot(*, error: str | None = None) -> ci_runs.RunsSnapshot:
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
        rel="n8nPipelines/a.json", status="success", detail="", mark="✔"
    )
    return ci_runs.RunsSnapshot(
        repo_path="octo/repo",
        runs=(run,),
        jobs={11: (job,)},
        pipelines={21: (pipeline,)},
        error=error,
    )


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


@contextmanager
def _fake_page_gui():
    """Rebase the page classes and swap the modules they build widgets with."""
    with ExitStack() as stack:
        for module in ("ci_page", "ci_runs"):
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.tk", FakeTk()))
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.ttk", FakeTtk()))
        stack.enter_context(fake_ci_page_bases())
        stack.enter_context(fake_runs_panel_bases())
        yield stack.enter_context(
            patch(
                "n8n_launcher.gui.ci_page.messagebox",
                MagicMock(askyesno=MagicMock(return_value=False)),
            )
        )


def _panel(workspace, **kwargs) -> CiSelectionPanel:
    """Return a loaded selection panel for *workspace*."""
    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None), **kwargs)
        panel.load(workspace)
    return panel


def _tree(panel) -> FakeTtk.Treeview:
    return panel._tree


# --------------------------------------------------------------- the selection
def test_save_hands_the_selection_and_the_push_answer_to_the_host(tmp_path) -> None:
    workspace = _workspace_with(
        tmp_path, {"n8nPipelines/manual.json": _MANUAL}, selected=["n8nPipelines/manual.json"]
    )
    saved: list[tuple] = []

    with _fake_page_gui() as messagebox:
        messagebox.askyesno.return_value = True
        panel = CiSelectionPanel(
            FakeTk.Frame(None),
            on_save=lambda *args: saved.append(args),
        )
        panel.load(workspace)
        panel.save()

    assert saved == [(workspace, {"n8nPipelines/manual.json"}, True)]


def test_save_asks_even_when_the_selection_is_empty(tmp_path) -> None:
    # Without this, "tout décocher" stayed local and GitHub kept running the
    # previous selection on the next push.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    saved: list[tuple] = []

    with _fake_page_gui() as messagebox:
        messagebox.askyesno.return_value = True
        panel = CiSelectionPanel(
            FakeTk.Frame(None),
            on_save=lambda *args: saved.append(args),
        )
        panel.load(workspace)
        panel.save()

    assert saved == [(workspace, set(), True)]
    messagebox.askyesno.assert_called_once()


def test_the_host_may_own_the_push_question(tmp_path) -> None:
    workspace = _workspace_with(
        tmp_path, {"n8nPipelines/manual.json": _MANUAL}, selected=["n8nPipelines/manual.json"]
    )
    saved: list[tuple] = []

    with _fake_page_gui() as messagebox:
        panel = CiSelectionPanel(
            FakeTk.Frame(None),
            on_save=lambda *args: saved.append(args),
            on_push_prompt=lambda _workspace: False,
        )
        panel.load(workspace)
        panel.save()

    assert saved == [(workspace, {"n8nPipelines/manual.json"}, False)]
    messagebox.askyesno.assert_not_called()


def test_ineligible_pipelines_are_greyed_out_and_never_ticked(tmp_path) -> None:
    # Sort key controls which row identify_row() reports: 'a' before 'z'.
    workspace = _workspace_with(
        tmp_path,
        {"n8nPipelines/aa-manual.json": _MANUAL, "n8nPipelines/zz-hook.json": _HOOK},
    )

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(workspace)
        tree = _tree(panel)

    eligible = tree.item("n8nPipelines/aa-manual.json")
    blocked = tree.item("n8nPipelines/zz-hook.json")
    assert "disabled" not in eligible.get("tags", [])
    assert "disabled" in blocked.get("tags", [])
    assert blocked["text"].startswith("-")

    click = tree._bindings["<Button-1>"]
    assert eligible["text"].startswith("[ ]")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("[x]")
    click(SimpleNamespace(x=5, y=5))
    assert tree.item("n8nPipelines/aa-manual.json")["text"].startswith("[ ]")
    # A blocked pipeline keeps its marker however often it is clicked: the
    # export is shown for the diagnosis, never offered for the run.
    panel.toggle("n8nPipelines/zz-hook.json")
    assert panel.selection() == set()
    assert tree.item("n8nPipelines/zz-hook.json")["text"].startswith("-")


def test_clicking_the_expander_does_not_tick_the_pipeline(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(workspace)
        tree = _tree(panel)

    rel = "n8nPipelines/manual.json"
    before = tree.item(rel)["text"]
    tree.element = "Treeitem.indicator"
    tree._bindings["<Button-1>"](SimpleNamespace(x=5, y=5))

    assert tree.item(rel)["text"] == before


def test_bulk_selection_only_touches_testable_pipelines(tmp_path) -> None:
    workspace = _workspace_with(
        tmp_path,
        {"n8nPipelines/aa-manual.json": _MANUAL, "n8nPipelines/zz-hook.json": _HOOK},
    )

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(workspace)
        panel.select_all()
        assert panel.selection() == {"n8nPipelines/aa-manual.json"}
        assert panel.testable_count() == 1
        panel.clear_all()
        assert panel.selection() == set()


def test_the_counter_counts_the_testable_pipelines(tmp_path) -> None:
    workspace = _workspace_with(
        tmp_path,
        {"n8nPipelines/aa-manual.json": _MANUAL, "n8nPipelines/zz-hook.json": _HOOK},
    )

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(workspace)
        panel.select_all()

    assert panel._counter.text == "1 pipeline(s) sélectionnée(s) sur 1 testable(s)"


def test_a_saved_selection_outside_the_exports_is_dropped(tmp_path) -> None:
    # tests.json can name a pipeline that has since been deleted: it must not
    # linger as a tick the user never made.
    workspace = _workspace_with(
        tmp_path,
        {"n8nPipelines/manual.json": _MANUAL},
        selected=["n8nPipelines/manual.json", "n8nPipelines/gone.json"],
    )

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(workspace)

    assert panel.selection() == {"n8nPipelines/manual.json"}


def test_retargeting_reloads_the_saved_selection(tmp_path) -> None:
    # Unsaved ticks are dropped on purpose: the file on disk is the truth, and a
    # mix of two workspaces' selections would be a lie.
    first = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    second = _workspace_with(tmp_path / "other", {"n8nPipelines/manual.json": _MANUAL}, selected=[])

    with _fake_page_gui():
        panel = CiSelectionPanel(FakeTk.Frame(None))
        panel.load(first)
        panel.select_all()
        assert panel.selection() == {"n8nPipelines/manual.json"}
        panel.load(second)

    assert panel.selection() == set()
    assert panel.workspace is second


# ------------------------------------------------------------------ tree sizing
def _tree_for(tmp_path, exports) -> FakeTtk.Treeview:
    """Return the selection tree built for a workspace holding *exports*."""
    workspace = _workspace_with(tmp_path, exports)
    FakeTtk.Treeview.instances.clear()
    return _tree(_panel(workspace))


def test_pipeline_tree_is_built_at_its_minimum_widths(tmp_path) -> None:
    # The tree asks Tk for its minimums and nothing more: the width it really
    # needs is pushed once the rows are in.
    tree = _tree_for(tmp_path, {"n8nPipelines/manual.json": _MANUAL})

    requests = tree.column_requests()
    assert requests["#0"]["minwidth"] == ci_page._SELECTION_MINIMUMS["#0"]
    assert requests["detail"]["minwidth"] == ci_page._SELECTION_MINIMUMS["detail"]
    # Nothing may stretch to fill a pane: the columns are sized, not elastic.
    assert all(request["stretch"] is False for request in requests.values())


def test_pipeline_tree_columns_follow_the_pipeline_names(tmp_path) -> None:
    long_name = "n8nPipelines/" + "very-long-pipeline-name" * 2 + ".json"
    tree = _tree_for(tmp_path, {"n8nPipelines/manual.json": _MANUAL, long_name: _MANUAL})

    widths = tree.column_widths()
    assert widths["#0"] > ci_page._SELECTION_MINIMUMS["#0"]
    assert widths["detail"] >= ci_page._SELECTION_MINIMUMS["detail"]


def test_the_selection_page_follows_its_table_and_never_stretches_it(tmp_path) -> None:
    # A tree packed with a horizontal fill is stretched to the pane, so the
    # header row stops sitting over the cells; the page asks its pane for the
    # width of the whole table instead.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    panel = _panel(workspace)
    tree = _tree(panel)

    assert "x" not in str(tree._pack_options.get("fill", ""))
    assert panel._options["width"] == sum(tree.column_widths().values())


def test_the_selection_page_labels_never_out_request_its_table(tmp_path) -> None:
    # A container's request is the largest of its children's, so the intros and
    # the counter are kept within the table's width: prose re-wraps, the counter
    # is cut. Otherwise the page is wider than its table and the table no longer
    # meets the page's outline.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    # Only this panel's own labels: the fake records every label the suite builds.
    FakeTk.Label.instances.clear()
    panel = _panel(workspace)
    total = sum(_tree(panel).column_widths().values())
    measure = theme.text_measure(panel, ci_page.FONT_META)

    counter = panel._counter._options["text"]
    assert measure(counter) <= total
    # A long selection line is cut with a trailing ellipsis, never clipped.
    panel._counter_text = " · ".join(["une pipeline sélectionnée"] * 20)
    assert panel._caption_text().endswith("…")
    assert measure(panel._caption_text()) <= total
    # Every label this panel built: a wraplength of 0 is a label that has not been
    # sized yet (a pipeline hint), anything else must sit within the table.
    for label in all_labels():
        wrap = int(label._options.get("wraplength", 0) or 0)
        assert wrap == 0 or wrap <= total + 2 * ci_page._SELECTION_PADX


def test_pipeline_hint_wraps_at_the_page_width(tmp_path) -> None:
    # The hint explains why a pipeline is blocked: it must wrap at the page's
    # real width rather than at a hard-coded 820px.
    FakeTk.Label.instances.clear()
    _tree_for(tmp_path, {"n8nPipelines/manual.json": _MANUAL})

    hint = next(
        label
        for label in all_labels()
        if "pipeline non testable" in str(label._options.get("text", ""))
    )
    hint._bindings["<Configure>"](SimpleNamespace(width=900))

    # 864 = 900 - the 18px padding on each side of the panel.
    assert hint._options["wraplength"] == 864


# ------------------------------------------------------------------- the page
def _page(tmp_path, workspace, **kwargs) -> CiPage:
    with _fake_page_gui():
        return CiPage(FakeTk.Frame(None), **kwargs)


def test_the_page_has_a_selection_tab_and_a_runs_tab(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    FakeTtk.Notebook.instances.clear()

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
        )
        page.retarget(workspace)

    notebook = FakeTtk.Notebook.instances[0]
    assert [tab["text"] for tab in notebook._tabs] == ["Sélection", "Déroulement"]
    assert page.subject is ci_page.CI_SUBJECT


def test_the_page_has_no_runs_panel_without_a_runs_source(tmp_path) -> None:
    # A workspace without a GitHub remote has nothing to show in "Déroulement":
    # the panel is simply not built, rather than built empty and confusing.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    RunsPanel.instances.clear()

    with _fake_page_gui():
        page = CiPage(FakeTk.Frame(None), on_save=lambda *args: None)
        page.retarget(workspace)

    assert page.runs_panel is None
    assert not RunsPanel.instances


def test_the_runs_panel_is_fed_from_the_cache_then_wired_to_the_host(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    snapshot = _runs_snapshot()
    refreshed: list[tuple] = []
    opened: list[ci_runs.RunSummary] = []
    ran: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: snapshot,
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
            runs_open=opened.append,
            runs_run=lambda ws, panel: ran.append((ws, panel)),
        )
        page.retarget(workspace)
        # A page is shown, not merely built: that is when the host fetches.
        page.on_show()
        panel = page.runs_panel

    assert panel is not None
    # The cached snapshot is on screen before any fetch is started, so opening
    # the tab is never a blank table waiting on the network.
    assert panel.tree.item("run-11")["text"].startswith("+")
    assert refreshed == [(workspace, panel)]

    panel.open_run_cb(snapshot.runs[0])
    assert opened == [snapshot.runs[0]]
    panel.run_cb()
    assert ran == [(workspace, panel)]


def test_the_run_button_is_absent_without_a_dispatch_callback(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
        )
        page.retarget(workspace)

    assert page.runs_panel is not None
    assert page.runs_panel.run_cb is None


def test_retargeting_switches_the_workspace_of_both_tabs(tmp_path) -> None:
    first = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    second = _workspace_with(tmp_path / "b", {"n8nPipelines/other.json": _HOOK})
    seen: list[object] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda workspace: seen.append(workspace) or _runs_snapshot(),
        )
        page.retarget(first)
        page.retarget(second)

    assert page.workspace is second
    assert page.selection.workspace is second
    assert seen == [first, second]


def test_saving_from_the_page_names_the_workspace_it_shows(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    saved: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(FakeTk.Frame(None), on_save=lambda *args: saved.append(args))
        page.retarget(workspace)
        page.selection.save()

    assert saved == [(workspace, set(), False)]


def test_saving_before_any_workspace_is_refused(tmp_path) -> None:
    # The page is built before the host retargets it; a stray save then must not
    # attribute a selection to the wrong workspace.
    with _fake_page_gui():
        page = CiPage(FakeTk.Frame(None), on_save=lambda *args: None)
        with __import__("pytest").raises(RuntimeError):
            page.selection.save()


# ---------------------------------------------------------------- runs polling
def test_the_page_polls_fast_while_a_run_is_in_flight(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    refreshed: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _active_run_snapshot(),
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
        )
        page.retarget(workspace)
        page.on_show()
        assert page.after_callbacks[-1][0] == RUNS_POLL_ACTIVE_MS
        page.after_callbacks[-1][1]()
        assert page.after_callbacks[-1][0] == RUNS_POLL_ACTIVE_MS

    assert refreshed == [(workspace, page.runs_panel), (workspace, page.runs_panel)]


def test_the_page_polls_slowly_when_nothing_runs(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
            runs_refresh=lambda ws, panel: None,
        )
        page.retarget(workspace)
        page.on_show()
        assert page.after_callbacks[-1][0] == RUNS_POLL_IDLE_MS


def test_a_hidden_page_stops_polling(tmp_path) -> None:
    # The user is looking at the workspace list again: nothing it polls can be
    # seen, and the GitHub API budget is worth more than the last tick.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    refreshed: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
        )
        page.retarget(workspace)
        page.on_show()
        pending = page.after_callbacks[-1][1]
        page.on_hide()
        pending()

    assert refreshed == [(workspace, page.runs_panel)]
    assert page.after_callbacks == []


def test_closing_the_page_stops_polling(tmp_path) -> None:
    # Without this Tk keeps firing the tick against destroyed widgets.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    refreshed: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
        )
        page.retarget(workspace)
        page.on_show()
        pending = page.after_callbacks[-1][1]
        page.on_close()
        pending()

    assert refreshed == [(workspace, page.runs_panel)]
    assert page.after_callbacks == []


def test_a_stale_tick_of_a_retargeted_page_is_dropped(tmp_path) -> None:
    first = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    second = _workspace_with(tmp_path / "b", {"n8nPipelines/other.json": _HOOK})
    refreshed: list[tuple] = []

    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _workspace: _runs_snapshot(),
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
        )
        page.retarget(first)
        page.on_show()
        stale = page.after_callbacks[-1][1]
        # Selecting another workspace fetches for the new one; the tick armed for
        # the previous one must not fetch for it a second time.
        page.retarget(second)
        stale()

    assert refreshed == [(first, page.runs_panel), (second, page.runs_panel)]


@contextmanager
def _page_for(workspace, *, refreshed, available=None, snapshot=None):
    """Yield a page bound to a runs host, with *refreshed* recording fetches.

    The fake GUI stays open for the whole ``with`` body: a page arms its poll
    through ``Frame.after``, so the timers only exist while the fakes do.
    """
    with _fake_page_gui():
        page = CiPage(
            FakeTk.Frame(None),
            on_save=lambda *args: None,
            runs_source=lambda _ws: snapshot or _runs_snapshot(),
            runs_refresh=lambda ws, panel: refreshed.append((ws, panel)),
            runs_available=available,
        )
        page.retarget(workspace)
        yield page


def test_a_workspace_without_a_github_remote_is_not_polled(tmp_path) -> None:
    # The tab stays (it never appears and disappears under the user) but a
    # workspace with no GitHub remote has nothing to fetch, so no call is made
    # and no timer is armed — not even every 5 s of git subprocesses.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    refreshed: list[tuple] = []

    with _page_for(workspace, refreshed=refreshed, available=lambda _ws: False) as page:
        page.on_show()

        assert refreshed == []
        assert page.after_callbacks == []
        assert page.runs_panel is not None
        # The header line is a *summary* cut to the table's width; the note in
        # full sits in the note area below the tree.
        assert page.runs_panel._summary_text == ci_page.NO_REMOTE_NOTE
        assert page.runs_panel._empty._options["text"] == ci_page.NO_REMOTE_NOTE


def test_the_runs_tab_shows_the_note_where_the_runs_would_be(tmp_path) -> None:
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    with _page_for(workspace, refreshed=[], available=lambda _ws: False) as page:
        _assert_note_shown(page)


def _assert_note_shown(page) -> None:
    """Assert the runs tab explains the empty state instead of failing."""
    assert page.runs_panel is not None
    assert page.runs_panel._last.repo_path == ""
    assert page.runs_panel._last.error is None
    assert page.runs_panel._last.note == ci_page.NO_REMOTE_NOTE
    assert page.runs_panel._empty._options["text"] == ci_page.NO_REMOTE_NOTE


def test_a_retarget_to_a_readable_workspace_resumes_polling(tmp_path) -> None:
    # The same tab serves every workspace: coming back to a GitHub one must
    # poll again, without closing and reopening the tab.
    offline = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    online = _workspace_with(tmp_path / "b", {"n8nPipelines/other.json": _HOOK})
    online.id = "ws-online"
    refreshed: list[tuple] = []
    readable = {offline.id: False, online.id: True}

    with _page_for(offline, refreshed=refreshed, available=lambda ws: readable[ws.id]) as page:
        page.on_show()
        assert page.after_callbacks == []

        # The user goes back to the list, then selects a GitHub workspace: hidden,
        # so the retarget only serves the cache, and showing the tab polls again.
        page.on_hide()
        page.retarget(online)
        assert refreshed == []
        page.on_show()

        assert [ws for ws, _ in refreshed] == [online]
        assert page.after_callbacks[-1][0] == RUNS_POLL_IDLE_MS


def test_an_unreadable_remote_is_treated_as_no_runs(tmp_path) -> None:
    # Probing the remote may fail (git missing, repo half-removed): the tab
    # must degrade to the note instead of raising inside a poll tick.
    workspace = _workspace_with(tmp_path, {"n8nPipelines/manual.json": _MANUAL})
    refreshed: list[tuple] = []

    def boom(_workspace):
        raise OSError("git is missing")

    page = _page_for(workspace, refreshed=refreshed, available=boom)
    with page as shown:
        shown.on_show()

        assert refreshed == []
        assert shown.runs_panel is not None
        assert shown.runs_panel._summary_text == ci_page.NO_REMOTE_NOTE
        assert shown.runs_panel._empty._options["text"] == ci_page.NO_REMOTE_NOTE
