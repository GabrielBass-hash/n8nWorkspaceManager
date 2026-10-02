# MIGRATION.md — the interface is gone; the logic is not

**Historical: this describes phase 1 (6.0.0 rebuilds the shell on PySide6).**
During phase 1 `src/n8n_launcher/gui/` was a two-file seam whose `run_gui()` and
`LauncherApp.run()` raised `NotImplementedError("new GUI not yet implemented
(phase 2)")` on purpose, and `main()` caught that refusal so the journal session
still closed and every workspace was still stopped. None of that is true any
more; the rules below are.

This file is the record of that deletion and of what survived it. It is kept
because the deleted code cost real debugging time and its rules still describe
correct behaviour — not because it schedules anything.

## What was deleted, and how much

The interface was exercised only through a widget tree, under Xvfb, against
three operating systems at once; `tests/test_structure.py` and
`tests/test_parity.py` existed to walk that tree and compare the three OS
against each other.

| Removed | Lines |
|---|---|
| `src/n8n_launcher/gui/` (18 files) | 9,917 |
| `tests/unit/gui/` (18 files of widget fakes and view tests) | 13,022 |
| `tests/test_structure.py` + `tests/test_parity.py` | 618 |
| Dead code with no caller left after the extraction (see below) | −4,497 |

What remains of the package is 88 lines: `gui/app.py` (71) and
`gui/__init__.py` (17). The largest file it had was `app.py` at 2,971 lines,
followed by `dialogs.py` (1,199), `monitoring.py` (1,095), `layout.py` (778),
`ci_page.py` (727), `board.py` (681), `ci_runs.py` (594) and `ci_edit.py`
(388).

## Where the decisions went

Extraction was not the same as keeping everything. What survives is a plain
function or dataclass with a plain test — no toolkit, no event loop, no display.

The test for a survivor is not "it has no widgets" but **it answers a question
the domain still asks**: which DB a new workspace gets, in what order a shutdown
runs, how wide a column should be, what to ask before downloading an update. A
row formatter, a status badge and a snapshot dataclass answer none of those, so
they went.

| Concern | Now lives in | Entry points |
|---|---|---|
| What a creation form collects | `workspaces/dialogs.py` | `CreatePlan`, `GitConfigChoice`, `GitClonePlan`, `GitHubCreatePlan`, `GitHubRepoPick`, `GitHubTokenPlan` |
| Which DB a new workspace gets | `workspaces/dialogs.py` | `default_creation_db`, `fresh_managed_db_config` |
| Shutdown order | `workspaces/close.py` | `CloseSequence`, `CloseHooks` |
| Update: check → download → install | `platform/update_flow.py` | `UpdateController` (every question injected) |
| Update: network + filesystem | `platform/updater.py` | unchanged — it was already separate |
| Column and room sizing | `gui_utils/text.py` | `ELLIPSIS`, `ellipsize`, `column_widths`, `fit_budget` |
| Is this pipeline testable? | `workspaces/ci.py` | `workflow_eligibility`, `missing_credentials`, `load_export` |

`workflow_eligibility` is the borderline case, and it is deliberate: it decides
whether the `N8N_CI_CREDENTIALS` secret and the tests selection are even
meaningful. It takes the persisted `(name, type)` metadata directly, because
`save_ci_selection()` writes whatever it is handed — a caller that can tick a
pipeline has to ask.

`gui_utils/text.py` also dropped `wrap_at`: it took a `tk.Label` and set
`config(wraplength=…)`, which is a widget call wearing a decision's clothes.

## The three designs worth not breaking

### 1. A view collects, it does not decide

`CreatePlan` is a dataclass. `default_creation_db(workspace)` returns a `DbMode`
by looking at whether `db/migrations/` holds SQL. A form asks for a folder and a
name and hands back one of these; nothing downstream re-derives the decision
from a widget's contents. This is what let the dialogs go without the behaviour
going.

A new question ("use the existing Postgres?") is a new field on the plan plus a
rule in the module that fills it — not an `if var.get():` in a view.

### 2. `measure` is a parameter, not a widget call

`gui_utils/text.py::column_widths(rows, *, headings, minimums, maximums,
measure, …)` takes a `measure: Callable[[str], int]`. The old
`gui/theme.py::text_measure(root, font)` resolved a named Tk font and fell back
to a per-character estimate; that is now the caller's job, and it is why the
sizing rule is still testable on a machine with no display (the tests pass
`len`).

The rule itself is the one thing not to "improve": **a column's width is its
content, and the room is a budget the table absorbs.** `fit_budget` gives the
surplus to the declared `flexible` column, takes a short room back
proportionally and never below a column's minimum. An `available` of `0` or `1`
means *unmeasured* — what a geometry manager answers before the first layout —
so the columns keep their content widths. The two bugs it exists to prevent, a
nearly empty column reserving a long one's room and the leftover handed to a
"flexible" column that was not the one being clipped, are still live.

Measurement stays short-listed to the 8 longest values per column
(`_MEASURED_CANDIDATES`), so a 500-row table costs what a 5-row one costs.

### 3. Side effects are injected, so the flow runs headless

`CloseSequence` takes a `CloseHooks` of callables; `UpdateController` takes
every question as a callback. Neither knows what a button is. The Tk answer for
`UpdateController`'s prompts lived in three methods of the old window and the
flow around them was identical, which is what made the move a re-export rather
than a rewrite.

A new prompt is a new callback parameter with a default, not a branch inside
the flow. Then the same tests keep working.

## Measurements that were taken, and should not be re-taken by guessing

These cost real debugging on a real Tk 9.0.4. They are gone from the code
because the code they described is gone; they are recorded so the same facts do
not have to be rediscovered.

- **A `ttk.PanedWindow` has `n-1` sashes**, and `sashpos(i)` is the **right edge
  of pane `i`** (ask for 600, pane 0 measures 600). Tkinter cannot count sashes
  — `sash(i, "max")` is not exposed — so asking for the last one raises
  `sash index i out of range`. `weight` only decides who absorbs the *leftover*:
  a `weight=0` pane is **not** given its request, it freezes at whatever width
  its first layout gave it (the journal stayed at 491 px in a 1600 px window),
  and `pane.configure(width=…)` does nothing. The only lever is `sashpos(i, x)`.
  The gap between two panes is exactly **12 px**.
- **`ttk.Treeview` sizes itself from its columns but does not invalidate the
  request it handed its geometry manager.** `configure(height=cget("height"))`
  is what re-reads it. A fitter that only sets column widths looks correct and
  then does nothing.
- **`panes()` answers widget *paths*, not widget objects.**
- **A container is as wide as its widest child**, so a prose label placed beside
  a table silently widens the card the table was fitted for.
- **Tk delivers a key to the focused widget, its class and the toplevel** — a
  `<Control-c>` binding on a panel never fires if it is bound on the panel.
- **A label's requested width is not its mapped width.** `winfo_reqwidth` is the
  text's own request, `winfo_width` is what the layout gave it, and before the
  first layout it answers `1`.
- **Native `listbox` text is never measured**, only fixed characters, so a table
  inside one is fitted from the column rule, never from `bbox()`.

## Habits that outlived the pages

- **A page never did its own I/O.** It rendered what the host fed it and called
  back, which is what kept git, GitHub and SSH calls on background workers rather
  than in a paint handler.
- **Focus was lifecycle.** `on_show`/`on_hide` were the only thing that started
  and stopped a page's timers, and a snapshot that landed after its card was
  closed was cached but never rendered.
- **A snapshot for a workspace the selection has left is dropped**, not rendered.
- **A retry loop must not overwrite the message on every attempt.** `gui/monitoring.py`
  grouped repeats of one `LEVEL|name` signature for 60 s through a `CriticalGate`;
  that class went with the panel, so the rule survives as a rule, not as code.
- **Never derive `finished` from anything but n8n's own vocabulary.**
  `TERMINAL_EXECUTION_STATUSES` / `PENDING_EXECUTION_STATUSES` live in
  `remote/deploy.py`, are injected into the generated server script through
  `__TERMINAL_STATUSES__` / `__PENDING_STATUSES__`, and a meta-test asserts the
  two parsers agree for every status.

What went with the pages, for the record: `PageSubject` / `PageKind` /
`log_page_event` (`core/subjects.py`), the in-memory `filter_events` and every
row formatter (`monitoring/present.py`), the runs model
(`workspaces/ci_runs.py`) and the server snapshot model. The surviving path to
that history is `EventStore.search_events` / `export_events` and
`WorkspaceManager.server_health` / `server_logs` / `server_deploy_status` /
`server_execution_status`.

## The dead-code pass

The extraction left a second problem: some of what it kept was kept *wrong*. A
module with no caller does not become valid by being toolkit-free. The pass
that removed them was part of the deletion, not an afterthought of it.

| Removed | Was |
|---|---|
| `core/throttle.py`, `core/state_labels.py` | helpers the views used to rate-limit and label; nothing else did |
| `core/first_launch.py`, `core/subjects.py` | first-launch validation and the journal's per-view filter tokens |
| `monitoring/present.py` | the in-memory `filter_events` — `EventStore.search_events` filters in SQL, so this was a second implementation to disagree with |
| `platform/browser.py`, `platform/files.py`, `platform/shortcuts.py` | app-mode launch, "reveal in file manager", desktop shortcut files |
| `workspaces/display.py`, `workspaces/server_snapshot.py`, `workspaces/ci_runs.py` | row formatting, and the two snapshot models a view rendered |
| presentation helpers in `workspaces/ci.py` | `actions_url`, `node_detail`, `start_description`, `ci_counts`, `NO_REMOTE_NOTE`, `provided_credentials` |
| four leaves in live modules | `ensure_directories`, `browser_app_dir`, `_tokenized_remote`, `secrets_path` |

`core/subjects.py` went as a consequence, not a choice: `CI_SUBJECT` was the
last construction site, and `PageSubject` itself existed only to be handed to
`filter_events`. `EventStore.search_events` keeps the search, the redaction and
the retention; only the duplicate in-memory filter and the row formatters went.

Consequence for the conventions: `pyproject.toml` still carries six
`per-file-ignores` entries for paths that no longer exist —
`gui/ci_edit.py`, `gui/ci_runs.py`, `gui/close.py`, `gui/dialogs.py`,
`gui/monitoring.py` and `gui/update_flow.py`. They match nothing. The entry for
`gui/app.py` still matches, but its rules (`S603`, `S606`, `S607`, `S110` — the
subprocess launch and fail-graceful catches of the old window) are vestigial too,
since the stub raises before doing any work.

## The seam contract

Four things are the interface's contract with the rest of the launcher. They
are what `__main__.py` calls, what CI builds, and what the integration harness
expects.

1. `run_gui(store, manager, monitor)` and `LauncherApp(store, manager, monitor)`
   with a `.run()` — the three collaborators are what a shell needs.
2. `main()` keeps the `_start_monitoring` → `run_gui` → `_close_session`
   bracket: every run, including every failure path, ends with
   `Surveillance terminée` in the journal and the workspaces stopped. The
   test for the refusal path
   (`test_main_shuts_the_workspaces_down_when_the_shell_is_not_implemented`)
   pins it.
3. `tests/unit/gui/test_stub.py` is replaced, not deleted, while the seam still
   refuses. It currently pins that both entry points raise and that importing a
   shipped module pulls in no toolkit.
4. `pyproject.toml` has no GUI toolkit in `dependencies`. A shell adds one — with
   the version actually tested against — rather than a floor nobody imports.

The first-launch validation went with the dead-code pass (nothing else called
it), so `main()` stops when there is no config and the launcher cannot create
its own. Nothing in `src/` validates an owner password today: `validate_password()`
was in `gui/first_launch.py` and is gone, so n8n's policy — 8 to 64 characters,
at least one digit and one uppercase letter — has to be re-derived wherever a
password is actually set.
