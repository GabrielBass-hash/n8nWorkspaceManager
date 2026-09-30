# n8n-launcher — Product Summary

**n8n-launcher** is a cross-platform application (Python 3.12+, **headless — no GUI toolkit**) for managing isolated Docker-based n8n workspaces. Each workspace runs as its own Docker Compose project with its own data volume and optional PostgreSQL database, providing full environment isolation without manual Compose management.

- **Version**: 5.0.3 (single-source SemVer in `src/n8n_launcher/__init__.py`)
- **License**: See repository
- **Entry point**: `n8n-launcher` console script → `n8n_launcher.__main__:main`
- **Interface**: none. `run_gui()` raises `NotImplementedError`; see `MIGRATION.md`.

---

## Architecture

```
src/n8n_launcher/
├── __main__.py            # Entry point: journal → interface (absent) → ordered shutdown
├── core/                  # Datamodels, config store, platform paths, locks
│   ├── models.py          # DbMode, GitConfig, DbConfig, Workspace, AppConfig (dataclasses)
│   ├── config.py          # ConfigStore: SQLite (WAL), load/mutate, multi-process safe
│   ├── filelock.py        # Cross-platform FileLock, single-instance lock, per-workspace git lock
│   ├── throttle.py        # Per-process concurrency limiter for background actions
│   ├── subjects.py        # PageSubject: the journal filter a focused view installs
│   ├── first_launch.py    # First-launch validation (password policy, config building)
│   └── paths.py           # platformdirs-based paths (config, logs, workspace runtime)
├── docker/                # Compose rendering + Docker subprocess wrapper
│   ├── compose.py         # write_compose(workspace, path) — per-workspace YAML
│   └── manager.py         # docker compose up/down/status; resolve_docker_command()
├── git/                   # Git operations wrapper (subprocess)
│   └── manager.py         # init, clone, add, commit, push, pull, remote management
├── github/                # GitHub REST client + token resolution
│   ├── api.py             # GitHubClient: github_owner(), create_repo(),
│   │                      # list_run_jobs(), fetch_job_logs(), dispatch_workflow()
│   └── auth.py            # resolve_github_token(): gh CLI / git credential / optional override
├── n8n/                   # n8n integration
│   ├── api.py             # HTTP client for public API (workflows, credentials)
│   ├── owner.py           # Owner bootstrap via internal REST endpoints
│   └── workflows.py       # SyncRunner: import_all() / export_all()
├── database/              # DB migrations, credentials, layout
│   ├── migrations.py      # MigrationRunner: detect + apply SQL via docker compose exec psql
│   ├── layout.py          # DB directory structure
│   └── credentials.py     # Auto-create n8n DB credentials; data_db_target()
├── platform/              # OS-specific helpers
│   ├── ports.py           # Port availability check + suggestion
│   ├── browser.py         # Browser app mode (--app flag for Chrome/Edge/Brave/Chromium)
│   ├── files.py           # Reveal a written file in the OS file manager
│   ├── shortcuts.py       # Desktop shortcuts: .desktop / .url / .command
│   ├── update_flow.py     # UpdateController: release check → download → install (injected questions)
│   └── updater.py         # GitHub release comparison + download (all network/filesystem work)
├── gui/                   # The seam, not the interface: run_gui() refuses
│   └── app.py             # LauncherApp / run_gui — raise NotImplementedError (phase 2)
├── gui_utils/             # Toolkit-free sizing rules (a `measure` callable renders them anywhere)
│   └── text.py            # ellipsize, column_widths, fit_budget, wrap_at
├── monitoring/            # Structured event journal
│   ├── events.py          # Event record
│   ├── store.py           # EventStore: SQLite, 30-day retention, search, export
│   ├── redaction.py       # Mask secret-shaped values before they are persisted
│   └── bootstrap.py       # Logging setup, exception capture
├── remote/                # Remote server deployment
│   ├── ssh.py             # ssh_run / scp plumbing, atomic write_remote_file
│   └── deploy.py          # Generated post-receive hook + deploy.py (template strings)
└── workspaces/            # Workspace orchestration & CI
    ├── manager.py         # WorkspaceManager: CRUD, start/stop, git sync, CI, install_server/publish
    ├── close.py           # CloseSequence: export → git sync → stop (ordered, headless)
    ├── dialogs.py         # Creation/Git plans (CreatePlan, GitClonePlan…) + form helpers
    ├── display.py         # Row formatting, git status, DB label, pipeline count
    ├── server_snapshot.py # ServerSnapshot: the read-only supervision model
    ├── ci.py              # CI harness: render_harness(), eligibility, tests.json selection
    └── ci_runs.py         # Runs model: RunSummary, JobSummary, RunsSnapshot, parsers
```

---

## Core Features

### Workspace CRUD & Lifecycle

- Create a workspace from a folder (workflows JSON in `n8nPipelines/`), with a DB mode (`CreatePlan.db`: managed local Postgres / none) and optional Git setup — collected by a form, decided by `workspaces/dialogs.py`.
- Start/stop with state reconciliation: background poller (5 s) queries `docker compose ps --format json` and reconciles with persisted state (STOPPED → STARTING → RUNNING → STOPPED → ERROR).
- Managed DB auto-fills missing parameters: `database_name` = `data`, `username` = `n8ndata` (fixed defaults), random password; `postgres_image` and `postgres_preload_timescaledb` configurable; identity immutable once set.
- Port auto-suggestion avoids conflicts across workspaces.
- Per-workspace n8n version pin (`n8n_version`, default 2.40.0).

### Docker Compose

- Each workspace gets its own Compose project: `n8n-ws-<id>`.
- Named volumes: `n8ndata-<id>` (data) and `pgdata-<id>` (managed mode).
- Managed Postgres via local `postgres` service; `DbMode.NONE` omits the DB service entirely.
- Docker CLI resolution on macOS probes `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin`, `/usr/local/sbin`, `/Applications/Docker.app/Contents/Resources/bin` before falling back to `PATH`.

### Git Synchronization

- Optional per-workspace: `GitConfig(enabled, remote_url, branch, ci_enabled, ci_credentials)`.
- Auto-pull `--rebase` on start (before workflow import); auto-push on close (export → stage → commit → push).
- One-shot tokenized push URL for GitHub (`https://<token>@github.com/...`), token never stored in `.git/config`. Clean URL persisted in `GitConfig.remote_url`.
- Push failures degrade to a `git_push_failed` flag (no exceptions) and are logged, never raised.

### GitHub Repo Creation

- `prompt_github_create` (`GitHubCreatePlan`): repo name derived from the workspace name (`repo_name_from`), visibility private by default, and a PAT resolved from `resolve_github_token()` (`gh auth token`, or the OS Git credential). The token is used once and never persisted.
- Token used once for REST API call + one seed push; never persisted unless an override is stored once as `AppConfig.github_token`.

### Workflow Import/Export

- On start: `import_all()` scans `n8nPipelines/` and workspace root for `*.json`, creates via `POST /api/v1/workflows` (whitelist payload; skips already-present by id or name; skips invalid/unreadable files).
- On stop: `export_all()` exports all workflows from n8n back to JSON.

### GitHub Actions CI

- **Harness generation** (`render_harness()`): emits `.github/workflows/n8n-ci.yml` (2 jobs: `validate` + `test`), `.n8n-tests/validate.py` (JSON shape, duplicate names, selection consistency), `.n8n-tests/runner.py` (imports via public API, recreates credentials, triggers via `/rest/workflows/:id/run`, polls execution, exits nonzero on failure). All files marker-commented; n8n image pinned via `__N8N_IMAGE__` token substitution.
- **Eligibility**: pipeline is testable iff manual/schedule/pinned trigger + all non-pinned nodes' credential types are covered by `ci_credentials` metadata.
- **Selection**: machine-managed `tests.json` (`{"selected": [...]}`), written by `save_ci_selection()`; only eligible pipelines can be ticked, and every ineligibility carries a reason.
- **Credentials**: `ci_credentials_payload(id)` reads the values live from the n8n public API (`api.get_credential(id).data`) and hands back the JSON for the `N8N_CI_CREDENTIALS` secret; only `(name, type)` metadata is persisted in config.
- **Runs**: `workspaces/ci_runs.py` models a run → jobs → pipelines snapshot parsed from the REST payloads and the runner's log lines; `dispatch_workflow()` fires `workflow_dispatch` on a chosen ref (prefilled with the latest run's branch).
- Runner auth: internal `/rest/*` calls use `n8n-auth` session cookie from `POST /rest/login` (not Bearer JWT). Container starts with `N8N_SECURE_COOKIE=false` for cookie replay over HTTP.

### First launch

There is no wizard yet: it was a view, so it went with the rest of the interface. What it collected and checked is headless and tested — `core/first_launch.py` validates the password against n8n's own policy (8-64 chars, one digit, one uppercase) and builds the initial config, `platform/shortcuts.py` installs the desktop shortcut, `docker.manager` checks availability. A launcher with no config logs that it is unconfigured and exits (`main()` does not fabricate an empty workspace list).

### Monitoring

- Every run is bracketed in the journal: `Surveillance active` at start, `Surveillance terminée — session de h:mm:ss, N workspace(s)` at close.
- Events are structured, redacted before they are written, kept 30 days, searchable, and exportable as JSON from the hidden log directory.

### Browser App Mode

- Opens n8n in Chrome/Edge/Brave/Chromium with `--app` flag (isolated window). Falls back to `webbrowser.open`. Uses isolated browser profile per workspace (`config_dir/browser/<id>/`).

### Desktop Shortcuts

- Linux: `.desktop` file · Windows: `.url` file · macOS: `.command` script.

### Updater

- `platform/updater.py` compares `__version__` against GitHub releases; background check offers download prompt.

---

## Distribution

| Platform | Artifact | Method |
|---|---|---|
| macOS | `dist/n8n-launcher-macos.dmg` | `scripts/build.py`: PyInstaller onedir `.app` → ad-hoc sign → `dmgbuild` styled DMG |
| Linux | `dist/n8n-launcher` (onefile) or `dist/n8n-launcher-linux-x86_64.AppImage` | `scripts/build.py` + `scripts/build_appimage.sh` |
| Windows | `dist/n8n-launcher.exe` | `scripts/build.py`: PyInstaller one-file |

CI matrix: `ubuntu-latest`, `macos-latest`, `windows-latest` for tests + build. Release on version bump (source version ≠ last git tag, main-only).

---

## Requirements

- **Runtime**: Docker daemon (running), Git (optional), GitHub CLI / OS Git credentials (optional, for CI features)
- **Development**: Python 3.12+, `uv` (package manager)
- **Config location**: `platformdirs.user_config_dir("n8n-launcher")/launcher.db` (SQLite in WAL mode, `0o600` on non-Windows; a legacy `config.json` is migrated in once). `logs_dir()` holds `events.db` (the journal) with 30-day retention.
- **Compose files**: `<config_dir>/workspaces/<id>/compose.yml`
