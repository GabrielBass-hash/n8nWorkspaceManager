# AGENTS.md — n8n-launcher

## What this is

Cross-platform app (Python 3.12+; Windows, macOS, Linux/Ubuntu) for managing
isolated Docker-based n8n workspaces. The interface is **PySide6**, confined to
`src/n8n_launcher/gui/` — no other module imports a toolkit, and
`tests/unit/gui/test_toolkit_isolation.py` asserts it from a subprocess. The
launcher still runs headless: without a display it logs *Interface
indisponible* and exits through the ordered shutdown. `MIGRATION.md` is the
record of what phase 1 deleted and of the rules that survived it; they still
govern the shell.

Single package `n8n_launcher` under `src/`, split by concern, one file per
concern: `core/` (models, config, paths, filelock), `database/`, `docker/`,
`git/`, `github/`, `n8n/` (public API, owner bootstrap, workflow sync, API-key
scopes), `monitoring/` (event journal), `remote/` (server deployment and
bounded observability), `platform/` (ports, updater, update flow),
`gui_utils/` (toolkit-free sizing rules), `gui/` (the seam), `workspaces/`
(manager, close sequence, dialog plans, CI). `git/__init__.py` and
`remote/__init__.py` re-export their package's public surface.

The rule that follows from the deletion: **a decision that used to live in a
view is a function with no widget in it, and its test is a plain assertion.** Do
not reintroduce a toolkit to make something easier to draw — extract the
decision instead.

Releases ship natively per platform: `.dmg` (macOS, ad-hoc signed **deepest-first**,
styled via `dmgbuild`, app icon + full `Info.plist`), a `dist/n8n-launcher/`
onedir directory (Windows), and that same directory wrapped into the `.AppImage`
from `scripts/build_appimage.sh` on Linux. **Never `--onefile`**: Qt resolves
its platform plugin at runtime, and a single-file archive unpacks the whole
bundle into a temporary directory on every launch. `build.py` targets the thin
root `run.py` with `--paths src` so the package keeps its relative imports.

The version is a single-source SemVer declared in
`src/n8n_launcher/__init__.py` (`__version__`, currently **6.0.0**);
`pyproject.toml` inherits it via `dynamic = ["version"]`, `scripts/build.py`
embeds it, `platform/updater.py` compares it. Bump it by hand before a
release. Releases publish from `main` only, and only when the source version
differs from the last `v<version>` tag — no auto-bump, no `[skip ci]`
convention.

## Commands

```bash
uv sync                        # runtime + dev tooling (dev group by default)
uv sync --group packaging      # build tooling

uv run pytest                  # unit tests (integration excluded by addopts)
uv run pytest -m integration   # integration tests (Docker daemon required)
uv run pytest tests/unit/docker/test_compose.py::test_render_compose_managed
uv run pytest tests/unit/gui/test_responsive.py -m responsive

uv run ruff check .
uv run ruff format --check .
uv run basedpyright            # src/ is fully typed; tests/ and scripts/ excluded
uv run pre-commit run --all-files   # ruff check + ruff format + basedpyright

uv run python scripts/build.py      # macOS .dmg via dmgbuild; Linux/Windows exe
bash scripts/build_appimage.sh      # after build.py produced dist/n8n-launcher
```

Stack: **uv** (dependencies + virtualenv), **Ruff** (lint + format),
**basedpyright** (`typeCheckingMode = "standard"`), **pytest + pytest-cov**,
**pre-commit**. Project metadata, `dependency-groups`, `[tool.ruff]`,
`[tool.pytest.ini_options]` and `[tool.basedpyright]` all live in
`pyproject.toml`; the hooks live in `.pre-commit-config.yaml`, with
`basedpyright` as a `local` hook so `pre-commit run --all-files` covers the
whole stack.

Current state of the gates: `ruff check` clean, `ruff format --check` clean
(110 files), `basedpyright` **0 errors**, `pytest` **778 passed / 7 deselected**
(7 = integration), coverage ~91 % with a CI floor of `--cov-fail-under=80`.

`basedpyright` excludes `tests/**` and `scripts/**` **only**: every module under
`src/n8n_launcher/` must typecheck, because it is shipped. The test fixtures and
build scripts are deliberately loose (raised-in-test paths, `MagicMock` returns)
and are not shipped. Do not widen the exclusion back to `src/`.

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check` and
`basedpyright` as three steps in the `lint` job — **not**
`pre-commit run --all-files`; that command is the local equivalent. `lint` gates
`test` (matrix ubuntu/macos/windows, `fail-fast: false`) and `integration`
(ubuntu only, `timeout-minutes: 60`); `test` gates `build` (three OS + the
Linux AppImage). `addopts` is `-m 'not integration' --cov=src/n8n_launcher
--cov-report=term-missing`, so a plain `pytest` runs the unit suite with
coverage.

## Test structure

- `tests/unit/` — self-contained, no external services, run by default. Mirrors
  `src/n8n_launcher/`: `tests/unit/<subpackage>/test_<module>.py`.
  `tests/unit/gui/` mirrors the shell: `test_theme`, `test_notifier`,
  `test_workspace_model`, `test_app`, `test_window`, `test_card_delegate`,
  `test_actions`, `test_create_panel`, `test_first_launch_wizard` (the `gui`
  copy — `core` already owns a `test_first_launch.py`) and
  `test_toolkit_isolation`, which imports every shipped business module in a
  fresh subprocess and asserts no `PySide6`/`shiboken6` reached `sys.modules`.
  `conftest.py` holds the `qt_app` fixture; `tests/conftest.py` forces
  `QT_QPA_PLATFORM=offscreen`, so no test needs an X server.
- **Filename collisions are a real trap.** `tests/unit/<subpackage>/test_manager.py`
  must not be used: the base name collides across `docker/`, `git/` and
  `workspaces/`, and pytest has no `__init__.py` to disambiguate. The files are
  `test_docker_manager.py`, `test_git_manager.py`, `test_workspace_manager.py`.
  The same trap applies to any name that already exists elsewhere in the tree:
  `workspaces/test_dialogs.py` became `test_dialog_plans.py` (it sat beside
  `gui/test_dialogs.py`) and `platform/test_update_flow.py` became
  `test_update_controller.py`.
- `tests/conftest.py` — the `managed_workspace` fixture (a `Workspace` with
  `tmp_path` and a managed DB). It no longer has a `--structure-dir` option:
  the Tk fake and the widget-tree suites went with the interface.
- `tests/integration/` — requires Docker, auto-skips without it. Session-scoped
  `DockerManager` with a 180 s timeout. `tests/integration/conftest.py` holds
  the sshd harness (`SshServer`, `build_sshd_image`, `_isolated_docker_home`,
  the `ssh_server` fixture, the `n8n_tunnel` factory) and
  `ssh_server.Dockerfile` builds the sandbox. The sandbox is bridge-networked
  with its sshd published on a **random high port** (privileged ports are not
  publishable on Docker Desktop, and `--network host` stays inside the VM,
  unreachable from macOS/Windows hosts). The generated `deploy.py` assumes
  `http://127.0.0.1:<n8n_port>` from inside the sandbox, so `n8n_tunnel` bridges
  it with an SSH reverse tunnel (`AllowTcpForwarding yes` + `socat` in the
  Dockerfile) under an isolated `HOME` so the isolated `docker compose` context
  keeps working — `DOCKER_HOST` and `DOCKER_CONFIG` are re-exported, otherwise
  the CLI loses its `compose` plugin. `N8N_LAUNCHER_TEST_DOCKER_SOCKET` points
  the harness at the host socket when it is not `/var/run/docker.sock` (macOS:
  `$HOME/.docker/run/docker.sock`); CI runs this suite on `ubuntu-latest` only,
  where the default is already correct. The sshd host keys live in the named
  volume `n8n-launcher-sshd-hostkeys`, because macOS `ssh` ignores `$HOME` for
  `~/.ssh` and a re-run must not see a changed host key.
  `test_lifecycle.py` (create → start → stop), `test_remote_deploy.py` (phase A
  `install_server`, phase B full `publish` = git-over-SSH + socket-mounted
  hook), `test_remote_execution_status.py` (a real n8n execution queried
  through the generated status command) and `test_ci_runner_smoke.py` all run
  that way.
- `tests/test_structure.py` and `tests/test_parity.py` were **deleted with the
  interface**: they walked the widget tree under an Xvfb and compared the three
  OS against each other.

## Code conventions

- **Unit test coverage is mandatory and maximal**: every public function ships
  with its test in `tests/unit/<subpackage>/test_<module>.py`. A new function
  without a test is rejected.
- **GUI adaptability is a blocking architectural invariant**: every GUI view
  must remain usable at the supported minimum window size (`1024x640`), normal
  and large viewports, unusual ratios, changed DPI/scaling and realistic
  content volumes. Responsive violations are test failures, never warnings.
  Use layouts, size hints, size policies, controlled scrolling and shared
  metrics; do not add resolution-specific branches or arbitrary dimensions to
  silence a test. A GUI change is not done until the `responsive` tests cover
  its resize and long-content behavior.
- **GUI actions must be sparse and contextual**: do not add an action button
  when selection, a standard gesture or an existing lifecycle already expresses
  the action more naturally. Every new action button must justify its
  discoverability, uniqueness and visual cost. In particular, workspace start
  belongs to double-clicking a stopped card, while stopping belongs to the
  ordered application-close sequence; do not duplicate either action in the
  header or hide a destructive stop behind an ambiguous gesture. The double
  click is **open**, not start: a card already in `RUNNING` opens its instance
  (`can_open` in `workspaces/status.py`), because "Docker already holds this
  stack" and "the launcher owns this window" are two different facts and only
  the second one is the view's business.
- Generic writing conventions: clarity, small functions, no dead code, no
  reinvented stdlib helper, explicit typing (`from __future__ import annotations`
  repo-wide).
- **Document the code**: every module has a docstring, every function and class
  has a docstring, and every non-obvious variable or block gets a comment
  explaining the *why*, not the *what*.
- Comments in the code are the exception to the "no comments" habit only when
  the *why* is not obvious from the code.
- Never commit `state/`, `workspaces/`, `*.env`, credentials or tokens. Note
  that the bare `workspaces/` pattern in `.gitignore` also matches
  `src/n8n_launcher/workspaces/` and `tests/unit/workspaces/`; both are
  re-included with negated patterns so new files there stay trackable.

## Key architecture

| Module | Role |
|---|---|
| `__main__.py` | Entry point. `main(argv=None)` short-circuits on `--self-test` before touching the config, the lock or Docker. Single-instance lock → `resolve_docker_command()` → journal → wizard (no config) or `run_gui()` → ordered shutdown. `_signal_shutdown` calls `stop_all` then `request_shutdown()` and only `sys.exit(0)` when there is no `QApplication` — raising `SystemExit` inside a Qt slot would be swallowed by the monitoring excepthook. A config that exists but cannot be read is backed up to `launcher.db.corrupt-<ts>` and named in the journal, then the run stops; a config that does not exist opens the wizard. Registers `atexit` (fallback), handles SIGTERM/SIGINT, and brackets every run: `_start_monitoring()` logs `Surveillance active`, `_close_session()` logs `Surveillance terminée` (session duration + how many workspaces were still up) **before** `monitor.close()`. |
| `gui/app.py` | The shell: `ensure_application`, `display_available`, `GuiUnavailable`, `request_shutdown`/`shutdown_requested`/`clear_shutdown`, `LauncherApp.run()`, `run_gui()`, `SELF_TEST_FLAG` + `self_test()`. `run()` skips `exec()` when a shutdown was already requested; the `QTimer` (`SIGNAL_TICK_MS = 200`) is what lets a Python signal handler run at all while the loop is parked in C++. `self_test()` builds the real window over a throwaway config in a temp dir and returns an exit code — it is what CI runs on a finished artifact. |
| `gui/window.py` | `MainWindow(manager)`: header (*Nouveau* / *Démarrer* / *Arrêter*, objectNames `new`/`start`/`stop`), `QStackedWidget` board + empty state, `QListView` in IconMode painted by `CardDelegate`, selection sync, `workspace_actions` (not `actions`: `QWidget.actions` already exists). |
| `gui/card_delegate.py` | `CARD_WIDTH` / `CARD_HEIGHT`, `tone_color`, `CardDelegate` — paints one workspace card (status pill, port, folder). No widget state, only geometry and colours. |
| `gui/workspace_model.py` | `WorkspaceListModel` (roles `WorkspaceRole` / `StatusRole`, `refresh`, `apply_workspace`, `workspace_at`, `status_at`, `index_of`). Qt annotates `rowCount`/`data` with `QModelIndex \| QPersistentModelIndex`; the overrides must be widened to match or basedpyright fails. |
| `gui/actions.py` | `WorkspaceActions(manager, executor=...)`: signals `busyChanged`/`failed`/`created`, one `_Task(QRunnable)` per call on a `QThreadPool`. The executor is injected, so every test runs synchronously. |
| `gui/create_panel.py` | `plan_from_fields(...) -> CreatePlan` and `CreateWorkspaceDialog` (objectNames `name`/`directory`/`managed`). It collects; it does not create — the plan goes to the action layer. |
| `gui/browser.py` | Opens a workspace's n8n as a standalone Chromium window, or **raises the window it already has**. It owns no memory: the decision belongs to the browser, so `open_web_app` returns a `WebAppOutcome` and the view only passes `reuse`. See the Chromium gotcha. |
| `gui/notifier.py` | `WorkspaceNotifier`: turns manager callbacks into Qt signals. |
| `gui/theme.py` | Palette, metrics and `STYLESHEET`; `dark_palette()`, `apply_theme(app)`. |
| `gui/first_launch.py` | `FirstLaunchWizard` (objectNames `email`/`password`/`work_dir`/`hint`) and `prompt_first_launch(store, docker, parent=None) -> bool`, which raises `GuiUnavailable` when there is no display. The rules it enforces live in `core/first_launch.py`. |
| `core/first_launch.py` | Toolkit-free wizard contract: `SetupWizardError`, `validate_password`, `build_initial_config`. `run_first_launch(store, docker, *, email, password, work_dir) -> AppConfig` refuses unless Docker is available, then writes the config. |
| `gui_utils/text.py` | The sizing rules, toolkit-free: `ELLIPSIS`, `ellipsize`, `column_widths` (the pure rule — a column's content, nothing else) and `fit_budget` (the same rule with a room). `measure` is a **parameter**: a `Callable[[str], int]`, not a widget call. See **The sizing rules**. |
| `workspaces/manager.py` | Central controller — CRUD + start/stop lifecycle, and the only `WorkspaceObserver` publisher (`add_observer` / `remove_observer`, emitting *after* each commit). Orchestrates compose rendering, Docker, migrations, owner bootstrap, DB credentials, workflow import and **Git synchronization** (auto-pull on start, auto-push on close). States run RUNNING → **STOPPING** → STOPPED, with `docker down` between the two writes. `workspace_branch()` gives every repo `dev`. `install_server()` / `publish()` implement the remote deployment; `server_health` / `server_logs` / `server_deploy_status` / `server_execution_status` are the read-only supervision reads. `ensure_serving()` is the "open this instance" path: it asks **Docker** (`live_state`), not the persisted state, whether a stack is up — so a workspace started outside the launcher, or before a crash that left `STOPPED` behind, is opened rather than started twice — and returns `Reachable(workspace, started)` so the caller knows whether the window on screen can still be alive. |
| `workspaces/close.py` | `CloseSequence`: the ordered shutdown (reconcile → export → git sync → stop, per workspace), with every decision injected as a `CloseHooks` callback (`on_progress`, `on_sync_failed`, `on_stop_failed`, `on_push_failed`) so it runs headless and is testable without a window. |
| `workspaces/dialogs.py` | What the creation and Git forms *collect*, not how they looked: `CreatePlan`, `GitConfigChoice`, `GitClonePlan`, `GitHubCreatePlan`, `GitHubRepoPick`, `GitHubTokenPlan`, plus `fresh_managed_db_config`, `default_creation_db` (managed when `has_db_layout()` — a `db/schema.sql` or a `db/migrations/*.sql`), `int_or` and `repo_name_from`. |
| `workspaces/ci.py` | The CI harness for a workspace's *own* repo: remote-URL → `owner/repo` parsing, workflow discovery (mirrors import — `n8nPipelines/*.json` + root `*.json` minus a blocklist, **deduped by basename in favour of `n8nPipelines/`**), the machine-managed `.n8n-tests/tests.json` selection, and `render_harness()`, which emits the workflow YAML plus a stdlib-only `validate.py` / `runner.py` (marker-commented, image pinned via token substitution). It also holds the two rules with no caller left: `workflow_eligibility(export, credentials)` and `load_export`. `save_ci_selection()` does **not** enforce the rule — it writes whatever the caller ticked, which is why a caller that can tick has to ask. |
| `docker/compose.py` | Renders Compose YAML. Each workspace gets its own project `n8n-ws-<id>`; named volumes `n8ndata-<id>` and, in managed mode, `pgdata-<id>`. Handles managed Postgres and `DbMode.NONE` (no DB service). `render_remote_compose` emits a top-level `name:` so the project is stable on the server. |
| `docker/manager.py` | Thin subprocess wrapper around `docker compose`, with `-p <project>` and `-f <file>` for isolation. `list_project_states()` reads `Labels` through `parse_container_labels()`, which accepts the three shapes the CLI produces — an object, a JSON string, and (Compose 2.35 on Docker Desktop) a flat `k=v,k=v` string. `resolve_docker_command()` probes the macOS install locations before falling back to `PATH`. |
| `core/config.py` | SQLite config store (`launcher.db`, WAL). `load()` / `mutate(fn)` are safe under threads *and* processes (`BEGIN IMMEDIATE` write serialization + 10 s busy timeout; readers use the WAL). The connection is cached for the process lifetime, so `close()` exists to release it — `__main__`'s `finally` and `gui/app.py::self_test()` both call it, and on Windows an open handle makes `launcher.db` undeletable. A legacy `config.json` is migrated in once when the database is missing; the database and its `-wal` / `-shm` sidecars are chmodded `0o600` on non-Windows. |
| `core/filelock.py` | Cross-platform `FileLock` (fcntl/shared on POSIX, `msvcrt.locking` on Windows), `acquire_single_instance_lock()`, and the reentrant per-workspace `workspace_git_lock` (`.n8n-launcher.git.lock`, gitignored) used around git lifecycle calls and publish. |
| `core/paths.py` | `platformdirs` paths for config, logs and runtime under `n8n-launcher`. Also exports `compose_file(workspace_id)`. |
| `core/models.py` | Dataclasses: `DbMode` (`NONE` / `MANAGED`), `DbConfig`, `GitConfig`, `ServerConfig`, `WorkspaceState`, `Workspace` (with `postgres_image`, `postgres_preload_timescaledb`, `n8n_version`, `git`, `server`), `AppConfig`. |
| `n8n/api.py` | Small HTTP client for the n8n **public** API (workflows, credentials). Used by owner bootstrap and workflow sync. |
| `n8n/owner.py` | Owner bootstrap through the internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`), retrying through the transient HTML n8n serves while starting. Also holds `hash_owner_password()` (bcrypt), the last-launcher use of the `bcrypt` dependency. |
| `n8n/scopes.py` | `REQUIRED_WORKFLOW_SCOPES` — the one scope list, shared by the local bootstrap and by the generated remote `deploy.py` through the `__API_KEY_SCOPES__` token. It is a contract with the n8n version (`400 Invalid scopes for user role` for anything it does not grant), so it must never be duplicated. |
| `n8n/workflows.py` | `SyncRunner`: `import_all()` creates in n8n every workflow JSON found in the workspace folder; `export_all(mirror=…)` refreshes `n8nPipelines/` exports and optionally mirrors identical copies at the workspace root; optional background sync thread. |
| `git/manager.py` | Git operations for workspace workflow synchronization, wrapping `subprocess.run` calls to `git` (init, clone, add, commit, push, pull, remote management) in the workspace's `workflows_dir` with a 30 s timeout. `workspace_branch(id)` is `dev` for every repo; `git_init` seeds that branch; `git_push` retries once on server-side rejection; `tokenize_remote_url` builds the one-shot GitHub push URL. |
| `github/api.py` | GitHub REST client: `github_owner()`, `create_repo()`, `list_workflow_runs()`, `list_run_jobs()`, `fetch_job_logs()`, `dispatch_workflow()`; typed `GitHubError` with `status_code`. Repo paths keep a **literal** slash (`repos/owner/repo/...`) — `repo_url_path()` only encodes the two segments, because URL-encoding the slash makes every Actions endpoint 404. |
| `github/auth.py` | Resolves the GitHub token without manual setup: `resolve_github_token(configured=None)` tries the optional persisted override, then `gh auth token`, then `git credential fill` for `github.com` (the same credential `git push` uses), with a 10 s timeout and interactive prompts disabled. Returns `None` when nothing is available; never logs or persists on its own. |
| `database/credentials.py` | Auto-creates n8n DB credentials; `data_db_target()` resolves the per-workspace DB target. |
| `database/layout.py` / `database/migrations.py` | Migration detection and runner (executes SQL via `docker compose exec psql`). Launcher-local: the generated remote `deploy.py` never runs them. |
| `monitoring/` | Structured event journal: `events.py` (`Event`), `store.py` (`EventStore`, SQLite `logs_dir()/events.db`, 30-day retention, `latest_events` / `search_events` / `export_events`), `redaction.py` (secret-shaped values are masked before they are persisted) and `bootstrap.py` (`bootstrap_logging`, `capture_exceptions`, `sys` / `threading` excepthooks). |
| `platform/ports.py` | Port availability check (`is_port_available`) and automatic suggestion (`suggest_port`) for n8n instances. |
| `platform/update_flow.py` | `UpdateController`: the release check → download → install order, with every question injected as a constructor callback (`set_status`, `on_offer`, `on_error`, `on_status_link`, `schedule`, `is_closed`, `finish_close`) plus the `events` queue results are routed back on. All the network and filesystem work is `platform/updater.py`; what lives here is what happens *around* it. |
| `remote/ssh.py` / `remote/deploy.py` | Remote server deployment and bounded observability: `ssh_run` / `scp` plumbing, atomic `write_remote_file` (`cat > path.tmp && mv -f`), the generated `post-receive` hook + `deploy.py` (template strings, never f-strings), and the read-only health / logs / marker / execution-status reads. The hook composes with `-p $PROJECT`; both hook and script write `last-deploy.json` atomically; `deploy.py` imports workflows from `collect_workflow_files()` with root-mirror dedup. |

## The sizing rules

These outlived the interface and remain the rule for any future view: **a
column's width is its content, and the room is a budget the table absorbs** —
never a guess, never elastic, never a second rule a table can be laid out by. Two
things used to fight here: a column with little in it reserved the room of a long
one, and the leftover was handed to a single "flexible" column, so the column
that actually needed the space was the one the widget clipped.

- **The rule** (`column_widths`): each column takes the width of its widest
  heading or value plus the cell margin, clamped between a declared minimum and
  maximum. A column lands on its minimum when its content is narrower than the
  floor (an empty table still shows readable headings).
- **The same rule with a room** (`fit_budget`): when the room is known, the
  surplus above the columns' natural widths goes to the `flexible` column (capped
  at its maximum), and a room too short is taken away **proportionally**, never
  below any column's minimum. A room of `0` or `1` is what a widget reports
  before its first layout, so it means *unmeasured*: the columns keep their
  content widths. A room narrower than the sum of the minimums is the one case
  left cut — there is no further room to give back.
- `measure` is a **parameter**, a `Callable[[str], int]` — not a widget call. The
  old `gui/theme.py::text_measure(root, font)` resolved a named Tk font and fell
  back to a per-character estimate; whoever builds a view now supplies that, and
  measurement is short-listed to the 8 longest values per column
  (`_MEASURED_CANDIDATES`) so a 500-row table costs what a 5-row one costs.

What was deleted with the widgets is the half that pushed those widths onto real
objects — `ColumnFitter`, `WindowFitter`, `bind_ellipsize`, `bind_wraplength`,
`wrap_at` and the `ttk.PanedWindow` split. The measurements behind them are
recorded in `MIGRATION.md` and are worth reading before phase 2 rebuilds them:
`ttk.Treeview` sizes itself from its columns but does not invalidate the request
it handed its geometry manager (`configure(height=cget("height"))` does), a
paned window never re-reads a pane's request (`sashpos(i)` is the right edge of
pane `i`; the gap between panes is 12 px), `panes()` answers paths rather than
widgets, and a container is as wide as its widest child — so a prose label
beside a table silently widens the card.

## What a future view owes the journal

Three habits outlived the pages, and they are the reason the journal is still
trustworthy:

- **A page never did its own I/O.** It rendered what the host fed it and called
  back, which is what kept git / GitHub / SSH calls on background workers rather
  than in a paint handler. A future view has to keep that split.
- **Focus was lifecycle.** `on_show` / `on_hide` were the only thing that started
  and stopped a page's timers, and a snapshot that landed after its card was
  closed was cached but never rendered.
- **A subject's tokens are OR'd and case-sensitive.** `PageSubject` named every
  word that could carry one of its events, so a line matching any one of them was
  a line that subject was looking for. `EventStore.search_events()` is now the
  only filter, and it filters in SQL — there is no second in-memory
  implementation to disagree with it.

What went with the pages: `PageSubject` / `PageKind` / `log_page_event`
(`core/subjects.py`), the in-memory `filter_events` and every row formatter
(`monitoring/present.py`), the runs model (`workspaces/ci_runs.py`) and the
server snapshot model. `EventStore.search_events` / `export_events` and
`WorkspaceManager.server_health` / `server_logs` / `server_deploy_status` /
`server_execution_status` are the surviving path to all of it.

## Workflow import

- On `ensure_running()`, `_import_workflows()` scans `<workflows_dir>/n8nPipelines/`
  **and** the workspace folder root for `*.json` workflow exports and creates
  them in n8n via the public API.
- JSON files without a `nodes` key, unreadable files, and workflows already
  present (by id **or** name) are skipped.
- `_create_payload()` is a **whitelist** of the fields the public create schema
  accepts (`name`, `nodes`, `connections`, `settings`, `staticData`, `pinData`,
  `nodeGroups`, `projectId`, `parentFolderId`); `settings` defaults to `{}`.
  Anything else (`active`, `triggerCount`, `shared`, …) is dropped.

## Workflow export

- `SyncRunner.export_all(mirror=…)` refreshes every workflow export from n8n
  into `<workflows_dir>/n8nPipelines/` (`<name>-<id>.json`, `_safe_name` name
  mangling) and removes stale launcher-named files there.
- When *mirror* is given — the workspace root, on close, stop-with-sync and
  publish — the **same per-workflow bodies are written as identical copies at
  the root**, because import and the CI harness read both locations. Cleanup in
  the mirror is restricted to launcher-named files via `EXPORT_NAME_RE`
  (`.+-\d+\.json$`), so unrelated root files (`package.json`, hand-written
  exports with a different naming) are never touched.
- Because the root also holds mirror copies, **every consumer that scans both
  locations dedups by basename, preferring `n8nPipelines/`**:
  `collect_workflows()` in `workspaces/ci.py` and `collect_workflow_files()` in
  the generated remote `deploy.py`. Without this, the same workflow would be
  imported or uploaded twice (double runs, duplicate remote workflows).

## Git synchronization

Git is optional per-workspace and configured at creation (git init + optional
remote URL) or later by asking for a `GitConfigChoice`
(`workspaces/dialogs.py`). Persisted as `Workspace.git`
(`GitConfig(enabled, remote_url, branch, ci_enabled, ci_credentials)`).

- **Branch model**: every launcher-created repo works on its own branch **`dev`**
  (`workspaces/manager.py::workspace_branch`), including `git init`
  (`git -c init.defaultBranch=dev init`). Legacy `main` / `n8n/*` branches are
  renamed to `dev` once on the first `ensure_running()` and `GitConfig.branch`
  is persisted. `dev` is both the work branch and the CI/tests branch; `main`
  exists **only** as the production reference on the remote server's bare repo
  (`publish` maps `dev → main` with `git push <server-url> dev:main`). The
  GitHub Actions harness hooks pushes on `branches: ["dev"]` and `enable_ci`
  sets the repo default branch to `dev`.
- **Concurrency**: git lifecycle calls run under a **workspace git lock**
  (`core/filelock.py::workspace_git_lock`, a reentrant `FileLock` on
  `.n8n-launcher.git.lock`, gitignored), so auto-pull (start) and auto-push
  (close / publish / CI) never interleave. `git_push` resolves dense refs lazily,
  validates the branch, and retries on server-side push rejection (`! [rejected]`
  / stale info), re-reading `GitConfig.branch` each attempt.
- **GitHub repo creation**: `github/api.py` wraps the GitHub REST API
  (`github_owner()`, `create_repo()`, typed `GitHubError` with `status_code`).
  The caller collects what the call needs through
  `workspaces/dialogs.py::GitHubCreatePlan` (repo name from `repo_name_from`,
  private by default, a PAT from `resolve_github_token`). The token is **never
  persisted by this flow**: it is used once for the API call, then
  `git/manager.py::git_seed_remote()` pushes with a one-shot tokenized URL
  (`push <https://token>@github.com/...> <branch>` — no `-u`, so the token never
  lands in `.git/config`); the `remote_url` stored in `GitConfig` is the clean
  `https://github.com/<owner>/<name>.git` URL, keeping CI eligibility
  (`github_repo_path`) intact.
- **Token resolution (zero manual config)**: `github/auth.py::resolve_github_token()`
  is the single entry point used for both Actions runs reads and repo creation.
  It reuses the OS Git credential for `github.com` (the same one `git push`
  uses) or `gh auth token`, so nothing has to be configured. An optional
  override can be persisted once as `AppConfig.github_token` and then takes
  priority; the resolved token is otherwise kept in memory only. Repo creation is
  `workspaces/dialogs.py::GitHubCreatePlan`; runs are read through
  `github/api.py`.
- **Auto-pull on start**: before `_import_workflows()`, `git_pull()` (with
  `--rebase`) brings remote JSON changes into `workflows_dir`, only when
  `git.enabled` is set and the folder is a real repo. Failures are logged as
  warnings and never block startup.
- **Auto-push on close**: the close sequence (`CloseSequence`) exports n8n
  workflows to JSON (`export_all`, mirroring at the workspace root), then
  `WorkspaceManager.sync_git()` stages everything (`git add -A`), commits with a
  timestamped message, and pushes. The push is skipped when there is nothing to
  commit *and* no unpushed commits. Push failures are logged, never raised.
- `git_push` uses `-u origin <branch>` so the branch the launcher created (`dev`
  via `git init`) seeds a fresh (empty) remote on the first push.

## Remote server deployment

Deployment to a production server is optional per-workspace and configured via
"Configurer le serveur…" (persisted as `Workspace.server`, `ServerConfig`). All
implementation lives in `remote/` (SSH plumbing + generated listener) and
`workspaces/manager.py::publish`; the "Publier sur le serveur…" flow pushes the
workspace git repo to the server's bare repo and waits for the `post-receive`
hook to confirm the deployment.

- **Server setup** (`install_server`): probes the remote tools, fills the
  placeholders in the generated `hook` / `deploy.py`, ships them via
  `write_remote_file`, and **initializes the bare repository**
  (`git init --bare <base>/<id>.git`) before writing the `post-receive` hook —
  the hook must exist at push time. Repo paths are `shell-quoted` everywhere.
- **Compose on the server**: `render_remote_compose` embeds a top-level
  `name: n8n-ws-<id>` (so the Compose project is stable on the server, not
  derived from a directory name) and a trimmed stack driven by
  `ServerConfig.n8n_port`; DB / env / secrets come from `secrets.json` scp'd at
  publish.
- **Hook protocol**: the generated `post-receive` hook runs
  `docker compose -f "$WORKFLOW/compose.yml" -p "$PROJECT" up -d` (the remote
  Compose file is committed into the pushed repo and unpacked in the checkout;
  `-p` matches `render_deploy_script`'s `PROJECT = os.environ["DEPLOY_PROJECT"]`),
  executes `deploy.py`, and records the result in `last-deploy.json`
  (`{sha,status,error,at}`) written **atomically** (`.tmp` + `os.replace`). The
  hook starts with `cd "$HOME"`: git runs hooks with the cwd set to the bare
  repo while every path (`BASE`, `BARE`, `WORKFLOW`, …) is home-relative.
  Content is a template string — never an f-string.
- **`deploy.py`**: unpacks `main` into the checkout dir, recreates credentials
  and imports workflows with `collect_workflow_files()` — same layout as import,
  and with the same **basename dedup against `n8nPipelines/`** so the publish
  root-mirror copies never upload a workflow twice. It deliberately **never
  runs `db/migrations/*.sql`**: those stay on the launcher-managed local stack
  and are applied by `database/migrations.py` via `docker compose exec psql`.
  It receives the API-key scopes through the `__API_KEY_SCOPES__` token rather
  than redeclaring them.
- **Activation is best effort, through the owner session**: the public
  `POST /api/v1/workflows/{id}/activate` is refused by n8n 2.x for a workflow the
  key's user cannot activate ("ask the owner to share it with you"), so
  `activate_workflow()` goes through the deploy's owner session —
  `GET /rest/workflows/{id}` for the `versionId` (n8n wraps it in `{"data": …}`;
  `unwrap()` reads both shapes, which is also why the create/upsert ids must be
  read through it), then `POST /rest/workflows/{id}/activate`. Creating the API
  key revokes the deploy session, so the lookup re-logs in and retries
  (`VERSION_LOOKUP_ATTEMPTS` / `_DELAY`) before falling back to the key. A
  refusal is **logged, never raised**: a manual-only export can never be
  activated on n8n 2.x, and the import has already succeeded.
- **Publish push timeout**: the server's `post-receive` hook runs Compose up +
  the import synchronously, so `publish` pushes with
  `timeout=_PUBLISH_PUSH_TIMEOUT` (300 s) instead of the 30 s every other git
  call gets.
- **Atomic remote writes**: `remote/ssh.py::write_remote_file` writes via
  `cat > '<path>'.tmp && mv -f` so a failed transfer never leaves a truncated
  generated file.
- **Publish flow** (`publish`): exports workflows (mirror at root), commits any
  dirty change, pushes `dev:main` to the server's bare repo (the server `main` is
  the production reference — a rejected push raises a clear French error), polls
  the marker until the `sha` matches the pushed HEAD, then records
  `server_last_error` / `server_last_deploy` on the workspace. A disabled
  `ServerConfig` blocks publish.

## GitHub Actions CI

CI is optional per-workspace, requires `git.enabled` with a **GitHub** remote
(`https://github.com/…`, `git@github.com:…`), and persists as
`GitConfig.ci_enabled` / `GitConfig.ci_credentials` (name + type metadata only).
All runtime logic lives in `workspaces/ci.py`, reached through `enable_ci()`,
`disable_ci()`, `set_ci_credentials()` and `ci_credentials_payload(id)`; runs
are read through `github/api.py`.

- **Enable** (`WorkspaceManager.enable_ci`) requires a real repo + GitHub
  remote, writes `.github/workflows/n8n-ci.yml` and `.n8n-tests/validate.py` /
  `runner.py` via `render_harness()`, commits and pushes, then sets the repo
  default branch to `dev`. Disable (`disable_ci`) deletes the three generated
  files but **keeps** `.n8n-tests/tests.json` so the selection survives a
  disable/enable cycle.
- **Generated files are marker-commented** — `render_harness()` emits a
  `GENERATED_MARKER` ("n8n-launcher : généré — ne pas modifier à la main.")
  comment and pins the n8n image by substituting the `__N8N_IMAGE__` token
  (`docker.n8n.io/n8nio/n8n:<version>`, `_safe_image_tag` falls back to
  `2.40.0`). Templates are plain strings — never f-strings, so braces survive
  verbatim.
- **Workflow** (two jobs): `validate` runs `validate.py` (JSON shape, duplicate
  names, selection consistency); `test` runs `runner.py` with `N8N_IMAGE`
  (repository variable, defaulted to the pinned version) and the
  `N8N_CI_CREDENTIALS` secret. Run triggers: `workflow_dispatch` plus push on
  `branches: ["dev"]` — the per-workspace branch every launcher repo lives on.
- **Eligibility** (`ci.workflow_eligibility`): a pipeline is testable iff it has
  a manual trigger, a schedule trigger, or a *pinned* webhook/chat trigger
  (`pinData` on the trigger), and every non-pinned node's credential types are
  covered by the `ci_credentials` metadata. Rationales are French,
  human-readable strings returned with the verdict, so any caller can explain the
  refusal instead of only greying a row out.
- **Selection** is machine-managed: `tests.json` is
  `{"selected": ["n8nPipelines/x.json", …]}` — never hand-edit. A caller that can
  tick a pipeline is the one that must ask `workflow_eligibility` first, because
  `save_ci_selection()` writes whatever it is handed. Any checkbox markers must
  stay ASCII (`[x]` / `[ ]`, `-` for ineligible): box glyphs ☑ / ☐ are absent
  from Linux UI fonts.
- **Manual run**: `GitHubClient.dispatch_workflow(repo, file, ref=…)` (`POST
  .../actions/workflows/<name>/dispatches`, success = 200/201/202/204; `<name>`
  must be the bare `n8n-ci.yml` — GitHub matches on the file name, not the
  `.github/workflows/` path, so `github/api.py::workflow_name()` strips it). The
  caller chooses the ref, and decides the thread it runs on.
- **Credentials**: `set_ci_credentials()` persists name/type metadata only;
  `ci_credentials_payload(id)` (**requires a started workspace + api_key**)
  returns the name/type names *and* the values, read once via
  `api.get_credential(id).data` — the JSON the caller hands to the
  `N8N_CI_CREDENTIALS` GitHub secret. The generated `runner.py` keeps
  `OWNER_EMAIL=ci@n8n-launcher.test` / `OWNER_PASSWORD=CiPassw0rd-n8n9`, imports
  exports via `POST /api/v1/workflows` (dropping server-only fields), recreates
  credentials, triggers via `POST /rest/workflows/:id/run` (ManualRunDto), polls
  `GET /rest/executions/:id`, and exits nonzero on any failure. A missing secret
  at runtime is a credential failure, never a crash.
- **Runner auth (gotcha sévère)**: the internal `/rest/*` calls authenticate via
  the `n8n-auth` session cookie set by `POST /rest/login` — n8n never returns a
  body token, and `Authorization: Bearer <JWT>` is rejected with 401. Because
  urllib / requests refuse to replay a cookie flagged `Secure` over plain HTTP,
  the runner starts its disposable container with `N8N_SECURE_COOKIE=false` (the
  same setting as the managed Compose containers — without it
  `POST /rest/api-keys` 401s on every version). Listing credentials through the
  public API additionally requires the `credential:list` scope (not just
  `credential:read` / `credential:create`, or `GET /api/v1/credentials` returns
  403 « Forbidden »); a secret set to `[]` is a no-op without any API call.
  `trigger_run` accepts both a flat `{"executionId": ...}` and
  `{"data": {"executionId": ...}}` response.

## Gotchas

- **n8n 2.33.x integration quirks**: owner bootstrap uses internal REST
  endpoints (not `N8N_INSTANCE_OWNER_*` env vars). API keys require a `scopes`
  array and a numeric `expiresAt`. Startup returns transient HTML until n8n is
  ready — the bootstrap retries until it gets JSON. The scope list is shared with
  the generated deploy script through `n8n/scopes.py`, never duplicated.
- **`/healthz` is liveness, not readiness.** Measured on n8n 2.40 after a
  container start: `/healthz` answers 200 at ~2.8 s while `GET /` still returns
  HTTP 404 `Cannot GET /`, and only at ~4.7 s does `/healthz/readiness` go 200 —
  the exact moment the frontend handler is mounted and `/` serves the 52 KB
  editor shell. Anything that opens the UI (or calls the API) must gate on
  `/healthz/readiness`, which is what `n8n/owner.py::wait_for_n8n` polls;
  gating on `/healthz` produces a browser window the user cannot use. An n8n
  without that route answers 404, and the wait falls back to the UI root.
- **Reopening a browser window is not a URL problem.** Measured on Chromium
  154: `--app=<same URL>` twice leaves **two** windows, because n8n routes
  client-side (a fresh window on `/` ends on `/signin?redirect=%252F` with no
  HTTP redirect at all) and Chromium matches the URL it is given against the
  window's *current* URL. So `gui/browser.py` does not track URLs: it opens
  windows in a launcher-owned profile (`core/paths.py::browser_profile_dir`),
  reads the port Chromium published in `DevToolsActivePort`
  (`--remote-debugging-port=0`), and matches the instance's **origin** in
  `/json/list` before calling `/json/activate/<id>` ("Target activated"), which
  raises that window whatever page of the SPA it is showing. Two consequences
  worth keeping: the n8n session cookie lives in that profile, so reopening no
  longer asks for a sign-in every time; and `reuse=False` (a workspace that was
  just started) closes the window from the instance's previous life, which can
  only be showing the connection that died with it. The DevTools endpoint binds
  loopback only and exposes a profile that already sits, in readable form, in the
  user's own config directory — that is the whole security delta. Two races are
  handled as "not ready": the port file appears slightly before the endpoint
  accepts, and a crashed browser leaves the file behind; both fall back to
  launching a window. Chromium's stderr is `DEVNULL` on purpose — its GTK and
  GCM chatter would otherwise land in the launcher's journal.
- **Public API is schema-strict**: `POST /workflows` validates with
  `additionalProperties: false`; sending read-only/server export fields in the
  body returns HTTP 400 "must NOT have additional properties". Only the
  whitelisted create fields survive (`_create_payload`).
- **Password policy is a mirror, not the source of truth.**
  `core/first_launch.py::validate_password` enforces n8n 2.40's rule (8 to 64
  chars, at least one digit and one uppercase letter) so the wizard can refuse
  early. If n8n ever changes it, both change together: the launcher's copy only
  saves the user a round trip, n8n still has the last word.
- **First launch and the unreadable config are two different branches.**
  `store.load()` raising `ConfigError` means either « absent » (wizard) or
  « present and broken » (back up and stop). The discriminator is
  `store.path.exists()`: a wizard may never run on top of a config it could not
  read, and the test pins that ordering.
- **A missing Qt platform plugin fails silently.** The one packaging failure a
  user sees as « the app does nothing ». `verify_qt_bundle()` runs at the end of
  every build (and as `scripts/build.py --verify <root>` in CI), and
  `n8n-launcher --self-test` is run on the finished artifact on all three OS.
- **Managed DB**: uses the local `postgres` service in Compose. `DbMode.NONE`
  omits the Postgres service entirely; legacy `"external"` configs fall back to
  `NONE` on load.
- **Named volumes required**: each workspace declares both `n8ndata-<id>` and
  (managed mode) `pgdata-<id>` as named volumes.
- **Managed DB auto-generation**: if a workspace uses `DbMode.MANAGED` without
  explicit DB params, `WorkspaceManager` auto-generates `database_name`,
  `username` and a random `password`.
- **`docker ps` labels are not always a map**: `list_project_states()` reads
  `Labels` through `parse_container_labels()`, which accepts the three shapes
  the CLI produces — an object, a JSON string, and (Compose 2.35 on Docker
  Desktop) a flat `k=v,k=v` string. Reading only the first two made every
  workspace look stopped on such a host.
- **Config file location**: config lives in a SQLite database at
  `platformdirs.user_config_dir("n8n-launcher")/launcher.db` (WAL; a legacy
  `config.json` is migrated in once). Compose files go under
  `config_dir/workspaces/<id>/compose.yml`.
- **CI secrets**: n8n credential values never persist in the launcher — only
  `(name, type)` metadata in the launcher config; the values are handed once to
  the `N8N_CI_CREDENTIALS` GitHub secret.
- **Windows config permissions**: the `0o600` chmod is skipped on Windows
  (`os.name == "nt"`).
- **macOS PATH in bundle-launched apps**: Finder / Dock / Launchpad start bundles
  with a minimal `PATH`, so `docker` must be found via `resolve_docker_command()`
  (probing `/opt/homebrew`, `/usr/local`, Docker Desktop) instead of relying on
  the environment.
- **macOS build tooling**: `build.py` uses `sips` / `iconutil` (icns),
  `patch_info_plist` and `dmgbuild` (dmg). The `dmgbuild>=1.6,<2` dependency is
  darwin-only in the `packaging` extra; PyInstaller lives in the `packaging`
  extra too (not in runtime deps).
- **Entry point**: PyInstaller builds target the thin root `run.py` with
  `--paths src` (not `src/n8n_launcher/__main__.py`) so the package keeps its
  relative imports.
- **Monitoring bootstrap**: `__main__._start_monitoring()` wraps
  `bootstrap_logging()` in a `contextlib.suppress(Exception)`: an unwritable log
  directory must never stop the launcher (it falls back to stderr only). The
  store is closed in `main()`'s `finally`, so handlers must not outlive it.
- **Session boundaries in the journal**: every run is bracketed —
  `Surveillance active` (with the store path and retention) and
  `Surveillance terminée — session de h:mm:ss, N workspace(s) en cours à la
  fermeture`. The closing event is emitted by `__main__._close_session()`, which
  runs in `main()`'s `finally` **before** `monitor.close()` and **re-runs the
  `stop_all` shutdown itself**: `atexit` handlers fire *after* that `finally`,
  i.e. once the store is closed, so their `Stopped <name>` records are dropped
  by the monitoring handler (visible only as a `--- Logging error ---` on
  stderr). Doing the shutdown in the `finally` is what puts the teardown *and* the
  closing event in the journal, in causal order; the `atexit` registration is a
  fallback for the exits that never unwind `main`. A session that could not read
  its config is still bracketed (count reported as 0), otherwise a hard kill
  would be indistinguishable from a launcher still running.
- **The journal is the whole observability surface now.** The panel that used to
  read it is gone, and so is the in-memory `filter_events` it filtered with —
  `EventStore.search_events` is the only filter, and it filters in SQL, so there
  is nothing left to disagree with it. What made the panel trustworthy and is
  still worth keeping as a rule for any future consumer: the level was part of
  the searched text (typing `ERROR` filtered by severity with no level widget),
  and a focused view added its `PageSubject` tokens on top — case-sensitive,
  OR'd among themselves and AND-combined with the free text.
  `EventStore.search_events` / `export_events` (30-day retention, redaction
  before write) are the direct path to that history.
- **Server supervision is read-only**: `WorkspaceManager.server_health`,
  `server_logs`, `server_deploy_status` and `server_execution_status` delegate to
  `remote/ssh.py` (`docker compose ps`, bounded `docker compose logs`,
  `last-deploy.json` + history, the generated status command). Every value is
  redacted and bounded upstream, each read failure degrades to an empty section
  instead of raising, and no read exposes the public n8n port. A view fed from
  these runs the four reads in one background worker and drops a snapshot whose
  workspace the selection has left.
- **Remote capability flag**: `REMOTE_STATUS_CAPABILITY = "__N8N_LAUNCHER_STATUS_V1__"`
  is the opt-in for the remote execution-status command; a server whose
  `deploy.py` predates it reports "unsupported" instead of failing.
- **Execution `finished` is derived, never sent**: n8n 2.40's
  `GET /rest/executions` returns `id` / `status` / `workflowName` / `startedAt` /
  `stoppedAt` and **no `finished` flag** (verified against a real 2.x instance in
  `tests/integration/test_remote_execution_status.py`). The launcher derives it
  from n8n's own vocabulary —
  `TERMINAL_EXECUTION_STATUSES = {canceled, crashed, error, success}` ⇒ `True`,
  `PENDING_EXECUTION_STATUSES = {new, running, waiting, unknown}` ⇒ `False`
  (upstream deliberately leaves `waiting` / `unknown` non-terminal; recovery may
  still rewrite an unknown execution to `crashed`), anything else ⇒ `None`, and
  an explicit boolean from the payload always wins. Both sets live in
  `remote/deploy.py`, are injected into the generated script through the
  `__TERMINAL_STATUSES__` / `__PENDING_STATUSES__` tokens, and a meta-test
  asserts the generated parser and the launcher parser return the same verdict
  for every status. A caller has to sort on `startedAt` (newest first) because
  the remote order is not a contract.
