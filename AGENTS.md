# AGENTS.md — n8n-launcher

## What this is

Tkinter desktop app (Python 3.12+) for managing isolated Docker-based n8n workspaces. Single package `n8n_launcher` under `src/`, no subpackages. Flat module layout — each file is one concern.

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
pytest tests/unit/test_compose.py

# Run a single test
pytest tests/unit/test_compose.py::test_render_compose_managed -v

# Build desktop executable (requires Docker to be available for PyInstaller)
python -m pip install -e '.[packaging]'
python scripts/build.py
```

There is **no lint, typecheck, or formatter** configured in this repo. CI only runs `pytest` and `scripts/build.py`.

## Test structure

- `tests/unit/` — self-contained, no external services. Run by default.
- `tests/integration/` — requires Docker. Auto-skips if Docker is unavailable. Uses a session-scoped `DockerManager` with a 180-second timeout.
- `tests/conftest.py` — provides `managed_workspace` fixture (a `Workspace` with `tmp_path` and managed DB).

The `addopts` in `pyproject.toml` is `"-m 'not integration'"`, so plain `pytest` only runs unit tests.

## Code conventions

- **Unit test coverage is mandatory and maximal**: every public function must come with its unit test(s) in `tests/unit/test_<module>.py`. A new function without a test is rejected.
- **Respect generic code-writing conventions**: clarity, small functions, no dead code, no reinvented stdlib helpers, explicit typing (`from __future__ import annotations` is used repo-wide).
- **Document the code**: every module has a docstring, every function/class has a docstring, and every non-obvious variable/block gets a comment explaining the *why* (not the *what*).

## Key architecture

| Module | Role |
|---|---|
| `__main__.py` | Entry point. Loads config, runs first-launch wizard if needed, then opens Tkinter GUI. Registers `atexit` to stop all workspaces. |
| `workspace_manager.py` | Central controller — CRUD + start/stop lifecycle. Orchestrates compose rendering, Docker, migrations, owner bootstrap, DB credentials. |
| `compose.py` | Renders Docker Compose YAML. Each workspace gets its own isolated Compose project named `n8n-ws-<id>`. |
| `docker_manager.py` | Thin subprocess wrapper around `docker compose` commands. Uses `-p <project>` and `-f <file>` for isolation. |
| `config.py` | JSON config store with atomic writes (temp file → rename) and `0o600` perms on non-Windows. |
| `paths.py` | Uses `platformdirs` for cross-platform config/log/runtime directories under `n8n-launcher`. |
| `owner_setup.py` | n8n owner bootstrap via internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`). |
| `gui.py` | Tkinter GUI (~791 lines). Dark theme, manages workspace list view and actions. |
| `database.py` / `db_manager.py` | Migration detection and runner (executes SQL via `docker compose exec psql`). |
| `browser.py` | Opens n8n in browser app mode (`--app` flag for Chrome/Edge/Brave/Chromium, falls back to `webbrowser.open`). |

## Gotchas

- **n8n 2.33.3 integration quirks**: Owner bootstrap uses internal REST endpoints (not `N8N_INSTANCE_OWNER_*` env vars). API keys require a `scopes` array and numeric `expiresAt`. Startup returns transient HTML until n8n is ready — bootstrap retries until it gets JSON.
- **Compose dollar-sign escaping**: `escape_compose_value()` doubles `$` to prevent Docker Compose from interpolating secret values as variables.
- **Named volumes required**: Each workspace declares both `n8ndata-<id>` and (managed mode) `pgdata-<id>` as named volumes.
- **Managed DB auto-generation**: If a workspace uses `DbMode.MANAGED` without explicit DB params, `WorkspaceManager` auto-generates `database_name`, `username`, and a random `password`.
- **Config file location**: Config lives at `platformdirs.user_config_dir("n8n-launcher")/config.json`. Compose files go under `config_dir/workspaces/<id>/compose.yml`.
- **Gitignored state**: `state/`, `workspaces/`, `*.env` files are local launcher state — never commit these.
- **Windows config permissions**: `0o600` chmod is skipped on Windows (`os.name == "nt"`).
