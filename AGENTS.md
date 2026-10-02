# AGENTS.md — n8n-launcher

## How to navigate this repo

57 modules, ~11.9k lines under `src/n8n_launcher/`. Locate before you read, in
increasing order of cost — stop at the first step that answers the question:

1. `grep` — a literal identifier.
2. `tree-sitter_find_usage` — a symbol's definition and every reference.
3. `tree-sitter_get_symbols` — what a file exports, before opening it.
4. `tree-sitter_get_dependencies` — a file's import graph.
5. `tree-sitter_get_file` on a line range — never a whole file when a
   range will do.
6. `read` with `offset`/`limit`.

The tree-sitter MCP server indexes lazily. Call
`register_project_tool(path=".", name="n8n-launcher")` **once per session**
before any other `tree-sitter_*` tool — every project-scoped call fails with
`Project 'n8n-launcher' not found` without it. A subagent for that ladder is
already configured in `.opencode/agents/codnav.md`; prefer it over reading
whole files yourself.

The server is launched by `opencode.json` as
`uvx --with 'mcp<2' mcp-server-tree-sitter`. The pin is load-bearing:
`mcp-server-tree-sitter` imports `mcp.server.fastmcp`, which `mcp` 2.x renamed
to `MCPServer`, so an unpinned server dies on import with `ModuleNotFoundError`
and OpenCode reports only `MCP error -32000: Connection closed`. JSON admits no
comment, so the reason is recorded here.

`docs/architecture.md` holds the module map (which file owns a behaviour) and
the gotchas measured against a real n8n / Docker / Chromium host. Read it when
you touch `n8n/`, `remote/`, `docker/`, `git/`, `github/`, `gui/browser.py`,
`gui/window.py` or `gui/board.py`. Do not read it to answer "how do I run the
tests" — that is §Commands below.

Type errors are reported live by the `basedpyright` LSP, which reads
`pyproject.toml` — the same config `uv run basedpyright` uses, so an editor
diagnostic and a CI diagnostic can never disagree. Lint and format have **no**
LSP here on purpose: `uv run ruff check .` and `uv run ruff format --check .`
are the contract, and a second server would only duplicate them.

## What this is

Cross-platform desktop app (Python 3.12+; Windows, macOS, Linux) that manages
isolated Docker-based n8n workspaces. One package, `src/n8n_launcher`, split by
concern; the thin root `run.py` is the dev entrypoint (`uv run python run.py`).

The interface is **PySide6**, confined to `src/n8n_launcher/gui/`. Every other
module must stay importable on a headless machine, and
`tests/unit/gui/test_toolkit_isolation.py` proves it from a fresh subprocess
(only a subprocess can: another test has already imported PySide6 by then).

The rule that survived the Tk deletion: **a decision that used to live in a view
is a function with no widget in it, and its test is a plain assertion.** Do not
reintroduce a toolkit to make something easier to draw — extract the decision
into `workspaces/status.py`, `workspaces/dialogs.py`, `gui_utils/`, or
`platform/update_flow.py`, where that kind of rule already lives.

`src/` is never pip-installed. `pythonpath = ["src"]` (pytest) and
`--paths src` (PyInstaller, targeting `run.py` not `__main__.py`) are the only
two places `src/` is made visible; relative imports inside the package are what
the bundle depends on.

Version is single-source SemVer in `src/n8n_launcher/__init__.py`
(`__version__`, currently **6.0.0**); `pyproject.toml` inherits it via
`dynamic = ["version"]`, `build.py` embeds it, `platform/updater.py` compares
it. Bump it by hand. `release.yml` publishes from `main` only, and only when the
source version differs from the last `v<version>` tag — no auto-bump.

Packaging: `.dmg` (macOS, ad-hoc signed **deepest-first**), a
`dist/n8n-launcher/` onedir directory (Windows), and that same directory wrapped
into the `.AppImage` on Linux. **Never `--onefile`**: Qt resolves its platform
plugin at runtime and a single-file archive unpacks the bundle into a temp dir
on every launch.

## Commands

```bash
uv sync                        # runtime + dev tooling (dev group by default)
uv sync --frozen               # what CI does
uv sync --group packaging      # PyInstaller + dmgbuild, for scripts/build.py

uv run pytest                  # unit suite; addopts = -m 'not integration' + coverage
uv run pytest -m integration   # integration; needs a Docker daemon
uv run pytest tests/unit/docker/test_compose.py::test_render_compose_managed
uv run pytest tests/unit/gui/test_responsive.py -m responsive

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pre-commit run --all-files   # ruff check + format + basedpyright

uv run python scripts/build.py      # macOS .dmg; onedir exe on Linux/Windows
bash scripts/build_appimage.sh      # after build.py produced dist/n8n-launcher
```

Gate state at the time of writing: `ruff check` clean, `ruff format --check`
clean (119 files), `basedpyright` **0 errors**, `pytest` **887 passed / 7
deselected** (7 = integration), coverage 91 % against a CI floor of
`--cov-fail-under=80`.

`basedpyright` runs in `typeCheckingMode = "standard"` and excludes
`tests/**` and `scripts/**` **only** — every module under `src/n8n_launcher/`
must typecheck because it is shipped. Do not widen the exclusion back to `src/`.
The test fixtures and build scripts are deliberately loose (`MagicMock` returns,
raised-in-test paths) and are not shipped.

## CI layout (`.github/workflows/ci.yml`)

`lint` (three steps: `ruff check`, `ruff format --check`, `basedpyright` — *not*
`pre-commit run --all-files`, which is the local equivalent) gates both `test`
and `integration`; `test` (matrix ubuntu/macos/windows, `fail-fast: false`)
gates `build` (three OS + the Linux AppImage).

- The `test` job installs the Qt runtime libraries (`libgl1 libegl1 …`) and runs
  **two** pytest invocations: the whole suite with `--cov-fail-under=80`, then
  `tests/unit/gui/test_responsive.py -m responsive` as its own report. The
  responsive suite is one parametrized test over 1024×640, 1280×720, 1024×1024.
- The `integration` job passes **`-p no:pytest-qt`**: the plugin imports `QtGui`
  from its own `pytest_configure`, before any test is collected, and on a runner
  without those libraries that is an `INTERNALERROR> ImportError: libEGL.so.1` —
  a dead job, not a failed test. Any new job that runs pytest has to pick one
  side of that trade-off explicitly.
- The Windows build smoke test needs `Start-Process -Wait`: `--windowed` is a
  GUI-subsystem binary, so PowerShell never sees its exit code otherwise.

## Test structure

- `tests/unit/` mirrors `src/n8n_launcher/` as `tests/unit/<pkg>/test_<module>.py`.
  Markers: `unit`, `integration`, `responsive`. There is no `tests/unit/gui_utils/`
  in the old list — it now exists and holds `test_text.py` and
  `test_responsive_contract.py`.
- `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` at **module import time**
  (it must be set before the first Qt import anywhere, which is why it is not a
  fixture), and provides the autouse `sandbox_platform_dirs` fixture: it patches
  `core.paths.user_config_dir` / `user_log_dir` — the functions, not the XDG
  variables, because macOS and Windows ignore those — so a test that builds a
  default `ConfigStore()` or calls `bootstrap_logging()` writes to a tmp dir
  instead of the developer's real `~/.config/n8n-launcher`. A test-level
  `monkeypatch.setattr` still wins. `managed_workspace` is the shared
  `Workspace` (spaces in its folder name on purpose).
- `tests/unit/gui/conftest.py` exposes `qt_app` = `ensure_application()`, the same
  singleton the shell uses.
- **Filename collisions are a real trap.** `tests/unit/<pkg>/test_manager.py` must
  not be used: the base name collides across `docker/`, `git/` and `workspaces/`,
  and there is no `__init__.py` to disambiguate. Hence `test_docker_manager.py`,
  `test_git_manager.py`, `test_workspace_manager.py`, and the same logic for
  `test_dialog_plans.py` (sat beside a deleted `gui/test_dialogs.py`) and
  `test_update_controller.py`. Pick a base name that exists nowhere else.
- `tests/unit/test_main.py`, `tests/unit/workspaces/test_ci_runner.py`,
  `tests/unit/remote/test_ssh_observability.py` and
  `tests/unit/platform/test_updater.py` cover the entry point, the generated CI
  harness and the updater; they are easy to forget when touching those modules.
- `tests/integration/` needs Docker (auto-skips without it) and a session-scoped
  `DockerManager(timeout=180)`. Four files: `test_lifecycle.py` (create → start →
  stop), `test_remote_deploy.py` (phase A `install_server`, phase B full
  `publish`), `test_remote_execution_status.py` (a real n8n execution read
  through the generated status command) and `test_ci_runner_smoke.py`.
  `conftest.py` holds the sshd sandbox (`SshServer`, `build_sshd_image`,
  `_isolated_docker_home`, `ssh_server`, `n8n_tunnel`) and `ssh_server.Dockerfile`
  builds it. The sandbox is bridge-networked with sshd on a **random high port**
  (privileged ports are not publishable on Docker Desktop, and `--network host`
  stays inside the VM, unreachable from macOS/Windows hosts); `n8n_tunnel` bridges
  the instance with an SSH reverse tunnel under an isolated `HOME`, which
  re-exports `DOCKER_HOST` and `DOCKER_CONFIG` or the CLI loses its `compose`
  plugin. Host keys live in the named volume `n8n-launcher-sshd-hostkeys`
  (macOS `ssh` ignores `$HOME` for `~/.ssh`, so a re-run must not see a changed
  host key). Set `N8N_LAUNCHER_TEST_DOCKER_SOCKET` when the host socket is not
  `/var/run/docker.sock` (macOS: `$HOME/.docker/run/docker.sock`); CI runs this
  suite on `ubuntu-latest` only, where the default is already right.
- `tests/test_structure.py` and `tests/test_parity.py` were deleted with the Tk
  interface. Do not reintroduce widget-tree walking.

## Conventions

- **Every public function ships with its unit test.** A new function without one
  is rejected, and its test belongs in `tests/unit/<pkg>/test_<module>.py`.
- **Docstrings everywhere**: every module, function and class. A non-obvious
  variable or block gets a comment explaining the *why*, not the *what*. This
  repo is written in that style — match it. `from __future__ import annotations`
  is repo-wide.
- **Responsive layout is a blocking invariant**, not a warning: views must stay
  usable at 1024×640, at other viewports and ratios, under changed DPI and with
  realistic content. Use layouts, size hints, size policies and controlled
  scrolling. Never add a resolution-specific branch or a magic dimension to
  silence a test. The sizes live in `gui_utils/responsive.py`.
- **GUI actions stay sparse and contextual.** Selection, a standard gesture or an
  existing lifecycle already express most actions; a new button has to justify
  its discoverability, uniqueness and visual cost. Double-click means **open**,
  not start: a card already `RUNNING` opens its instance (`can_open`), because
  "Docker already holds this stack" and "the launcher owns this window" are two
  different facts.
- **A lifecycle action belongs to the card, not to a button beside it.** A
  stopped card is started by double-clicking it; a live one is stopped by
  clicking its **status pill** (`primary_action`), which is the one control the
  card already wears — the hover ring is what makes it a control, so nothing has
  to advertise it. The right-click menu (`card_actions`) is the complete,
  explicitly labelled surface and is where `Supprimer…` lives. Never gate a
  destructive action behind a gesture whose meaning is not written down, and
  never add a per-card button: the header keeps only *Nouveau* plus the
  keyboard-reachable *Arrêter* fallback for the selected card.
- **A workspace that could still be running must always be stoppable.**
  `can_stop` is `state not in {STOPPED, STOPPING}` and not `state is RUNNING`:
  a failed start leaves containers up (the error lands *after* `docker up`) and
  a crash-looping container is reported `STARTING`, so narrowing it produces a
  live workspace the user cannot bring down. What blocks a *concurrent* stop is
  the actions layer's busy lock, not `can_stop` — which is why every card
  gesture and the menu check it too.
- **A view never does its own I/O.** Git / GitHub / SSH / Docker work belongs on
  a `QRunnable` (`gui/actions.py`) or an injected callback, never in a paint
  handler or a slot that Qt calls synchronously.
- **Generated content is a template string, never an f-string**
  (`remote/deploy.py`, `workspaces/ci.py`), so braces and `$` survive verbatim;
  values arrive through `__TOKEN__` substitution.
- **Ruff policy**: the shell-outs to `docker` / `git` / `gh` are deliberate, so
  each package that spawns processes carries its own
  `per-file-ignores` entry (`S603`/`S604`/`S607`, …) in `pyproject.toml` — add
  one when you add a package. `gui/**` deliberately has **no** `S110`: a
  swallowed exception there has to be a visible `except` that logs, never a bare
  `pass`.
- Never commit `state/`, `workspaces/`, `*.env`, credentials or tokens. The bare
  `workspaces/` pattern in `.gitignore` also matches `src/n8n_launcher/workspaces/`
  and `tests/unit/workspaces/`; both are re-included with negated patterns, so new
  files there stay trackable — do not "fix" the pattern.

`git/__init__.py` and `remote/__init__.py` re-export their package's public
surface; import managers through them.

## The sizing rule

One rule, and it outlived the widgets: **a column's width is its content, and the
room is a budget the table absorbs** — never a guess, never elastic, never a
second rule a table can be laid out by.

- `column_widths`: each column takes the width of its widest heading or value
  plus the cell margin, clamped between its declared minimum and maximum.
- `fit_budget`: the surplus above the natural widths goes to the declared
  `flexible` column (capped at its maximum); a room too short is taken back
  **proportionally**, never below any column's minimum. An `available` of `0` or
  `1` means *unmeasured* — what a geometry manager answers before the first
  layout — so the columns keep their content widths.
- `measure` is a `Callable[[str], int]` parameter, short-listed to the 8 longest
  values per column (`_MEASURED_CANDIDATES`), so a 500-row table costs what a
  5-row one costs.

The measurement archaeology behind this (Tk `PanedWindow` sashes, `Treeview`
request invalidation, …) is in `MIGRATION.md` §2 — read it before rebuilding a
table or a splitter.

## Rules that no other file states

- **A stop a user asked for is a close for that workspace.** Every gesture that
  stops one workspace goes through `manager.stop_with_sync`, never `stop`: it
  exports the workflows and pushes them, which is exactly what closing the app
  does. `stop` alone is the internal teardown, and using it from a card gesture
  loses everything the user just built in n8n until they quit.
- **A model's `refresh` is a reset, and a reset eats the selection.** The window
  remembers the selected workspace id and re-selects it afterwards
  (`MainWindow._on_workspaces_changed`); without that, every reconcile or
  creation silently cleared the header's target. Re-derive the header actions on
  *both* notifier signals — a state change with no user gesture behind it is
  otherwise invisible to the control that acts on it.
- **Git is serialized per workspace.** Every git lifecycle call runs inside
  `git/manager.py::workspace_git_lock(workflows_dir)`, a reentrant `FileLock` on
  `.n8n-launcher.git.lock` (re-entrant down the call stack of one thread,
  exclusive between threads *and* between processes). Auto-pull on start and
  auto-push on close / publish / CI must never interleave. The lock file is in
  `EXCLUDE_BODY` so `git add -A` never stages it.
- **Branch model**: `dev` is the work branch *and* the CI branch for every
  workspace; `main` exists only as the production reference on the server's bare
  repo (`publish` maps `dev → main`). Legacy `main` / `n8n/*` branches are renamed
  to `dev` once on the first `ensure_running()`.
- **The GitHub token is never persisted by the flows that use it.** It is used
  once for the API call, then `git_seed_remote()` pushes with a one-shot
  tokenized URL and **no `-u`**, so the token never lands in `.git/config`. The
  `remote_url` stored in `GitConfig` is the clean `https://github.com/<owner>/<name>.git`
  URL, which is what keeps CI eligibility (`github_repo_path`) working.
- **`save_ci_selection()` does not enforce eligibility** — it writes whatever the
  caller ticked. A caller that can tick a pipeline must ask
  `workflow_eligibility()` first; that is why the verdict carries a French,
  human-readable rationale.
- **The remote hook starts with `cd "$HOME"`**: git runs hooks with the cwd set to
  the bare repo while every generated path (`BASE`, `BARE`, `WORKFLOW`, …) is
  home-relative. Repo paths are `shell-quoted` everywhere. Both the hook and
  `deploy.py` write `last-deploy.json` atomically, and `write_remote_file` ships
  content via `cat > <path>.tmp && mv -f` so a failed transfer never leaves a
  truncated generated file.
- **`publish()` pushes with a 300 s timeout** (`_PUBLISH_PUSH_TIMEOUT`), not the
  30 s every other git call gets, because the `post-receive` hook runs Compose up
  and the import synchronously.
- **Workflow files are scanned in two places** (`n8nPipelines/` and the workspace
  root, because export mirrors there), so **every consumer dedups by basename in
  favour of `n8nPipelines/`**: `collect_workflows()` in `workspaces/ci.py` and
  `collect_workflow_files()` in the generated `deploy.py`. Without it the same
  workflow is imported or uploaded twice.
- **The journal is the whole observability surface.** `EventStore.search_events()`
  is the only filter and it filters in SQL; there is no second in-memory
  implementation to disagree with it. The level is part of the searched text
  (typing `ERROR` filters by severity). Reads degrade to an empty section rather
  than raising, and no supervision read exposes the public n8n port.
- **Session bracketing is a contract**: `Surveillance active` and
  `Surveillance terminée` must both land in the journal, in that order, with the
  teardown before the close. The closing event is emitted by
  `__main__._close_session()` in `main()`'s `finally`, which re-runs the
  `stop_all` shutdown itself — `atexit` handlers fire *after* that `finally`, i.e.
  once the store is closed, so their records are dropped. A session that could not
  read its config is still bracketed (count 0), otherwise a hard kill would be
  indistinguishable from a launcher still running.

## Further reading

- `docs/architecture.md` — the module map (which file owns which behaviour) and
  the gotchas measured against a real n8n 2.x / Docker Desktop / Chromium host.
  Read it before touching `n8n/`, `remote/`, `docker/`, `git/`, `github/` or the
  two GUI modules that own I/O.
- `README.md` — user-facing feature behaviour: git sync, GitHub, workflow import
  and export, remote deployment, CI, n8n integration notes, distribution, releases.
  The behaviour sections here deliberately do not repeat it.
- `MIGRATION.md` — **historical** record of the phase-1 deletion of the Tk
  interface. Its §1–§3 (a view collects, `measure` is a parameter, side effects
  are injected) and the Tk measurements are what still governs; the rest is the
  archive of how the deletion was sequenced. Do not read it to learn current
  behaviour — the dependency set it assumes (no toolkit) is obsolete, PySide6 is
  the interface and `gui/app.py` is real.
- `PRODUCT_SUMMARY.md` — the behavioural contract in one pass, useful before
  changing a lifecycle.
