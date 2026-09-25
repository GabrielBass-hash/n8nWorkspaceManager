# n8n-launcher — Product Summary

**n8n-launcher** is a cross-platform desktop application (Python 3.12+, Tkinter) for managing isolated Docker-based n8n workspaces. Each workspace runs as its own Docker Compose project with its own data volume and optional PostgreSQL database, providing full environment isolation without manual Compose management.

- **Version**: 5.0.3 (single-source SemVer in `src/n8n_launcher/__init__.py`)
- **License**: See repository
- **Entry point**: `n8n-launcher` console script → `n8n_launcher.__main__:main`

---

## Architecture

```
src/n8n_launcher/
├── __main__.py            # Entry point: config load → first-launch wizard → Tkinter GUI
├── core/                  # Datamodels, config store, platform paths
│   ├── models.py          # DbMode, GitConfig, DbConfig, Workspace, AppConfig (dataclasses)
│   ├── config.py          # ConfigStore: atomic JSON writes, thread lock, mutate()
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
│   ├── shortcuts.py       # Desktop shortcuts: .desktop / .url / .command
│   └── updater.py         # GitHub release comparison + update check
├── gui/                   # Tkinter UI
│   ├── app.py             # LauncherApp: workspace list, rows, chips, context menu
│   ├── dialogs.py         # Modal forms: create workspace, git config, GitHub repo/token
│   ├── first_launch.py    # First-launch wizard (email, password, work dir, shortcut)
│   ├── display.py         # Row formatting, git status, DB label, pipeline count
│   ├── close.py           # CloseController: export + git sync + stop on app close
│   ├── update_flow.py     # UpdateController: GitHub release check + download prompt
│   ├── ci_edit.py         # CI dialogs: pipeline tree, credentials, run ref
│   ├── ci_runs.py         # RunsPanel: runs → jobs → pipelines tree view
│   └── theme.py           # Dark theme constants + styles
└── workspaces/            # Workspace orchestration & CI
    ├── manager.py         # WorkspaceManager: CRUD, start/stop, git sync, CI enable/disable
    ├── ci.py              # CI harness: render_harness(), eligibility, tests.json selection
    └── ci_runs.py         # Runs model: RunSummary, JobSummary, RowsSnapshot, parsers
```

---

## Core Features

### Workspace CRUD & Lifecycle

- Create workspace from a folder (workflows JSON in `n8nPipelines/`), with DB mode selection (Managed local Postgres / None) and optional Git setup in a single dialog.
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
- Push failures degrade to a `git_push_failed` flag (no exceptions); UI shows a warning chip + dialog on stop.

### GitHub Repo Creation

- "Créer sur GitHub…" dialog: repo name (auto-derived from workspace name), visibility (private by default), PAT (prefilled from `gh auth token` or clipboard, "Détecter via gh CLI" button).
- Token used once for REST API call + one seed push; never persisted unless user explicitly sets an override via "Configurer le token GitHub…".

### Workflow Import/Export

- On start: `import_all()` scans `n8nPipelines/` and workspace root for `*.json`, creates via `POST /api/v1/workflows` (whitelist payload; skips already-present by id or name; skips invalid/unreadable files).
- On stop: `export_all()` exports all workflows from n8n back to JSON.

### GitHub Actions CI

- **Harness generation** (`render_harness()`): emits `.github/workflows/n8n-ci.yml` (2 jobs: `validate` + `test`), `.n8n-tests/validate.py` (JSON shape, duplicate names, selection consistency), `.n8n-tests/runner.py` (imports via public API, recreates credentials, triggers via `/rest/workflows/:id/run`, polls execution, exits nonzero on failure). All files marker-commented; n8n image pinned via `__N8N_IMAGE__` token substitution.
- **Eligibility**: pipeline is testable iff manual/schedule/pinned trigger + all non-pinned nodes' credential types are covered by `ci_credentials` metadata.
- **Selection**: machine-managed `tests.json` (`{"selected": [...]}`); tree dialog shows checkboxes for eligible pipelines, greyed-out for ineligible with rationale.
- **Credentials dialog**: reads values live from n8n public API (`api.get_credential(id).data`), copies JSON to clipboard as `N8N_CI_CREDENTIALS` secret; only `(name, type)` metadata persisted in config.
- **Runs panel**: tree view of runs → jobs → pipelines with glyphs (✔/✘/◻/–), auto-refresh every 5 s, "Ouvrir sur GitHub" opens a run on GitHub, "Lancer la CI" dispatches `workflow_dispatch` on a chosen ref (prefilled with latest run's branch); disabled while a run is in flight.
- Runner auth: internal `/rest/*` calls use `n8n-auth` session cookie from `POST /rest/login` (not Bearer JWT). Container starts with `N8N_SECURE_COOKIE=false` for cookie replay over HTTP.

### First-Launch Wizard

- Prompts for owner email, password (n8n policy: 8-64 chars, one digit, one uppercase), work directory.
- Checks Docker availability before saving config.
- Optionally installs a desktop shortcut.

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
- **Config location**: `platformdirs.user_config_dir("n8n-launcher")/config.json` (JSON, atomic writes, 0o600 perms on non-Windows)
- **Compose files**: `<config_dir>/workspaces/<id>/compose.yml`
