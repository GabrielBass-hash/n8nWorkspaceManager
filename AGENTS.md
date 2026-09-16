# AGENTS.md — n8n-launcher

## What this is

Cross-platform Tkinter desktop app (Python 3.12+; **Windows, macOS, Linux/Ubuntu**) for managing isolated Docker-based n8n workspaces. Single package `n8n_launcher` under `src/`, split into subpackages by concern: `core/` (config, models, paths), `database/`, `docker/`, `git/`, `n8n/` (API, owner bootstrap, workflow sync), `platform/` (updater, browser, ports, shortcuts), `gui/` (app, dialogs, close/update flows, display), `workspaces/` (manager). Each file is one concern.

Releases ship natively per platform: `.dmg` (macOS, ad-hoc signed, styled via `dmgbuild` with app icon + full `Info.plist`), one-file `.exe` (Windows), `.AppImage` (Linux). CI builds all three in the `build` job and the release-asset job.

## Commands

```bash
# Setup
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[test]'

# Run unit tests (default — excludes integration)
pytest

# Run integration tests (requires Docker daemon running)
pytest -m integration

# Run a single test file
pytest tests/unit/docker/test_compose.py

# Run a single test
pytest tests/unit/docker/test_compose.py::test_render_compose_managed -v

# Build desktop executable (macOS: .dmg via dmgbuild; Linux/Windows: one-file exe)
python -m pip install -e '.[packaging]'
python scripts/build.py

# Additional Linux AppImage (after scripts/build.py produced dist/n8n-launcher)
bash scripts/build_appimage.sh
```

There is **no lint, typecheck, or formatter** configured in this repo. CI only runs `pytest` (matrix: ubuntu, macOS, windows) and `scripts/build.py`.

## Test structure

- `tests/unit/` — self-contained, no external services. Run by default. Mirrors `src/n8n_launcher/` layout: `tests/unit/<subpackage>/test_<module>.py`, with `tests/unit/gui/` holding a shared `conftest.py` (mock ticks, `SyncThread`, messagebox patches) and `helpers.py` (row/chip inspection helpers). `tests/unit/<subpackage>/test_manager.py` **must not** be used as a filename — the base name collides across `docker/`, `git/`, `workspaces/`; the files are `test_docker_manager.py`, `test_git_manager.py`, `test_workspace_manager.py`.
- `tests/integration/` — requires Docker. Auto-skips if Docker is unavailable. Uses a session-scoped `DockerManager` with a 180-second timeout.
- `tests/conftest.py` — provides `managed_workspace` fixture (a `Workspace` with `tmp_path` and managed DB).
- `tests/unit/n8n/` (API sync, owner bootstrap, workflow import), `platform/test_browser.py` (browser app-mode), `platform/test_shortcuts.py`, `gui/test_display.py` (display metadata), `git/test_git_manager.py`, `workspaces/test_workspace_manager.py`, and `database/test_database.py` cover the newer helpers. GUI behavior (list, dialogs, close/update flows) lives under `tests/unit/gui/`.

The `addopts` in `pyproject.toml` is `"-m 'not integration'"`, so plain `pytest` only runs unit tests.

## Code conventions

- **Unit test coverage is mandatory and maximal**: every public function must come with its unit test(s) in `tests/unit/test_<module>.py`. A new function without a test is rejected.
- **Respect generic code-writing conventions**: clarity, small functions, no dead code, no reinvented stdlib helpers, explicit typing (`from __future__ import annotations` is used repo-wide).
- **Document the code**: every module has a docstring, every function/class has a docstring, and every non-obvious variable/block gets a comment explaining the *why* (not the *what*).

## Key architecture

| Module | Role |
|---|---|
| `__main__.py` | Entry point. Loads config, runs first-launch wizard if needed, then opens Tkinter GUI. Resolves the Docker CLI via `resolve_docker_command()` (macOS PATH caveat). Registers `atexit` to stop all workspaces. |
| `workspaces/manager.py` | Central controller — CRUD + start/stop lifecycle. Orchestrates compose rendering, Docker, migrations, owner bootstrap, DB credentials, workflow import, and **Git synchronization** (auto-pull on start, auto-push on close). |
| `docker/compose.py` | Renders Docker Compose YAML. Each workspace gets its own isolated Compose project named `n8n-ws-<id>`. Handles managed Postgres and `DbMode.NONE` (no DB service). |
| `docker/manager.py` | Thin subprocess wrapper around `docker compose` commands. Uses `-p <project>` and `-f <file>` for isolation. `resolve_docker_command()` probes macOS install locations (Homebrew, Docker Desktop, `/usr/local/bin`) before falling back to `PATH`. |
| `core/config.py` | JSON config store with atomic writes (temp file → rename) and `0o600` perms on non-Windows. |
| `core/paths.py` | Uses `platformdirs` for cross-platform config/log/runtime directories under `n8n-launcher`. Also exports `compose_file(workspace_id)`. |
| `n8n/owner.py` | n8n owner bootstrap via internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`). |
| `n8n/api.py` | Small HTTP client for the n8n **public** API (workflows, credentials). Used by owner bootstrap and workflow sync. |
| `database/credentials.py` | Auto-creates n8n DB credentials; `data_db_target()` resolves the per-workspace DB target. |
| `n8n/workflows.py` | Workflow synchronization: `import_all()` creates in n8n every workflow JSON found in the workspace folder; optional background sync thread. |
| `git/manager.py` | Git operations for workspace workflow synchronization. Wraps `subprocess.run` calls to `git` (init, clone, add, commit, push, pull, remote management). All commands run in the workspace's `workflows_dir` with a 30 s timeout. |
| `gui/app.py` | Tkinter GUI. Dark theme, workspace list view and actions. Creation dialog asks DB choice (local managed / none) then offers git setup. Context menu has "Configurer Git…" for existing workspaces. |
| `database/layout.py` / `database/migrations.py` | Migration detection and runner (executes SQL via `docker compose exec psql`). |
| `platform/browser.py` | Opens n8n in browser app mode (`--app` flag for Chrome/Edge/Brave/Chromium, falls back to `webbrowser.open`). |
| `gui/first_launch.py` | First-launch config (`email`, owner password, work dir, desktop shortcut). `validate_password()` enforces n8n's own password policy. |
| `platform/ports.py` | Port availability check (`is_port_available`) and automatic suggestion (`suggest_port`) for n8n instances. |
| `platform/shortcuts.py` | Desktop shortcut creation: `.desktop` (Linux), `.url` (Windows), `.command` (macOS). |
| `gui/display.py` | Display helpers for the GUI: git status (`git_repo_status`), DB label, pipeline count, per-workspace row formatting. |
| `core/models.py` | Dataclasses incl. `DbMode` (`NONE` / `MANAGED`), `DbConfig`, `GitConfig`, `Workspace` (with `postgres_image`, `postgres_preload_timescaledb`, `git`). |

## Workflow import

- On `ensure_running()`, `_import_workflows()` scans `<workflows_dir>/n8nPipelines/` **and** the workspace folder root for `*.json` workflow exports and creates them in n8n via the public API.
- JSON files without a `nodes` key, unreadable files, and workflows already present (by id **or** name) are skipped.
- `_create_payload()` is a **whitelist** of fields accepted by the public create schema (`name`, `nodes`, `connections`, `settings`, `staticData`, `pinData`, `nodeGroups`, `projectId`, `parentFolderId`); `settings` is defaulted to `{}`. Anything else (e.g. `active`, `triggerCount`, `shared`) is dropped.

## Git synchronization

Git is optional per-workspace and configured at workspace creation (git init + optional remote URL) or later via the "Configurer Git…" context menu entry. Persisted as `Workspace.git` (`GitConfig(enabled, remote_url, branch)`).

- **Auto-pull on start**: before `_import_workflows()`, `git_pull()` (with `--rebase`) brings remote JSON changes into `workflows_dir` only when `git.enabled` is set and the folder is a real repo. Failures are logged as warnings and never block startup.
- **Auto-push on close**: the GUI close sequence exports n8n workflows to JSON (`export_all`), then `WorkspaceManager.sync_git()` stages everything (`git add -A`), commits with a timestamped message, and pushes. Push is skipped when there is nothing to commit *and* no unpushed commits. Push failures are logged, never raised.
- `git_push` uses `-u origin <branch>` so a branch created by the launcher (`main` via `git init` + `git branch -M main`) seeds a fresh (empty) remote on first push.
- `git_repo_status()` in `gui/display.py` probes the real repo with `git rev-parse --git-dir` — a bare `.git` directory is not enough.

## Gotchas

- **n8n 2.33.3 integration quirks**: Owner bootstrap uses internal REST endpoints (not `N8N_INSTANCE_OWNER_*` env vars). API keys require a `scopes` array and numeric `expiresAt`. Startup returns transient HTML until n8n is ready — bootstrap retries until it gets JSON.
- **Public API is schema-strict**: `POST /workflows` validates with `additionalProperties: false`; sending read-only/server export fields in the body returns HTTP 400 "must NOT have additional properties". Only the whitelisted create fields survive (`_create_payload`).
- **Password policy**: `validate_password()` mirrors n8n 2.33.3 — 8 to 64 chars, at least one digit and one uppercase letter. `test1234` is rejected.
- **Managed DB**: uses the local `postgres` service in Compose. `DbMode.NONE` omits the Postgres service entirely; legacy `"external"` configs fall back to `NONE` on load.
- **Named volumes required**: Each workspace declares both `n8ndata-<id>` and (managed mode) `pgdata-<id>` as named volumes.
- **Managed DB auto-generation**: If a workspace uses `DbMode.MANAGED` without explicit DB params, `WorkspaceManager` auto-generates `database_name`, `username`, and a random `password`.
- **Config file location**: Config lives at `platformdirs.user_config_dir("n8n-launcher")/config.json`. Compose files go under `config_dir/workspaces/<id>/compose.yml`.
- **Gitignored state**: `state/`, `workspaces/`, `*.env` files are local launcher state — never commit these.
- **Windows config permissions**: `0o600` chmod is skipped on Windows (`os.name == "nt"`).
- **macOS PATH in GUI-launched apps**: Finder/Dock/Launchpad start apps with a minimal `PATH`, so `docker` must be found via `resolve_docker_command()` (probes `/opt/homebrew`, `/usr/local`, Docker Desktop) instead of relying on the environment.
- **macOS build tooling**: `build.py` uses `sips`/`iconutil` (icns), `patch_info_plist`, and `dmgbuild` (dmg). The `dmgbuild>=1.6,<2` dependency is darwin-only in the `packaging` extra; PyInstaller lives in the `packaging` extra too (not in runtime deps).
- **Entry point**: PyInstaller builds target the thin root `run.py` with `--paths src` (not `src/n8n_launcher/__main__.py`) so the package keeps its relative imports.