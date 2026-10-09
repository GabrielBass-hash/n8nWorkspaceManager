# docs/architecture.md — the module map and a gotchas index

This file exists so that `AGENTS.md` stays short enough to be useful on every
turn. It holds the module map — *where does this behaviour live* — and an index
of the facts measured on a real host, which you ask once per task, not once per
edit.

**Read this file when** you are about to touch `n8n/`, `remote/`, `docker/`,
`git/`, `github/`, `database/`, `monitoring/`, `platform/`, or the three GUI
modules that own the card gestures — `gui/browser.py`, `gui/window.py` and
`gui/board.py`. **Do not read it** to answer "how do I run the tests" or "what is
the packaging story" — that is `AGENTS.md` §Commands, or `README.md`
§Distribution.

Nothing here is a summary of the source. The module map is maintained here;
each package's ``src/n8n_launcher/*/__init__.py`` docstring carries its
one-line digest (role, entry, headline gotcha) so the navigation ladder can
answer a role question without opening this file. The measured gotchas are
**not** maintained here: their full text lives in the docstring or comment of
the symbol it concerns, and the index below points at each one — the fact and
the code cannot drift apart.

---

## Architecture

| Module | Role |
|---|---|
| `__main__.py` | Entry point. `main(argv=None)` short-circuits on `--self-test` before touching the config, the lock or Docker; then single-instance lock → `resolve_docker_command()` → journal → wizard (no config) or `run_gui()` → ordered shutdown. Brackets every run: `_start_monitoring()` logs `Surveillance active`, `_close_session()` logs `Surveillance terminée` (duration + how many workspaces were still up) **before** `monitor.close()`. A config that exists but cannot be read is backed up to `launcher.db.corrupt-<ts>` and the run stops; a config that does not exist opens the wizard. |
| `gui/app.py` | The shell: `ensure_application`, `display_available`, `GuiUnavailable`, `request_shutdown`/`shutdown_requested`/`clear_shutdown`, `LauncherApp.run()`, `run_gui()`, `SELF_TEST_FLAG`, `self_test()`. `run()` skips `exec()` when a shutdown was already requested; the `SIGNAL_TICK_MS = 200` `QTimer` is what lets a Python signal handler run at all while Qt's loop is parked in C++. `self_test()` returns an exit code — it is what CI runs on a finished artifact. |
| `gui/window.py` | `MainWindow(manager)`: header (*Nouveau* / *Arrêter*, objectNames `new`/`stop`), `QStackedWidget` board + empty state, the `WorkspaceBoard` and its three gestures (double-click = open, status pill = `primary_action`, right-click = `card_actions`), selection sync restored across a reload, `workspace_actions` (not `actions` — `QWidget.actions` already exists). |
| `gui/board.py` | `WorkspaceBoard(QListView)`: the wrapping IconMode grid, the pill hover (cursor, tooltip, accent ring) and the pill hit-test. Hit-testing lives here rather than in the window because it must agree with what the delegate painted. Emits `pillActivated` / `contextRequested`; decides nothing. |
| `gui/card_delegate.py` | `CARD_WIDTH` / `CARD_HEIGHT` / `CARD_SIZE`, `tone_color`, `card_rect`, `pill_rect`, `pill_hit`, `CardDelegate`: paints one card (status pill, port, folder) and the hover ring. Geometry and colours only, no widget state. |
| `gui/workspace_model.py` | `WorkspaceListModel` (roles `WorkspaceRole` / `StatusRole`, `refresh`, `apply_workspace`, `workspace_at`, `status_at`, `index_of`). Qt annotates `rowCount`/`data` with `QModelIndex \| QPersistentModelIndex`; the overrides must be widened to match or basedpyright fails. |
| `gui/actions.py` | `WorkspaceActions(manager, executor=…)`: signals `busyChanged` / `failed` / `created`, one `_Task(QRunnable)` per call on the global `QThreadPool`. The executor is injected, so every test runs synchronously. |
| `gui/browser.py` | Opens a workspace's n8n as a standalone Chromium window, or **raises the window it already has**: `open_web_app(...) -> WebAppOutcome` (`OPENED` / `RAISED`) asks DevTools for a match and never compares URLs. Chromium's stderr is `DEVNULL` on purpose. |
| `gui/create_panel.py` | `plan_from_fields(name, managed) -> CreatePlan` and `CreateWorkspaceDialog` (objectNames `name` / `directory` / `managed`). It collects; it does not create — the plan goes to the action layer. |
| `gui/notifier.py` | `WorkspaceNotifier`: turns manager callbacks into Qt signals. |
| `gui/theme.py` | Palette, metrics, `STYLESHEET`; `dark_palette()`, `apply_theme(app)`. |
| `gui/first_launch.py` | `FirstLaunchWizard` (objectNames `email` / `password` / `work_dir` / `hint`) and `prompt_first_launch(store, docker, parent=None) -> bool`, which raises `GuiUnavailable` when there is no display. The rules it enforces live in `core/first_launch.py`. |
| `core/first_launch.py` | Toolkit-free wizard contract: `SetupWizardError`, `validate_password`, `build_initial_config`, `run_first_launch` (refuses unless Docker is available, then writes the config). |
| `gui_utils/text.py` | The sizing rules, toolkit-free: `ELLIPSIS`, `ellipsize`, `column_widths` (the pure rule) and `fit_budget` (the same rule with a room). `measure` is a **parameter** — `Callable[[str], int]` — not a widget call. |
| `gui_utils/responsive.py` | The responsive contract, toolkit-free: `MIN_WINDOW_WIDTH` / `MIN_WINDOW_HEIGHT` (1024×640), `PREFERRED_*`, `Viewport`, `Rect`, `initial_window_size`, `assert_rect_within`, `responsive_failure`. Views consume the values; `tests/unit/gui_utils/test_responsive_contract.py` asserts them without Qt. |
| `workspaces/manager.py` | Central controller: CRUD + start/stop lifecycle and the only `WorkspaceObserver` publisher (`add_observer` / `remove_observer`, emitting *after* each commit). States run RUNNING → **STOPPING** → STOPPED with `docker down` between the two writes. Orchestrates compose rendering, Docker, migrations, owner bootstrap, DB credentials, workflow import and Git sync (auto-pull on start, auto-push on close). CI surface: `enable_ci`, `disable_ci`, `set_ci_credentials`, `ci_credentials_payload`, `save_ci_selection`. Read-only supervision: `server_health`, `server_logs`, `server_deploy_status`, `server_execution_status`. `ensure_serving()` is the "open this instance" path: it asks **Docker** (`live_state`), not the persisted state, and returns `Reachable(workspace, started)`, so a stack started outside the launcher (or before a crash that left `STOPPED` behind) is opened rather than started twice. |
| `workspaces/status.py` | What a card shows and which actions it offers: `StatusTone`, `WorkspaceAction`, `CardAction`, `WorkspaceStatus`, `status_for`, `state_label`, `summary_line`, `can_start` / `can_stop` / `can_delete` / `can_open`, `primary_action` (what the status pill does) and `card_actions` (the right-click menu). A view renders this; it never re-derives a label, a tone or a rule. |
| `workspaces/close.py` | `CloseSequence`: the ordered shutdown (reconcile → export → git sync → stop, per workspace) with every decision injected as a `CloseHooks` callback (`on_progress`, `on_sync_failed`, `on_stop_failed`, `on_push_failed`), so it runs headless and is testable without a window. |
| `workspaces/dialogs.py` | What the forms *collect*, not how they looked: `CreatePlan`, `GitConfigChoice`, `GitClonePlan`, `GitHubCreatePlan`, `GitHubRepoPick`, `GitHubTokenPlan`, plus `fresh_managed_db_config`, `default_creation_db` (managed when `has_db_layout()` — a `db/schema.sql` or a `db/migrations/*.sql`), `int_or` and `repo_name_from`. |
| `workspaces/ci.py` | The CI harness for a workspace's *own* repo: `github_repo_path` (remote URL → `owner/repo`), workflow discovery (`collect_workflows`: `n8nPipelines/*.json` + root `*.json` minus a blocklist, **deduped by basename in favour of `n8nPipelines/`**), `load_export`, `missing_credentials`, `workflow_eligibility`, `save_ci_selection`, and `render_harness()` which emits the workflow YAML plus a stdlib-only `validate.py` / `runner.py` from the template documents in `workspaces/templates/` (`GENERATED_MARKER`-commented, image pinned through the `__N8N_IMAGE__` token). |
| `docker/compose.py` | Renders Compose YAML. One project per workspace, `n8n-ws-<id>`; named volumes `n8ndata-<id>` and, in managed mode, `pgdata-<id>`. Handles managed Postgres and `DbMode.NONE` (no DB service at all). `render_remote_compose` emits a top-level `name:` so the project is stable on the server. |
| `docker/manager.py` | Thin subprocess wrapper around `docker compose`, always with `-p <project>` and `-f <file>` for isolation. `list_project_states()` reads `Labels` through `parse_container_labels()`, which accepts the three shapes the CLI produces (object, JSON string, and the flat `k=v,k=v` string Compose 2.35 emits on Docker Desktop). `resolve_docker_command()` probes the macOS install locations before falling back to `PATH`. |
| `core/config.py` | SQLite config store (`launcher.db`, WAL). `load()` / `mutate(fn)` are safe under threads *and* processes (`BEGIN IMMEDIATE` write serialization + 10 s busy timeout; readers use the WAL). The connection is cached for the process lifetime, so `close()` exists — `__main__`'s `finally` and `gui/app.py::self_test()` both call it, and on Windows an open handle makes `launcher.db` undeletable. A legacy `config.json` is migrated once when the database is missing; the database and its `-wal` / `-shm` sidecars are chmodded `0o600` on non-Windows. |
| `core/filelock.py` | Cross-platform `FileLock` (fcntl/shared on POSIX, `msvcrt.locking` on Windows) and `acquire_single_instance_lock()`. |
| `core/models.py` | Dataclasses: `DbMode` (`NONE` / `MANAGED`), `DbConfig`, `GitConfig`, `ServerConfig`, `WorkspaceState`, `Workspace`, `AppConfig`. |
| `core/paths.py` | `platformdirs` paths for config, logs and runtime under `n8n-launcher`; `compose_file(workspace_id)` and `browser_profile_dir()`. |
| `core/templates.py` | `read_template(package, name)` — loads the `__TOKEN__` template documents (`remote/templates/*.tmpl`, `workspaces/templates/*.tmpl`) through `importlib.resources`, at import time of the calling module. `scripts/build.py` ships them with `--add-data` and asserts their presence in `--verify`. |
| `n8n/api.py` | Small HTTP client for the n8n **public** API (workflows, credentials). |
| `n8n/owner.py` | Owner bootstrap through the internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`), retrying through the transient HTML n8n serves while starting. `wait_for_n8n()` polls readiness. Also `hash_owner_password()` (bcrypt). |
| `n8n/scopes.py` | `REQUIRED_WORKFLOW_SCOPES` — the one scope list, shared by the local bootstrap and by the generated remote `deploy.py` through the `__API_KEY_SCOPES__` token. It is a contract with the n8n version; never duplicate it. |
| `n8n/exports.py` | Export/import planning for workflow JSON: `plan_import` / `plan_export`, the `SKIP_*` reasons, `export_filename` / `parse_export_id`, and `WORKFLOW_KEYS` — the whitelist the public create schema accepts (`additionalProperties: false`). |
| `n8n/workflows.py` | `SyncRunner`: `import_all()` creates in n8n every workflow JSON found in the workspace folder; `export_all(mirror=…)` refreshes `n8nPipelines/` exports and optionally mirrors identical copies at the workspace root; optional background sync thread. |
| `git/manager.py` | Git operations for a workspace's workflow synchronization, in the `workflows_dir`. `WORKSPACE_BRANCH = "dev"` and `workspace_branch(id)`; `git_init` seeds that branch. `workspace_git_lock(path)` is the reentrant per-workspace lock. `git_push` (`-u origin <branch>`, one retry on a server-side rejection). `git_seed_remote` + `tokenize_remote_url` build the one-shot tokenized GitHub push URL. |
| `github/api.py` | GitHub REST client: `github_owner()`, `create_repo()`, `list_workflow_runs()`, `list_run_jobs()`, `fetch_job_logs()`, `dispatch_workflow()`; typed `GitHubError` with `status_code`. Repo paths keep a **literal** slash (`repos/owner/repo/...`) because URL-encoding it makes every Actions endpoint 404. |
| `github/auth.py` | `resolve_github_token(configured=None)`: the persisted override, then `gh auth token`, then `git credential fill` for `github.com` (the same credential `git push` uses), 10 s timeout, prompts disabled. Returns `None` when nothing is available; never logs or persists on its own. |
| `database/credentials.py` | Auto-creates n8n DB credentials; `data_db_target()` resolves the per-workspace DB target. |
| `database/layout.py` / `database/migrations.py` | `has_db_layout()` and the migration detection + runner (executes SQL via `docker compose exec psql`). Launcher-local: the generated remote `deploy.py` never runs them. |
| `monitoring/` | Structured event journal: `events.py` (`Event`), `store.py` (`EventStore`, SQLite `logs_dir()/events.db`, `RETENTION_DAYS = 30`, `latest_events` / `search_events` / `export_events`), `redaction.py` (secret-shaped values masked *before* they are persisted) and `bootstrap.py` (`bootstrap_logging`, `capture_exceptions`, `sys` / `threading` excepthooks). |
| `platform/ports.py` | `is_port_available` and `suggest_port`. |
| `platform/update_flow.py` | `UpdateController`: the release check → download → install order, with every question injected as a constructor callback (`set_status`, `on_offer`, `on_error`, `on_status_link`, `schedule`, `is_closed`, `finish_close`) and queue results routed back through `events`. All the network and filesystem work is `platform/updater.py`, which also compares `__version__`. Per-platform assets are the macOS `.dmg`, the Linux onedir tarball (`n8n-launcher-linux.tar.gz`, with an `.AppImage` fallback) and the Windows onedir zip (`n8n-launcher-windows.zip`); `install_target()` returns the `.app` bundle on macOS and the onedir **install directory** elsewhere, and the generated helper scripts unpack the archive and swap that directory. |
| `remote/ssh.py` / `remote/deploy.py` | Remote server deployment and bounded observability: `ssh_run` / `scp` plumbing, atomic `write_remote_file`, the generated `post-receive` hook + `deploy.py` (template documents in `remote/templates/`, loaded by `core/templates.py`), and the read-only health / logs / marker / execution-status reads. `REMOTE_STATUS_CAPABILITY` and the execution-status sets live here. |

`git/__init__.py` and `remote/__init__.py` re-export their package's public
surface; import managers through them.

---

## Measured gotchas (index)

Facts verified against a real n8n 2.x / Docker Desktop / Chromium host. Do not
re-derive them by guessing. The full text lives at the symbol — that docstring
or comment is the source of truth; this index only says where to look.

- `n8n/owner.py::wait_for_n8n` — `/healthz` is liveness, `/healthz/readiness` is the gate (n8n 2.40 timings, 404 fallback).
- `n8n/exports.py::WORKFLOW_KEYS` — the public create schema is `additionalProperties: false`; only the whitelist survives.
- `n8n/owner.py::OwnerSetup` — `/rest/*` authenticates with the `n8n-auth` session cookie; a Bearer token is 401.
- `docker/compose.py::_render` — `N8N_SECURE_COOKIE=false` over plain HTTP, or every `/rest` call 401s (same in `workspaces/templates/runner.py.tmpl`).
- `n8n/api.py::list_credentials` — listing through the public API needs the `credential:list` scope, or it is 403.
- `workspaces/templates/runner.py.tmpl::trigger_run` — accepts both `{"executionId": …}` and `{"data": {"executionId": …}}`.
- `n8n/owner.py::_ensure_owner` — bootstrap drives the internal REST endpoints (not `N8N_INSTANCE_OWNER_*`), retried through the transient HTML; API keys need a `scopes` array and numeric `expiresAt`.
- `gui/browser.py::open_web_app` — reopening is a DevTools *origin* match, not a URL problem; two races fall back to launching a window.
- `scripts/build.py::verify_qt_bundle` — a missing Qt platform plugin fails silently; hence `--verify` in CI and `--self-test` on the artifact.
- `docker/manager.py::parse_container_labels` — `docker ps` Labels is not always a map (flat `k=v,k=v` since Compose 2.35).
- `docker/manager.py::resolve_docker_command` — macOS bundle launches get a minimal `PATH`: probe install locations, never bare name.
- `__main__.py::_backup_unreadable_config` — an unreadable config is moved aside and the run stops; an absent config (`store.path.exists()`) opens the wizard instead.
- `core/first_launch.py::validate_password` — a mirror of n8n's password policy, not the source of truth.
- `remote/templates/deploy.py.tmpl::activate_workflow` — activation is best effort through the owner session; a refusal is logged, never raised.
- `remote/deploy.py::TERMINAL_EXECUTION_STATUSES` — `finished` is derived from n8n's vocabulary, never sent by `/rest/executions`.
