# n8n-launcher — Product Summary

**n8n-launcher** is a cross-platform application (Python 3.12+, Windows / macOS
/ Linux) for managing isolated Docker-based n8n workspaces. Each workspace is
its own Docker Compose project with its own data volume and optional PostgreSQL
database.

- **Version**: 5.0.3 — single-source SemVer in `src/n8n_launcher/__init__.py`
- **Entry point**: console script `n8n-launcher` → `n8n_launcher.__main__:main`
- **Interface**: none. No GUI toolkit is a dependency; `src/n8n_launcher/gui/`
  is a seam whose `run_gui()` and `LauncherApp.run()` raise
  `NotImplementedError("new GUI not yet implemented (phase 2)")`.
- **Runtime dependencies**: `bcrypt`, `platformdirs`, `PyYAML`, `requests`
- **Build dependency**: PyInstaller, plus `dmgbuild` on macOS
- **Configuration**: `platformdirs.user_config_dir("n8n-launcher")/launcher.db`
  (SQLite, WAL, `0o600` on non-Windows; a legacy `config.json` is migrated in
  once). The journal lives in `logs_dir()/events.db` with a 30-day retention.

## Architecture

```
src/n8n_launcher/
├── __main__.py            # lock → journal → run_gui() (refuses) → ordered shutdown
├── core/
│   ├── models.py          # DbMode, DbConfig, GitConfig, Workspace, AppConfig
│   ├── config.py          # ConfigStore: SQLite WAL, load/mutate, thread- and process-safe
│   ├── filelock.py        # FileLock, single-instance lock, reentrant per-workspace git lock
│   └── paths.py           # platformdirs config/log/runtime paths, compose_file(id)
├── database/
│   ├── migrations.py      # MigrationRunner: detect + apply SQL via `compose exec psql`
│   ├── layout.py          # db/migrations directory layout
│   └── credentials.py     # auto-create n8n DB credentials, data_db_target()
├── docker/
│   ├── compose.py         # write_compose(), render_remote_compose(): per-workspace YAML
│   └── manager.py         # `docker compose` wrapper, list_project_states(), resolve_docker_command()
├── git/
│   └── manager.py         # init/clone/add/commit/push/pull/remotes, workspace_branch(), tokenize_remote_url()
├── github/
│   ├── api.py             # GitHubClient: owner, create_repo, runs, jobs, logs, dispatch_workflow()
│   └── auth.py            # resolve_github_token(): override → gh CLI → git credential
├── n8n/
│   ├── api.py             # public REST client (workflows, credentials)
│   ├── owner.py           # owner bootstrap through the internal /rest/* endpoints, hash_owner_password()
│   ├── workflows.py       # SyncRunner: import_all(), export_all(mirror=…), background thread
│   └── scopes.py          # the one API-key scope list shared by owner.py and the generated deploy.py
├── monitoring/
│   ├── events.py          # Event record
│   ├── store.py           # EventStore: SQLite, 30-day retention, search/export
│   ├── redaction.py       # mask secret-shaped values before they are written
│   └── bootstrap.py       # bootstrap_logging(), capture_exceptions()
├── remote/
│   ├── ssh.py             # ssh_run/scp, atomic write_remote_file, health/logs/marker/status reads
│   └── deploy.py          # generated post-receive hook + deploy.py (template strings)
├── platform/
│   ├── ports.py           # is_port_available(), suggest_port()
│   ├── updater.py         # GitHub release comparison + download
│   └── update_flow.py     # UpdateController: check → download → install, questions injected
├── gui_utils/
│   └── text.py            # ellipsize, column_widths, fit_budget — toolkit-free sizing rules
├── gui/
│   └── app.py             # the seam: run_gui() / LauncherApp.run() raise NotImplementedError
└── workspaces/
    ├── manager.py         # WorkspaceManager: CRUD, start/stop, git sync, CI, install_server/publish
    ├── close.py           # CloseSequence with injected CloseHooks
    ├── dialogs.py         # the plans a form collects, and the rules that fill them
    └── ci.py              # CI harness, render_harness(), eligibility, tests.json selection
```

`git/__init__.py` and `remote/__init__.py` re-export their package's public
surface, so consumers import from `n8n_launcher.git` /
`n8n_launcher.remote` without knowing the file layout.

## Behaviour

### Workspace lifecycle

- States: `STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `ERROR`. `docker down`
  runs between the `STOPPING` and `STOPPED` writes.
- `start()` writes the Compose file, brings the stack up and applies the managed
  SQL migrations. `ensure_running()` starts the workspace if it is not already
  running, invokes `on_ready(port)` for the caller to wait on n8n's health
  endpoint, bootstraps the owner when the workspace has no API key, creates the
  DB credentials, and imports the workflows (auto-pull included).
- `reconcile_all()` syncs persisted states with Docker reality: one
  `docker ps` batch through `list_project_states()` before taking the config
  lock, then a single `store.mutate` for the changes. It is called on demand,
  not on a timer. A workspace whose Compose file is gone keeps its stored state
  and is never force-stopped.
- `parse_container_labels()` accepts the three label shapes the Docker CLI
  produces, including the flat `k=v,k=v` string Compose 2.35 emits on Docker
  Desktop.
- Managed DB auto-fills missing parameters (database `data`, user `n8ndata`,
  random password). `postgres_image` and `postgres_preload_timescaledb` are
  per-workspace. A port is suggested automatically when the chosen one is taken.
- Each workspace pins its n8n version (`n8n_version`, default 2.40.0).

### Docker Compose

- Project name `n8n-ws-<id>`; named volumes `n8ndata-<id>` and, in managed mode,
  `pgdata-<id>`.
- `DbMode.NONE` omits the database service entirely. A legacy `"external"` mode
  loads as `NONE`.

### Git

- Branch model: every launcher repo works on `dev`, seeded by
  `git -c init.defaultBranch=dev init`; a legacy `main`/`n8n/*` branch is
  renamed to `dev` once on the first `ensure_running()`. `dev` is the work
  branch and the CI branch; `main` exists only on the server's bare repository,
  where `publish` pushes `dev:main`.
- Concurrency: git lifecycle calls take a reentrant per-workspace lock
  (`.n8n-launcher.git.lock`, gitignored).
- Auto-pull `--rebase` on start (never blocks), auto-push on close (skipped when
  there is nothing to commit and no unpushed commit). `git_push` seeds an empty
  remote with `-u` and retries once on a server-side rejection. Push failures are
  logged, never raised.
- GitHub repo creation stores the clean URL and seeds the remote with a one-shot
  tokenized push, so the token never lands in `.git/config`.

### Workflows

- Import scans `n8nPipelines/` and the workspace root; export writes
  `n8nPipelines/<name>-<id>.json` and, with `mirror=`, identical copies at the
  root. Mirror cleanup is restricted to launcher-named files
  (`EXPORT_NAME_RE = .+-\d+\.json$`).
- Every consumer of both locations dedups by basename in favour of
  `n8nPipelines/` (`collect_workflows()`, `collect_workflow_files()`).
- Create payloads are a whitelist: n8n's public create schema rejects
  server-only fields with HTTP 400.

### GitHub Actions CI

- `render_harness()` emits `.github/workflows/n8n-ci.yml` (jobs `validate` +
  `test`), `.n8n-tests/validate.py` and `.n8n-tests/runner.py`, all
  marker-commented, with the image pinned through the `__N8N_IMAGE__` token.
  Triggers: `workflow_dispatch` and pushes on `dev`.
- `workflow_eligibility(export, credentials)` decides whether a pipeline is
  testable: a manual, schedule or *pinned* webhook/chat trigger, plus every
  non-pinned node's credential types covered. It returns a French rationale with
  the verdict. `save_ci_selection()` does not enforce it — a caller that can tick
  a pipeline has to ask.
- Selection is machine-managed (`.n8n-tests/tests.json`).
- Only `(name, type)` credential metadata is persisted. `ci_credentials_payload(id)`
  needs a started workspace with an `api_key` and reads the values once to build
  the `N8N_CI_CREDENTIALS` secret.
- Runs are read through `github/api.py`; a manual run dispatches
  `workflow_dispatch` on a chosen ref.

### Remote deployment and observability

- `install_server()` initializes the bare repo before writing the
  `post-receive` hook, then ships the hook and `deploy.py` atomically.
- The hook composes the stack with `-p "$PROJECT"` and writes
  `last-deploy.json` atomically; `deploy.py` recreates credentials, imports
  workflows with the basename dedup, and never runs `db/migrations/*.sql`.
- Activation goes through the deploy's owner session (`/rest/workflows/{id}`
  then `/rest/workflows/{id}/activate`) and is best effort: a refusal is logged.
- `publish()` pushes `dev:main` with a 300 s timeout, polls the marker until the
  sha matches HEAD, then records `server_last_error` / `server_last_deploy`.
- `server_health`, `server_logs`, `server_deploy_status` and
  `server_execution_status` are read-only, bounded, redacted, and degrade to an
  empty result instead of raising. `REMOTE_STATUS_CAPABILITY` is the opt-in for
  the execution-status command: a server whose `deploy.py` predates it reports
  "unsupported".
- Execution `finished` is derived from n8n's own vocabulary
  (`TERMINAL_EXECUTION_STATUSES` ⇒ `True`, `PENDING_EXECUTION_STATUSES` ⇒
  `False`, else `None`; an explicit boolean wins). Both parsers are asserted to
  agree by a meta-test, and callers must sort on `startedAt`.

### Monitoring

- Every run is bracketed: `Surveillance active` (store path, retention) and
  `Surveillance terminée — session de h:mm:ss, N workspace(s) en cours à la
  fermeture`. The teardown and the closing event are written in `main()`'s
  `finally`, before the store is closed; the `atexit` hook is the fallback for
  exits that never unwind `main`.
- `bootstrap_logging()` degrades to stderr-only when the log directory is not
  writable, so monitoring can never keep the launcher from starting.
- Events are structured, redacted before being written, kept 30 days, and
  reachable through `EventStore.search_events()` (SQL) and `export_events()`.
  There is no second in-memory filter.

### First launch

There is no wizard and no automatic configuration: a launcher with no config
logs that it is unconfigured and stops, rather than fabricating an empty
workspace list. Creating the first config is manual.

## Quality gates

- `ruff check .` and `ruff format --check .`: clean.
- `basedpyright` (`typeCheckingMode = "standard"`, `tests/**` and `scripts/**`
  excluded): 0 errors.
- `pytest`: 686 unit tests pass; the integration suite (7 tests, Docker
  required) is deselected by `addopts = "-m 'not integration'"`.
- Coverage floor `--cov-fail-under=80`, enforced by CI.

## Distribution

| Platform | Artifact | Method |
|---|---|---|
| macOS | `dist/n8n-launcher-macos.dmg` | `scripts/build.py`: PyInstaller onedir `.app` → ad-hoc sign → `dmgbuild` |
| Linux | `dist/n8n-launcher`, `dist/n8n-launcher-linux-x86_64.AppImage` | `scripts/build.py` + `scripts/build_appimage.sh` |
| Windows | `dist/n8n-launcher.exe` | `scripts/build.py`: PyInstaller one file |

CI runs the tests and the build on `ubuntu-latest`, `macos-latest` and
`windows-latest` (integrations on Linux only). Releases are published from
`main` only, when the source version differs from the last `v<version>` tag.
