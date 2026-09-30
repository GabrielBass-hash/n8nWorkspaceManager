# MIGRATION.md — the interface is gone; the logic is not

**Status: the launcher has no GUI.** `src/n8n_launcher/gui/` is a two-file stub.
`run_gui(store, manager, monitor)` and `LauncherApp(store, manager, monitor).run()`
raise `NotImplementedError("new GUI not yet implemented (phase 2)")` on purpose,
and `main()` catches that refusal so the journal session closes and every
workspace is still stopped.

This document exists because the deletion was only half the work. Tkinter took
9,205 lines of `src/` with it, and every *decision* those lines made — what a
creation form collects, in what order a shutdown runs, how wide a column should
be, what to ask the user before downloading an update — had to be rescued first
or it would have been lost. It was rescued. This is where it went, what it looks
like now, and what a new interface has to be careful about.

## Why the widgets went

The interface was ~15,000 lines of views that could only be exercised through a
widget tree, under Xvfb, against three operating systems at once. `tests/test_structure.py`
and `tests/test_parity.py` existed to walk that tree and compare the three OS
against each other. That is a large amount of machinery whose only subject was
itself, and none of it survives an interface rewrite — but the rules the views
enforced did not have to die with them.

## What was kept, and where it went

Every one of these is a plain function or dataclass with a plain test. No
toolkit, no event loop, no display.

| Concern | Now lives in | Entry points |
|---|---|---|
| What a creation form collects | `workspaces/dialogs.py` | `CreatePlan`, `GitClonePlan`, `GitConfigChoice`, `GitHubCreatePlan`, `GitHubRepoPick`, `GitHubTokenPlan` |
| Which DB a new workspace gets | `workspaces/dialogs.py` | `default_creation_db`, `fresh_managed_db_config` |
| First-launch validation | `core/first_launch.py` | `validate_password`, `build_initial_config` |
| Shutdown order | `workspaces/close.py` | `CloseSequence`, `CloseHooks` |
| Row formatting / status probes | `workspaces/display.py` | `format_row`, `git_repo_status`, `row_tooltip`, `db_label` |
| Update: check → download → install | `platform/update_flow.py` | `UpdateController` (all questions injected) |
| Update: network + filesystem | `platform/updater.py` | unchanged — it was already separate |
| Column and room sizing | `gui_utils/text.py` | `column_widths`, `fit_budget`, `ellipsize`, `ELLIPSIS` |
| Journal filtering / formatting | `monitoring/present.py` | `filter_events`, `event_row`, `CriticalGate`, `matches_tokens` |
| What a focused view filters on | `core/subjects.py` | `PageSubject`, `PageKind`, `log_page_event` |
| Runs rendering | `workspaces/ci_runs.py` | `compose_snapshot`, `run_summary`, `job_summary` |
| Server supervision rendering | `workspaces/server_snapshot.py` | `ServerSnapshot`, `server_snapshot_text` |

## The three designs worth not breaking

### 1. A view collects, it does not decide

`CreatePlan` is a dataclass. `default_creation_db(workspace)` returns a `DbMode`
by looking at whether `db/migrations/` has SQL in it. A form asks for a folder
and a name and hands back one of these; nothing downstream re-derives the
decision from a widget's contents. This is what let the dialogs go without the
behaviour going.

**When rebuilding:** a form may validate and convert, never decide. If a new
question appears ("use the existing Postgres?"), add a field to the plan and a
rule to the module that fills it — not a `if check_var.get():` in the view.

### 2. `measure` is a parameter, not a widget call

`gui_utils/text.py::column_widths(rows, measure, …)` takes a
`measure(text, font) -> int` callable. The old `gui/theme.py::text_measure`
resolved a named Tk font and fell back to a per-character estimate; that is now
the caller's job. `wrap_at` and `bind_wraplength` did *not* come with it: both
took a `tk.Label` and set `config(wraplength=…)`, which is a widget call wearing
a decision's clothes, so they went with the widgets. This is why the sizing rule is still testable on a machine with
no display: the tests pass a `lambda s: len(s)`.

The rule itself is the one thing not to "improve": **a column's width is its
content, and the room is a budget the table absorbs.** `fit_budget` gives the
surplus to the declared `flexible` column, takes a short room away
proportionally, and never cuts below a column's minimum. A room of `0` or `1`
means *unmeasured* (what a widget reports before its first layout), so the
columns keep their natural widths. The two bugs it exists to prevent — a nearly
empty column reserving a long one's room, and the leftover handed to a
"flexible" column that was not the one being clipped — are still live.

Measurement should stay short-listed to the 8 longest values per column
(`_MEASURED_CANDIDATES`), so a 500-row table costs what a 5-row one costs.

### 3. Side effects are injected, so the flow runs headless

`CloseSequence` takes a `CloseHooks` of callables; `UpdateController` takes every
question as a callback. Neither knows what a button is. The Tk answer for
`UpdateController`'s prompts lived in three methods of the old window; the flow
around them was identical, which is what made the move a re-export rather than a
rewrite.

**When rebuilding:** a new prompt is a new callback parameter with a default, not
a branch inside the flow. Then the same tests keep working.

## The measurements that were taken, and should not be re-taken by guessing

These cost real debugging on a real Tk 9.0.4. They are gone from the code
because the code they described is gone; they are recorded here so phase 2 does
not spend the same week rediscovering them.

- **A `ttk.PanedWindow` has `n-1` sashes**, and `sashpos(i)` is the **right edge
  of pane `i`** (ask for 600, pane 0 measures 600). Tkinter cannot count sashes
  — `sash(i, "max")` is not exposed — so asking for the last one raises
  `sash index i out of range`. `weight` only decides who absorbs the *leftover*:
  a `weight=0` pane is **not** given its request, it freezes at whatever width
  its first layout gave it (the journal stayed at 491px in a 1600px window), and
  `pane.configure(width=…)` does nothing. The only lever is `sashpos(i, x)`. The
  gap between two panes is exactly **12px**.
- **`ttk.Treeview` sizes itself from its columns but does not invalidate the
  request it handed its geometry manager.** `configure(height=cget("height"))` is
  what re-reads it. This is why a fitter that only sets column widths looks
  correct and then does nothing.
- **`panes()` answers widget *paths*, not widget objects.**
- **A container is as wide as its widest child**, so a prose label placed beside
  a table silently widens the card the table was fitted for.
- **Tk delivers a key to the focused widget, its class and the toplevel** — a
  `<Control-c>` binding on a panel never fires if it is bound on the panel.
- **A label's requested width is not its mapped width.** `winfo_reqwidth` is the
  text's own request; `winfo_width` is what the layout gave it, and before the
  first layout it answers `1`.
- **Native `listbox` text is never measured**, only fixed characters, so a
  table inside one is fitted from the column rule, never from `bbox()`.

## What the host owes its views (habits, not code)

The deleted pages were good for reasons that survive them:

- **A page never does its own I/O.** It was handed what the host had fetched and
  it called back (`source`/`refresh`/`available`, `runs_source`/`runs_run`/…),
  which is what kept git, GitHub and SSH calls on background workers rather than
  in a paint handler.
- **Focus is lifecycle.** `on_show`/`on_hide` were the only thing that started
  and stopped a page's timers, and a snapshot that landed after its card was
  closed was cached but never rendered.
- **A snapshot for a workspace the selection has left is dropped**, not rendered.
- **The journal is the observability surface.** `EventStore` keeps 30 days,
  redacts before writing, and the panel's single free-text field folded the level
  into the searched text — so `EventStore.search_events` / `export_events` are
  now the direct path to the history the panel used to show.
- **A retry loop must not overwrite the message on every attempt.**
  `CriticalGate` groups repeats of one `LEVEL|name` signature for 60 s.
- **Never leave `finished` derived from anything but n8n's own vocabulary.**
  `TERMINAL_EXECUTION_STATUSES` / `PENDING_EXECUTION_STATUSES` live in
  `remote/deploy.py`, are injected into the generated server script through
  `__TERMINAL_STATUSES__`/`__PENDING_STATUSES__`, and a meta-test asserts the two
  parsers agree for every status.

## Rebuilding checklist

1. **Pick a toolkit, deliberately.** Nothing depends on PyQt6/PySide6 any more
   — it was removed from `pyproject.toml` rather than left as a floor for a
   layer nobody imported. Add it back with the version you actually tested
   against, and add the display-server problem it brings (macOS PATH, Linux
   headless CI) to the release notes.
2. **Fill in `gui/app.py` and stop raising.** Keep `run_gui(store, manager,
   monitor)` and `LauncherApp(store, manager, monitor)` as the signatures — the
   entry point, the CI and the integration harness all expect them. Replace
   `tests/unit/gui/test_stub.py` as you go; do not delete the file while the
   stub still refuses.
3. **Do not re-create `tests/conftest.py`'s removed Tk fake** unless there is a
   toolkit to fake. A `FakeTtk` that is not asserted on is dead weight, and the
   fakes it held encoded the bugs above.
4. **Wire the first launch.** `main()` currently stops when there is no config,
   and `core/first_launch.py::run_first_launch` is there to be driven by a view.
   Until then the launcher cannot create its own config, so it cannot be used
   for a first run.
5. **The `_start_monitoring` → `run_gui` → `_close_session` bracket is the
   contract.** Every run, including the failure paths, must end with
   `Surveillance terminée` in the journal and the workspaces stopped. There is a
   test for the stub-refusal path (`test_main_shuts_the_workspaces_down_when_the_shell_is_not_implemented`)
   — keep it passing.

## Reference: what was deleted

| Removed | Was |
|---|---|
| `gui/app.py` | 2,971 lines — the window, the workspace list, every action |
| `gui/dialogs.py` | 1,199 lines — the creation, git, clone and GitHub forms |
| `gui/layout.py` | 778 lines — the fitters and binders (the rule itself survived in `gui_utils/text.py`) |
| `gui/monitoring.py` | 773 lines — the journal panel, its scopes and the export |
| `gui/ci_page.py`, `gui/ci_runs.py`, `gui/board.py` | 719 / 594 / 681 lines — the dashboard and its cards |
| `gui/ci_edit.py`, `gui/dialog.py`, `gui/theme.py`, `gui/server_page.py`, `gui/update_flow.py`, `gui/close.py`, `gui/pages.py`, `gui/tokens.py`, `gui/first_launch.py` | the remaining views and their constants |
| `tests/test_structure.py`, `tests/test_parity.py` | widget-tree snapshots under Xvfb and the cross-OS comparison |
| `tests/unit/gui/` (18 files) | 12,684 lines of widget fakes and view tests |

Kept: 9 lines in `gui/app.py` and 17 in `gui/__init__.py`, plus 523 lines of
newly headless logic in `gui_utils/text.py`, `workspaces/dialogs.py` and
`platform/update_flow.py`.