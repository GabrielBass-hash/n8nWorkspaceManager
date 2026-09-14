# n8n-launcher

Cross-platform desktop launcher for isolated n8n workspaces.

## Status

The current slice provides domain models, platform-specific paths, persistent configuration, Compose rendering, Docker lifecycle commands, database migration/validation helpers, port allocation, browser app mode, workspace CRUD/lifecycle orchestration, a Tkinter GUI, the first-launch setup wizard, the n8n public-API client with owner bootstrap, workflow sync, and PyInstaller packaging. Unit and integration test suites are green.

## n8n 2.33.3 integration notes

Findings from the integration spikes, baked into the code:

- The owner bootstrap uses the internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`) because n8n 2.33.3 requires a `firstName`/`lastName` for the owner, an 8-64 character password, and returns the session as an HttpOnly `n8n-auth` cookie rather than a body token.
- API keys now require a `scopes` array and a numeric `expiresAt` (`0` = no expiry); the launcher requests the six workflow scopes it needs and reads the key from `rawApiKey`.
- During startup n8n answers with transient HTML pages (`n8n is starting up`, `Cannot POST ...`); `OwnerSetup.bootstrap` retries until the API responds with JSON or reports an already-configured owner.
- Because of these constraints the launcher keeps the REST bootstrap instead of `N8N_INSTANCE_OWNER_*` env vars (which would write the owner password to the Compose file). `hash_owner_password` (bcrypt) stays available for a future hashed-env evaluation.
- Named Compose volumes must be declared: each workspace declares both `n8ndata-<id>` and (managed mode) `pgdata-<id>`.
- `docker compose ps --format json` is validated by the integration suite to derive per-service state.
- Browser app mode uses the `--app` flag of Chrome/Edge/Brave/Chromium when one is installed, and falls back to `webbrowser.open`. Flag construction is unit-tested; real flags are exercised on the three-OS e2e pass.

## Development

Requires Python 3.12 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

Integration tests are opt-in:

```bash
pytest -m integration
```

Build the desktop executable locally with:

```bash
python -m pip install -e '.[packaging]'
python scripts/build.py
```

The launcher is independent of the source repository that inspired some of its API and workflow-sync boundaries. It does not reuse that repository's weather database schema, runtime state, or Docker sync service.
# n8nWorkspaceManager
# n8nWorkspaceManager
