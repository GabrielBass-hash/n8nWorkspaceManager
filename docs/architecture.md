# docs/architecture.md — the module map and the measured gotchas

This file exists so that `AGENTS.md` stays short enough to be useful on every
turn. It holds the two sections of `AGENTS.md` that answer *where does this
behaviour live* and *what was measured on a real host* — questions you ask
once per task, not once per edit.

**Read this file when** you are about to touch `n8n/`, `remote/`, `docker/`,
`git/`, `github/`, `database/`, `monitoring/`, `platform/`, or
`gui/browser.py` and `gui/window.py`. **Do not read it** to answer "how do I run
the tests" or "what is the packaging story" — that is `AGENTS.md` §Commands, or
`README.md` §Distribution.

Nothing here is a summary of the source: both sections moved here verbatim, and
they are the only place either is maintained. A change to a module's role or to
a measured fact is edited here first.

---

## Architecture

| Module | Role |
|---|---|
| `__main__.py` | Entry point. `main(argv=None)` short-circuits on `--self-test` before touching the config, the lock or Docker; then single-instance lock → `resolve_docker_command()` → journal → wizard (no config) or `run_gui()` → ordered shutdown. Brackets every run: `_start_monitoring()` logs `Surveillance active`, `_close_session()` logs `Surveillance terminée` (duration + how many workspaces were still up) **before** `monitor.close()`. A config that exists but cannot be read is backed up to `launcher.db.corrupt-<ts>` and the run stops; a config that does not exist opens the wizard. |
| `gui/app.py` | The shell: `ensure_application`, `display_available`, `GuiUnavailable`, `request_shutdown`/`shutdown_requested`/`clear_shutdown`, `LauncherApp.run()`, `run_gui()`, `SELF_TEST_FLAG`, `self_test()`. `run()` skips `exec()` when a shutdown was already requested; the `SIGNAL_TICK_MS = 200` `QTimer` is what lets a Python signal handler run at all while Qt's loop is parked in C++. `self_test()` returns an exit code — it is what CI runs on a finished artifact. |
| `gui/window.py` | `MainWindow(manager)`: header (*Nouveau* / *Démarrer* / *Arrêter*, objectNames `new`/`start`/`stop`), `QStackedWidget` board + empty state, `QListView` in IconMode painted by `CardDelegate`, selection sync, `workspace_actions` (not `actions` — `QWidget.actions` already exists). |
| `gui/card_delegate.py` | `CARD_WIDTH` / `CARD_HEIGHT`, `tone_color`, `CardDelegate`: paints one card (status pill, port, folder). Geometry and colours only, no widget state. |
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
| `workspaces/status.py` | What a row shows and which actions it offers: `StatusTone`, `WorkspaceStatus`, `status_for`, `state_label`, `summary_line`, `can_start` / `can_stop` / `can_delete` / `can_open`. A view renders this; it never re-derives a label, a tone or a rule. |
| `workspaces/close.py` | `CloseSequence`: the ordered shutdown (reconcile → export → git sync → stop, per workspace) with every decision injected as a `CloseHooks` callback (`on_progress`, `on_sync_failed`, `on_stop_failed`, `on_push_failed`), so it runs headless and is testable without a window. |
| `workspaces/dialogs.py` | What the forms *collect*, not how they looked: `CreatePlan`, `GitConfigChoice`, `GitClonePlan`, `GitHubCreatePlan`, `GitHubRepoPick`, `GitHubTokenPlan`, plus `fresh_managed_db_config`, `default_creation_db` (managed when `has_db_layout()` — a `db/schema.sql` or a `db/migrations/*.sql`), `int_or` and `repo_name_from`. |
| `workspaces/ci.py` | The CI harness for a workspace's *own* repo: `github_repo_path` (remote URL → `owner/repo`), workflow discovery (`collect_workflows`: `n8nPipelines/*.json` + root `*.json` minus a blocklist, **deduped by basename in favour of `n8nPipelines/`**), `load_export`, `missing_credentials`, `workflow_eligibility`, `save_ci_selection`, and `render_harness()` which emits the workflow YAML plus a stdlib-only `validate.py` / `runner.py` (`GENERATED_MARKER`-commented, image pinned through the `__N8N_IMAGE__` token). |
| `docker/compose.py` | Renders Compose YAML. One project per workspace, `n8n-ws-<id>`; named volumes `n8ndata-<id>` and, in managed mode, `pgdata-<id>`. Handles managed Postgres and `DbMode.NONE` (no DB service at all). `render_remote_compose` emits a top-level `name:` so the project is stable on the server. |
| `docker/manager.py` | Thin subprocess wrapper around `docker compose`, always with `-p <project>` and `-f <file>` for isolation. `list_project_states()` reads `Labels` through `parse_container_labels()`, which accepts the three shapes the CLI produces (object, JSON string, and the flat `k=v,k=v` string Compose 2.35 emits on Docker Desktop). `resolve_docker_command()` probes the macOS install locations before falling back to `PATH`. |
| `core/config.py` | SQLite config store (`launcher.db`, WAL). `load()` / `mutate(fn)` are safe under threads *and* processes (`BEGIN IMMEDIATE` write serialization + 10 s busy timeout; readers use the WAL). The connection is cached for the process lifetime, so `close()` exists — `__main__`'s `finally` and `gui/app.py::self_test()` both call it, and on Windows an open handle makes `launcher.db` undeletable. A legacy `config.json` is migrated once when the database is missing; the database and its `-wal` / `-shm` sidecars are chmodded `0o600` on non-Windows. |
| `core/filelock.py` | Cross-platform `FileLock` (fcntl/shared on POSIX, `msvcrt.locking` on Windows) and `acquire_single_instance_lock()`. |
| `core/models.py` | Dataclasses: `DbMode` (`NONE` / `MANAGED`), `DbConfig`, `GitConfig`, `ServerConfig`, `WorkspaceState`, `Workspace`, `AppConfig`. |
| `core/paths.py` | `platformdirs` paths for config, logs and runtime under `n8n-launcher`; `compose_file(workspace_id)` and `browser_profile_dir()`. |
| `n8n/api.py` | Small HTTP client for the n8n **public** API (workflows, credentials). |
| `n8n/owner.py` | Owner bootstrap through the internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`), retrying through the transient HTML n8n serves while starting. `wait_for_n8n()` polls readiness. Also `hash_owner_password()` (bcrypt). |
| `n8n/scopes.py` | `REQUIRED_WORKFLOW_SCOPES` — the one scope list, shared by the local bootstrap and by the generated remote `deploy.py` through the `__API_KEY_SCOPES__` token. It is a contract with the n8n version; never duplicate it. |
| `n8n/workflows.py` | `SyncRunner`: `import_all()` creates in n8n every workflow JSON found in the workspace folder; `export_all(mirror=…)` refreshes `n8nPipelines/` exports and optionally mirrors identical copies at the workspace root; optional background sync thread. |
| `git/manager.py` | Git operations for a workspace's workflow synchronization, in the `workflows_dir`. `WORKSPACE_BRANCH = "dev"` and `workspace_branch(id)`; `git_init` seeds that branch. `workspace_git_lock(path)` is the reentrant per-workspace lock. `git_push` (`-u origin <branch>`, one retry on a server-side rejection). `git_seed_remote` + `tokenize_remote_url` build the one-shot tokenized GitHub push URL. |
| `github/api.py` | GitHub REST client: `github_owner()`, `create_repo()`, `list_workflow_runs()`, `list_run_jobs()`, `fetch_job_logs()`, `dispatch_workflow()`; typed `GitHubError` with `status_code`. Repo paths keep a **literal** slash (`repos/owner/repo/...`) because URL-encoding it makes every Actions endpoint 404. |
| `github/auth.py` | `resolve_github_token(configured=None)`: the persisted override, then `gh auth token`, then `git credential fill` for `github.com` (the same credential `git push` uses), 10 s timeout, prompts disabled. Returns `None` when nothing is available; never logs or persists on its own. |
| `database/credentials.py` | Auto-creates n8n DB credentials; `data_db_target()` resolves the per-workspace DB target. |
| `database/layout.py` / `database/migrations.py` | `has_db_layout()` and the migration detection + runner (executes SQL via `docker compose exec psql`). Launcher-local: the generated remote `deploy.py` never runs them. |
| `monitoring/` | Structured event journal: `events.py` (`Event`), `store.py` (`EventStore`, SQLite `logs_dir()/events.db`, `RETENTION_DAYS = 30`, `latest_events` / `search_events` / `export_events`), `redaction.py` (secret-shaped values masked *before* they are persisted) and `bootstrap.py` (`bootstrap_logging`, `capture_exceptions`, `sys` / `threading` excepthooks). |
| `platform/ports.py` | `is_port_available` and `suggest_port`. |
| `platform/update_flow.py` | `UpdateController`: the release check → download → install order, with every question injected as a constructor callback (`set_status`, `on_offer`, `on_error`, `on_status_link`, `schedule`, `is_closed`, `finish_close`) and queue results routed back through `events`. All the network and filesystem work is `platform/updater.py`, which also compares `__version__`. |
| `remote/ssh.py` / `remote/deploy.py` | Remote server deployment and bounded observability: `ssh_run` / `scp` plumbing, atomic `write_remote_file`, the generated `post-receive` hook + `deploy.py` (template strings), and the read-only health / logs / marker / execution-status reads. `REMOTE_STATUS_CAPABILITY` and the execution-status sets live here. |

`git/__init__.py` and `remote/__init__.py` re-export their package's public
surface; import managers through them.

---

## Measured gotchas

Facts verified against a real n8n 2.x / Docker Desktop / Chromium host. Do not
re-derive them by guessing.

- **`/healthz` is liveness, not readiness.** On n8n 2.40, `/healthz` answers 200
  at ~2.8 s while `GET /` still returns 404 `Cannot GET /`; only at ~4.7 s does
  `/healthz/readiness` go 200 — the moment the frontend handler is mounted.
  Anything that opens the UI or calls the API gates on
  `/healthz/readiness`, which is what `n8n/owner.py::wait_for_n8n` polls. An n8n
  without that route answers 404 and the wait falls back to the UI root.
- **Public API is schema-strict**: `POST /api/v1/workflows` validates with
  `additionalProperties: false`, so a server-only field in the body is HTTP 400
  "must NOT have additional properties". Only `_create_payload`'s whitelist
  survives.
- **`/rest/*` authenticates with the `n8n-auth` session cookie** from
  `POST /rest/login`; n8n returns no body token and `Authorization: Bearer <JWT>`
  is rejected with 401. Because urllib / requests refuse to replay a `Secure`
  cookie over plain HTTP, the generated CI runner starts its disposable container
  with `N8N_SECURE_COOKIE=false` (same as the managed Compose containers) —
  without it `POST /rest/api-keys` 401s on every version. Listing credentials
  through the public API additionally needs the `credential:list` scope, or
  `GET /api/v1/credentials` is 403 « Forbidden ». `trigger_run` must accept both
  `{"executionId": …}` and `{"data": {"executionId": …}}`.
- **Owner bootstrap uses the internal REST endpoints**, not the
  `N8N_INSTANCE_OWNER_*` env vars; API keys need a `scopes` array and a numeric
  `expiresAt`; n8n serves transient HTML while starting, so the bootstrap retries
  until it gets JSON.
- **Reopening a browser window is not a URL problem.** n8n routes client-side (a
  fresh window on `/` lands on `/signin?redirect=%252F` with no HTTP redirect), so
  `--app=<same URL>` twice leaves two windows. `gui/browser.py` opens windows in a
  launcher-owned profile (`browser_profile_dir`), reads the port Chromium published
  in `DevToolsActivePort` (`--remote-debugging-port=0`), matches the instance's
  **origin** in `/json/list` and calls `/json/activate/<id>`. Two races (the port
  file appears before the endpoint accepts; a crashed browser leaves the file
  behind) fall back to launching a window. Security delta: the DevTools endpoint
  binds loopback only, over a profile already readable in the user's own config
  directory.
- **A missing Qt platform plugin fails silently** — the one packaging failure a
  user sees as « the app does nothing ». `verify_qt_bundle()` runs at the end of
  every build (and as `scripts/build.py --verify <root>` in CI), and
  `n8n-launcher --self-test` runs on the finished artifact on all three OS.
- **`docker ps` labels are not always a map** (see `docker/manager.py`).
- **macOS bundle-launched apps get a minimal `PATH`**, so `docker` is located by
  probing the standard install locations, never by bare name.
- **First launch and the unreadable config are two different branches.**
  `store.load()` raising `ConfigError` means either « absent » (wizard) or
  « present and broken » (back up and stop). The discriminator is
  `store.path.exists()`: a wizard may never run on top of a config it could not
  read.
- **`validate_password` is a mirror, not the source of truth** (8–64 chars, one
  digit, one uppercase, per n8n 2.40). It only saves the user a round trip; if n8n
  changes, both move together.
- **Activation on the server is best effort, through the owner session**: the
  public activate endpoint is refused by n8n 2.x for a workflow the key's user
  cannot activate. Creating the API key revokes the deploy session, so the
  `versionId` lookup re-logs in and retries. A refusal is logged, never raised —
  the import has already succeeded.
- **`finished` is derived, never sent.** `GET /rest/executions` returns no
  `finished` flag, so it comes from n8n's own vocabulary:
  `TERMINAL_EXECUTION_STATUSES` ⇒ `True`, `PENDING_EXECUTION_STATUSES` ⇒ `False`,
  anything else ⇒ `None`, an explicit boolean always winning. Both sets live in
  `remote/deploy.py` and are injected into the generated script; a meta-test
  asserts both parsers agree for every status. Callers sort on `startedAt`
  (newest first) — the remote order is not a contract.
