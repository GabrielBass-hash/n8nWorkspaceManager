# AGENTS.md — n8n-launcher

## What this is

Cross-platform Tkinter desktop app (Python 3.12+; **Windows, macOS, Linux/Ubuntu**) for managing isolated Docker-based n8n workspaces. Single package `n8n_launcher` under `src/`, split into subpackages by concern: `core/` (config, models, paths), `database/`, `docker/`, `git/`, `n8n/` (API, owner bootstrap, workflow sync), `platform/` (updater, browser, ports, shortcuts), `gui/` (app, dialogs, close/update flows, display), `workspaces/` (manager). Each file is one concern.

Releases ship natively per platform: `.dmg` (macOS, ad-hoc signed, styled via `dmgbuild` with app icon + full `Info.plist`), one-file `.exe` (Windows), and a one-file executable on Linux (a `.AppImage` is built out-of-band via `scripts/build_appimage.sh`). CI builds the three release artifacts in the `build` jobs; the AppImage is not part of CI.

The launcher version is a single-source SemVer (`MAJOR.MINOR.PATCH`) declared in `src/n8n_launcher/__init__.py` (`__version__`); `pyproject.toml` inherits it via `dynamic = ["version"]`, `scripts/build.py` embeds it, `platform/updater.py` compares it. Bump it with `python scripts/bump_version.py <patch|minor|major>` (or `--to X.Y.Z`; `--dry-run` previews; refuses on a dirty tree or an already-tagged version, `--force` bypasses). Releases are published only from `main` and only when the source version differs from the last git tag — no auto-bump, no `[skip ci]` reliance.

## Commands

```bash
# Setup (uv is the single package manager; .python-version pins 3.12)
uv sync                        # runtime + dev tooling (dev group installed by default)
uv sync --group packaging      # build tooling

# Run unit tests (default — excludes integration)
uv run pytest

# Run integration tests (requires Docker daemon running)
uv run pytest -m integration

# Run a single test file
uv run pytest tests/unit/docker/test_compose.py

# Run a single test
uv run pytest tests/unit/docker/test_compose.py::test_render_compose_managed -v

# Lint / format / typecheck (single config in pyproject.toml)
uv run ruff check .
uv run ruff format --check .
uv run basedpyright            # src/n8n_launcher/ is fully typed (0 errors); tests/ and scripts/ are excluded by policy

# All checks at once (same command CI runs; CI also runs pytest + build)
uv run pre-commit run --all-files

# Build desktop executable (macOS: .dmg via dmgbuild; Linux/Windows: one-file exe)
uv run python scripts/build.py

# Bump the launcher SemVer (single source: src/n8n_launcher/__init__.py)
uv run python scripts/bump_version.py patch|minor|major        # or: --to X.Y.Z
uv run python scripts/bump_version.py --dry-run patch          # preview without writing

# Additional Linux AppImage (after scripts/build.py produced dist/n8n-launcher)
bash scripts/build_appimage.sh
```

The stack is **uv** (dependency management + virtualenv), **Ruff** (lint + format), **basedpyright** (typecheck, `typeCheckingMode = "standard"`), **pytest + pytest-cov**, and **pre-commit**. Everything is configured in `pyproject.toml` (project metadata, `dependency-groups`, `[tool.ruff]`, `[tool.pytest.ini_options]`, `[tool.basedpyright]`); hooks live in `.pre-commit-config.yaml` (including `basedpyright` as a `local` hook, so `pre-commit run --all-files` covers the full stack). CI runs `uv sync --frozen`, `uv run pre-commit run --all-files`, `pytest` (matrix: ubuntu, macOS, windows) and `scripts/build.py`.

`basedpyright` is configured with `exclude = ["tests/**", "scripts/**"]`: every module under `src/` must typecheck, but the test fixtures and build scripts are deliberately loose (FakeTk stand-ins, MagicMock return values) and are not shipped, so they are out of scope. Do not widen the exclusion back to `src/`.

## Test structure

- `tests/unit/` — self-contained, no external services. Run by default. Mirrors `src/n8n_launcher/` layout: `tests/unit/<subpackage>/test_<module>.py`, with `tests/unit/gui/` holding a shared `conftest.py` (mock ticks, `SyncThread`, messagebox patches) and `helpers.py` (row/chip inspection helpers). `tests/unit/<subpackage>/test_manager.py` **must not** be used as a filename — the base name collides across `docker/`, `git/`, `workspaces/`; the files are `test_docker_manager.py`, `test_git_manager.py`, `test_workspace_manager.py`.
- `tests/integration/` — requires Docker. Auto-skips if Docker is unavailable. Uses a session-scoped `DockerManager` with a 180-second timeout.
- `tests/conftest.py` — provides `managed_workspace` fixture (a `Workspace` with `tmp_path` and managed DB).
- `tests/unit/n8n/` (API sync, owner bootstrap, workflow import), `platform/test_browser.py` (browser app-mode), `platform/test_shortcuts.py`, `gui/test_display.py` (display metadata), `git/test_git_manager.py`, `workspaces/test_workspace_manager.py`, `workspaces/test_ci.py` (harness/eligibility/selection), `workspaces/test_ci_runner.py` (meta-tests of the generated `validate.py`/`runner.py` scripts), `gui/test_ci_edit.py` (CI dialogs), and `database/test_database.py` cover the newer helpers. GUI behavior (list, dialogs, close/update flows) lives under `tests/unit/gui/`.

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
| `github/api.py` | GitHub REST client: `github_owner()`, `create_repo()`, `list_workflow_runs()`, `list_run_jobs()`, `fetch_job_logs()`, `dispatch_workflow()`; typed `GitHubError` with `status_code`. Repo paths keep a **literal** slash (`repos/owner/repo/...`) — `repo_url_path()` only encodes the two segments; URL-encoding the slash makes every Actions endpoint 404. |
| `github/auth.py` | Resolves the GitHub token without manual setup: `resolve_github_token(configured=None)` tries the optional persisted override, then `gh auth token`, then `git credential fill` for `github.com` (i.e. the same credential `git push` uses), with a 10 s timeout and interactive prompts disabled. Returns `None` when nothing is available; never logs or persists on its own. |
| `database/credentials.py` | Auto-creates n8n DB credentials; `data_db_target()` resolves the per-workspace DB target. |
| `n8n/workflows.py` | Workflow synchronization: `import_all()` creates in n8n every workflow JSON found in the workspace folder; optional background sync thread. |
| `git/manager.py` | Git operations for workspace workflow synchronization. Wraps `subprocess.run` calls to `git` (init, clone, add, commit, push, pull, remote management). All commands run in the workspace's `workflows_dir` with a 30 s timeout. |
| `workspaces/ci.py` | GitHub Actions CI harness for a workspace's *own* repo: remote-URL → `owner/repo` parsing, workflow discovery (mirrors import: `n8nPipelines/*.json` + root `*.json` minus a blocklist), pipeline eligibility (manual/schedule/pinned trigger + covered credentials), the machine-managed `.n8n-tests/tests.json` selection, and `render_harness()` which emits the workflow YAML plus a stdlib-only `validate.py`/`runner.py` (marker-commented, image pinned via token substitution). |
| `workspaces/ci_runs.py` | Read-only GitHub Actions run model: `RunSummary` / `JobSummary` / `PipelineResult` / `RunsSnapshot`, payload parsers (`run_summary`, `job_summary`, `parse_pipeline_lines` from the runner log) and `compose_snapshot()`. |
| `gui/ci_edit.py` | CI dialogs: `prompt_ci_workflows` (collapsed pipeline tree, per-pipeline counters, checkbox glyphs, ineligible rows greyed out, "Pousser maintenant ?" on save, "Déroulement" runs tab), `prompt_ci_credentials` (copies the credentials JSON to the clipboard for the `N8N_CI_CREDENTIALS` GitHub secret, then records name/type metadata only) and `prompt_run_ci` (ref to dispatch). |
| `gui/ci_runs.py` | "Déroulement" tab panel: `RunsPanel` renders a snapshot as a runs → jobs → pipelines tree, `runs_summary_text()` for the empty/error states, plus the `refresh` / `open_run` / `run` ("Lancer la CI") host callbacks. |
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

- **GitHub repo creation**: `github/api.py` wraps the GitHub REST API (`github_owner()`, `create_repo()`, typed `GitHubError` with `status_code`); the "Créer sur GitHub…" button (create wizard checkbox *"Créer le dépôt distant sur GitHub"* and `prompt_git_config` when no remote is set) collects a PAT via `prompt_github_create` (private by default, masked entry prefilled from `resolve_github_token`, "Détecter via gh CLI" fills `gh auth token`). The token is **never persisted by this flow**: it is used once for the API call, then `git/manager.py::git_seed_remote()` pushes with a one-shot tokenized URL (`push <https://token>@github.com/...> branch` — no `-u`, so the token never lands in `.git/config`); `remote_url` stored in `GitConfig` is the clean `https://github.com/<owner>/<name>.git` URL, keeping CI eligibility (`github_repo_path`) intact.

- **Token resolution (zero manual config)**: `github/auth.py::resolve_github_token()` is the single entry point used for both Actions runs reads and repo creation. It reuses the OS Git credential for `github.com` (the same one `git push` uses) or `gh auth token`, so nothing has to be configured. An optional override can be persisted once as `AppConfig.github_token` — via the tree dialog's "Se souvenir de ce token" checkbox or the context menu "Configurer le token GitHub…" — and then takes priority; the resolved token is otherwise kept in memory only. The Actions panel (`gui/ci_runs.py`, fed by `workspaces/ci_runs.py`) prompts for a token only when every source is empty.

- **Auto-pull on start**: before `_import_workflows()`, `git_pull()` (with `--rebase`) brings remote JSON changes into `workflows_dir` only when `git.enabled` is set and the folder is a real repo. Failures are logged as warnings and never block startup.
- **Auto-push on close**: the GUI close sequence exports n8n workflows to JSON (`export_all`), then `WorkspaceManager.sync_git()` stages everything (`git add -A`), commits with a timestamped message, and pushes. Push is skipped when there is nothing to commit *and* no unpushed commits. Push failures are logged, never raised.
- `git_push` uses `-u origin <branch>` so a branch created by the launcher (`main` via `git init` + `git branch -M main`) seeds a fresh (empty) remote on first push.
- `git_repo_status()` in `gui/display.py` probes the real repo with `git rev-parse --git-dir` — a bare `.git` directory is not enough.

## GitHub Actions CI

CI is optional per-workspace, requires `git.enabled` with a **GitHub** remote (`https://github.com/…`, `git@github.com:…`), and persists as `GitConfig.ci_enabled` / `GitConfig.ci_credentials` (name + type metadata only). All runtime logic lives in `workspaces/ci.py`; the GUI entry points are the CI chip and the four "Configurer les tests GitHub Actions…" / "Gérer les credentials CI…" / "Ouvrir les Actions GitHub…" / "Désactiver les tests CI" context menu items.

- **Enable** (`WorkspaceManager.enable_ci`) requires a real repo + GitHub remote, writes the workflow `.github/workflows/n8n-ci.yml` and `.n8n-tests/validate.py` / `runner.py` via `render_harness()`, commits and pushes. Disable (`disable_ci`) deletes the three generated files but **keeps** `.n8n-tests/tests.json` so the selection survives a disable/enable cycle.
- **Generated files are marker-commented** — `render_harness()` emits a `GENERATED_MARKER` ("n8n-launcher : généré — ne pas modifier à la main.") comment and pins the n8n image by substituting the `__N8N_IMAGE__` token (`docker.n8n.io/n8nio/n8n:<version>`, `_safe_image_tag` falls back to `2.40.0`). Templates are plain strings — never f-strings, so braces survive verbatim.
- **Workflow** (two jobs): `validate` runs `validate.py` (JSON shape, duplicate names, selection consistency); `test` runs `runner.py` with `N8N_IMAGE` (repository variable, defaulted to the pinned version) and the `N8N_CI_CREDENTIALS` secret.
- **Eligibility** (`ci.workflow_eligibility`): a pipeline is testable iff it has a manual trigger, a schedule trigger, or a *pinned* webhook/chat trigger (`pinData` on the trigger), and every non-pinned node's credential types are covered by `ci_credentials` metadata. Rationales are French, human-readable strings surfaced as greyed-out tooltips in the tree dialog.
- **Selection** is machine-managed: `tests.json` is `{"selected": ["n8nPipelines/x.json", …]}` — never hand-edit. The tree dialog ticks only eligible pipelines (checkbox glyphs '☑/☐', grey '·' for ineligible); `save_ci_selection()` writes it, commits, and pushes only when the user ticks "Pousser maintenant ?".
- **Manual run from the "Déroulement" tab**: the runs panel exposes a "Lancer la CI" button wired through `prompt_ci_workflows(runs_run=…)` → `RunsPanel.run_cb` → `LauncherApp._ci_runs_run`. It prompts for the ref (prefilled with `_ci_latest_branch`, i.e. the newest cached run's branch, else `main`) via `ci_edit.prompt_run_ci`, then dispatches `.github/workflows/n8n-ci.yml` with `GitHubClient.dispatch_workflow(repo, file, ref=…)` (`POST .../actions/workflows/<name>/dispatches`, success = 200/201/202/204; `<name>` must be the bare `n8n-ci.yml` — GitHub matches on the file name, not the `.github/workflows/` path, so `github/api.py::workflow_name()` strips it) on a background thread, sets the status bar and refreshes the panel. The button only appears when the host passes `runs_run` (GitHub remote present).
- **Credentials**: `set_ci_credentials()` persists name/type metadata only; `ci_credentials_payload(id)` (**requires a started workspace + api_key**) returns the name/type names for the dialog. The dialog reads the actual values once via `api.get_credential(id).data` and copies the JSON to the clipboard as the `N8N_CI_CREDENTIALS` GitHub secret. The generated `runner.py` keeps `OWNER_EMAIL=ci@n8n-launcher.test` / `OWNER_PASSWORD=CiPassw0rd-n8n9`, imports exports via `POST /api/v1/workflows` (dropping server-only fields), recreates credentials, triggers via `POST /rest/workflows/:id/run` (ManualRunDto), polls `GET /rest/executions/:id`, and exits nonzero on any failure. Missing secret at runtime = credential failure, never a crash.
- **Runner auth (gotcha sévère)**: the internal `/rest/*` calls authenticate via the `n8n-auth` session cookie set by `POST /rest/login` — n8n never returns a body token, and `Authorization: Bearer <JWT>` is rejected with 401. Because urllib/requests refuse to replay a cookie flagged `Secure` over plain HTTP, the runner starts its disposable container with `N8N_SECURE_COOKIE=false` (same setting as the managed Compose containers — without it `POST /rest/api-keys` 401s on every version). `trigger_run` accepts both a flat `{"executionId": ...}` and `{"data": {"executionId": ...}}` response.

## Gotchas

- **n8n 2.33.3 integration quirks**: Owner bootstrap uses internal REST endpoints (not `N8N_INSTANCE_OWNER_*` env vars). API keys require a `scopes` array and numeric `expiresAt`. Startup returns transient HTML until n8n is ready — bootstrap retries until it gets JSON.
- **Public API is schema-strict**: `POST /workflows` validates with `additionalProperties: false`; sending read-only/server export fields in the body returns HTTP 400 "must NOT have additional properties". Only the whitelisted create fields survive (`_create_payload`).
- **Password policy**: `validate_password()` mirrors n8n 2.33.3 — 8 to 64 chars, at least one digit and one uppercase letter. `test1234` is rejected.
- **Managed DB**: uses the local `postgres` service in Compose. `DbMode.NONE` omits the Postgres service entirely; legacy `"external"` configs fall back to `NONE` on load.
- **Named volumes required**: Each workspace declares both `n8ndata-<id>` and (managed mode) `pgdata-<id>` as named volumes.
- **Managed DB auto-generation**: If a workspace uses `DbMode.MANAGED` without explicit DB params, `WorkspaceManager` auto-generates `database_name`, `username`, and a random `password`.
- **Config file location**: Config lives at `platformdirs.user_config_dir("n8n-launcher")/config.json`. Compose files go under `config_dir/workspaces/<id>/compose.yml`.
- **Gitignored state**: `state/`, `workspaces/`, `*.env` files are local launcher state — never commit these. Note the bare `workspaces/` pattern also matches `src/n8n_launcher/workspaces/` and `tests/unit/workspaces/`; those packages are re-included via negated patterns in `.gitignore` so new files there stay trackable.
- **CI secrets**: n8n credential values never persist in the launcher — only `(name, type)` metadata in `config.json`; the values are copied to the clipboard once for the `N8N_CI_CREDENTIALS` GitHub secret.
- **Windows config permissions**: `0o600` chmod is skipped on Windows (`os.name == "nt"`).
- **macOS PATH in GUI-launched apps**: Finder/Dock/Launchpad start apps with a minimal `PATH`, so `docker` must be found via `resolve_docker_command()` (probes `/opt/homebrew`, `/usr/local`, Docker Desktop) instead of relying on the environment.
- **macOS build tooling**: `build.py` uses `sips`/`iconutil` (icns), `patch_info_plist`, and `dmgbuild` (dmg). The `dmgbuild>=1.6,<2` dependency is darwin-only in the `packaging` extra; PyInstaller lives in the `packaging` extra too (not in runtime deps).
- **Entry point**: PyInstaller builds target the thin root `run.py` with `--paths src` (not `src/n8n_launcher/__main__.py`) so the package keeps its relative imports.
