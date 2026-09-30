# n8n-launcher

Cross-platform launcher for isolated n8n workspaces.

> **The interface is gone.** Tkinter has been removed and the launcher is
> headless: it manages workspaces, Git, remote deployments and CI, but it has
> nothing to draw. `run_gui()` raises `NotImplementedError` on purpose. The
> logic the windows used to drive is all still here and tested — see
> [`MIGRATION.md`](MIGRATION.md) for what phase 2 rebuilds over it.

## Status

The launcher provides domain models, platform-specific paths, persistent configuration, Compose rendering, Docker lifecycle commands, database migration/validation helpers, port allocation, browser app mode, workspace CRUD/lifecycle orchestration, Git synchronization, GitHub repo creation, GitHub Actions CI harness generation and live runs, remote server deployment, an event journal, desktop shortcuts, and PyInstaller packaging.

Everything that used to be reachable from a window is a **callable** now. Creation collects a `CreatePlan` (`workspaces/dialogs.py`), the update flow is `UpdateController` with injected questions (`platform/update_flow.py`), shutdown is `CloseSequence` (`workspaces/close.py`), and the sizing rules the tables obeyed are pure functions of their content (`gui_utils/text.py`). Unit and integration test suites are green; there is no GUI suite, because there is no GUI.

What runs today: `main()` opens the journal, finds no interface to show, logs that, and shuts every workspace down cleanly on the way out.

## Key concepts

### Workspace isolation

Each workspace is a user-defined n8n instance bound to a folder of workflow exports. The launcher creates an isolated Docker Compose project named `n8n-ws-<id>`, writes the Compose YAML under `config_dir/workspaces/<id>/compose.yml`, and uses named volumes (`n8ndata-<id>`, and `pgdata-<id>` in managed mode).

### Database modes

- **`MANAGED`** — a local Postgres service runs inside the Compose project. When DB parameters are missing, the launcher fills them in: `data` / `n8ndata` for database/user (fixed defaults), a random password; Migrations in `db/migrations/` are detected and applied on startup.
- **`NONE`** — n8n runs without any database service.

### Git synchronization

Git is optional per-workspace and configured at creation or later through `prompt_git_config` / `workspaces/dialogs.py`. Every launcher-created repo works on its own branch **`dev`** (legacy `main`/`n8n/*` branches are renamed once on first start); git lifecycle calls are serialized per-workspace. When enabled with a remote:

- **Auto-pull on start** — `git pull --rebase` brings remote JSON changes into the workspace folder before workflow import.
- **Auto-push on close** — n8n workflows are exported to JSON, staged, committed with a timestamped message, and pushed. Push is skipped when there is nothing to commit *and* no unpushed commits. Server-side push rejections are retried once.
- **Push failures never block**: a `git_push_failed` flag is persisted and surfaced as a warning chip and dialog; failures are logged, never raised.

### GitHub repo creation

When no remote is set, the "Créer sur GitHub…" flow creates the repository via the GitHub REST API, stores the clean `https://github.com/<owner>/<name>.git` URL, and seeds the remote with a one-shot tokenized URL (`https://<token>@github.com/...`) so the token never lands in `.git/config`. CI eligibility (`github_repo_path`) stays intact because the stored URL is unencoded.

### Remote server deployment

Deployment to a production server is optional per-workspace (persisted as `ServerConfig`) and driven from the same git repo: `publish()` pushes the workspace **`dev`** branch to the server's bare repo as its `main` (the production reference), then waits for the server's `post-receive` hook to redeploy the stack and confirm through a marker file (`last-deploy.json`).

- `install_server` creates the bare repo (`git init --bare`), ships a generated `post-receive` hook and `deploy.py` (marker-commented, template strings), all transferred atomically (`write_remote_file`: `cat > path.tmp && mv`).
- The server Compose file adds a top-level `name: n8n-ws-<id>` so the project is stable regardless of the checkout directory; the hook runs `docker compose -f "$WORKFLOW/compose.yml" -p "$PROJECT" up -d` and every remote import uses that pinned project.
- `deploy.py` recreates credentials and imports the pipelines — deduplicating by basename so root mirror copies never upload a workflow twice — but **never runs `db/migrations/*.sql`**: SQL migrations stay on the launcher-managed local stack. Secrets travel as a `secrets.json` scp'd at publish; nothing is committed to git.

### GitHub Actions CI

Each workspace with Git enabled and a **GitHub** remote can run its exported pipelines as GitHub Actions tests. Runs are triggered manually or by pushes on the per-workspace `dev` branch (the repo default branch is set to `dev` on enable):

- **Enabling** (`enable_ci()`) generates three files in the workspace repo — `.github/workflows/n8n-ci.yml`, `.n8n-tests/validate.py` and `.n8n-tests/runner.py` — plus the machine-managed selection `.n8n-tests/tests.json`. Every generated file carries the marker *« n8n-launcher : généré — ne pas modifier à la main »*. Changes are committed and pushed on enable.
- **Pipeline eligibility** (`workflow_eligibility`): a pipeline is testable iff it has a manual trigger, a schedule trigger, or a *pinned* webhook/chat trigger (`pinData`), and every non-pinned node's credential types are covered by the recorded CI credentials. Each ineligibility carries a human-readable reason.
- **Credentials**: values are read once from the running workspace's n8n instance and copied to the clipboard as a JSON document for the `N8N_CI_CREDENTIALS` secret. The launcher keeps only `(name, type)` metadata, never the values.
- **Runs** (`workspaces/ci_runs.py`): a runs → jobs → pipelines model parsed from the GitHub REST payloads and the runner's log lines, refreshed on demand. Dispatching is `dispatch_workflow()` (`workflow_dispatch` on a chosen ref, prefilled with the latest run's branch); the generated workflow's `concurrency: cancel-in-progress` cancels a run already in flight.
- **Disabling** removes only the generated harness files and keeps `tests.json`.

### GitHub token management

Tokens are resolved automatically from the OS Git credential helper (the same credential `git push` uses) or `gh auth token`, with a 10 s timeout, and never logged or persisted on its own. An override can be persisted once as `AppConfig.github_token`; otherwise the resolved token lives in memory only. Nothing has to be typed into a dialog, and nothing has to be configured.

### n8n 2.33.x integration notes

Findings from the integration spikes, baked into the code:

- The owner bootstrap uses internal REST endpoints (`/rest/owner/setup`, `/rest/login`, `/rest/api-keys`) because n8n 2.33.x requires a `firstName`/`lastName` for the owner, an 8-64 character password, and returns the session as an HttpOnly `n8n-auth` cookie rather than a body token.
- API keys require a `scopes` array and a numeric `expiresAt` (`0` = no expiry); the launcher requests the six workflow scopes it needs and reads the key from `rawApiKey`.
- During startup n8n answers with transient HTML pages (`n8n is starting up`, `Cannot POST ...`); `OwnerSetup.bootstrap` retries until the API responds with JSON or reports an already-configured owner.
- Because of these constraints the launcher keeps the REST bootstrap instead of `N8N_INSTANCE_OWNER_*` env vars. `hash_owner_password` (bcrypt) stays available for a future hashed-env evaluation.
- Named Compose volumes must be declared: each workspace declares both `n8ndata-<id>` and (managed mode) `pgdata-<id>`.
- `docker compose ps --format json` is validated by the integration suite to derive per-service state.
- Browser app mode uses the `--app` flag of Chrome/Edge/Brave/Chromium when one is installed, and falls back to `webbrowser.open`. Flag construction is unit-tested; real flags are exercised on the three-OS e2e pass.

### macOS PATH in bundle-launched apps

Finder/Dock/Launchpad start apps with a minimal `PATH`, so `docker` is resolved via `resolve_docker_command()` — probing `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin`, `/usr/local/sbin`, and `/Applications/Docker.app/Contents/Resources/bin` before falling back to `PATH` lookup — instead of relying on the environment `PATH`.

### Password policy

`validate_password()` mirrors n8n 2.33.x — 8 to 64 chars, at least one digit and one uppercase letter. `test1234` is rejected.

### Public API is schema-strict

`POST /workflows` validates with `additionalProperties: false`; sending read-only/server export fields returns HTTP 400. The launcher uses a whitelist (`_create_payload`) — only `name`, `nodes`, `connections`, `settings`, `staticData`, `pinData`, `nodeGroups`, `projectId`, `parentFolderId` survive.

## Development

Requires Python 3.12 or newer. The project uses **uv** as the single package manager; every command below runs inside the uv-managed virtualenv.

```bash
uv sync                        # runtime + dev tooling
uv run pytest                  # unit tests (excludes integration)
```

Lint, format, and typecheck (Ruff + basedpyright + pre-commit). The single pre-commit command runs exactly what CI runs for static checks:

```bash
uv run pre-commit run --all-files
```

The underlying tools are also available directly:

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Integration tests are opt-in (require Docker):

```bash
uv run pytest -m integration
```

Build the distribution locally:

```bash
uv sync --group packaging
uv run python scripts/build.py
```

On macOS the onedir `.app` bundle embeds the app icon and a full `Info.plist` (display name, version, retina support), is ad-hoc signed, and is packaged into a styled drag-and-drop `.dmg` rendered by `dmgbuild`. The bundle ships a headless launcher: it will log that no interface is available and exit, which is why the CLI form is the useful one today.

On Linux, additionally build the AppImage:

```bash
bash scripts/build_appimage.sh
```

| Platform | Output |
|---|---|
| macOS | `dist/n8n-launcher-macos.dmg` |
| Linux | `dist/n8n-launcher` or `dist/n8n-launcher-linux-x86_64.AppImage` |
| Windows | `dist/n8n-launcher.exe` |

## Tests et CI

### Lancer les tests en local

```bash
uv sync            # installe les deps runtime + dev
uv run pytest      # tests unitaires (exclut les tests integration)
```

Les intégrations, opt-in et nécessitant Docker, se lancent avec `uv run pytest -m integration`.

### Ce que fait la CI

Le fichier `.github/workflows/ci.yml` enchaîne 4 jobs sur chaque PR (branches `dev`/`main`) :

1. **`lint`** — `ubuntu-latest`. `ruff check .`, `ruff format --check .` et `basedpyright`. Échoue si le code est sale ; bloque tous les jobs suivants.
2. **`test`** — matrix `ubuntu` / `windows` / `macos` (Python 3.12, `fail-fast: false`). Installe les deps via `uv sync --frozen`, lance `pytest` et vérifie la couverture `--cov-fail-under=80`. Chaque OS upload son rapport JUnit (`pytest-report-<os>.xml`). Aucun toolkit graphique n'est installé ni invoqué : la suite tourne telle quelle sur un runner sans écran, `xvfb-run` n'a plus lieu d'être.
3. **`integration`** — `ubuntu-latest`, en parallèle de `test`. Lance `tests/integration` (Docker requis) : déploiement distant (`install_server`, `publish`, hook `post-receive`) et statut d'exécution distant.
4. **`build`** — après `test`, produit le `.dmg`/`.exe`/binaire et les upload.

Les jobs **`parity`** et les suites `tests/test_structure.py` / `tests/test_parity.py` ont été supprimés avec l'interface : ils capturaient l'arbre des widgets sous un Xvfb et comparaient les trois OS entre eux, ce qui n'avait de sens que tant que ces arbres existaient.

## Installation

### macOS — no paid Apple Developer account needed

The macOS build is ad-hoc signed but **not notarized**. Three free routes avoid the Gatekeeper block:

**1. Terminal installer (recommended, reliable on macOS 15+).** Downloading with `curl` never adds the quarantine attribute:

```bash
curl -fsSL https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest/download/install_macos.sh | bash
```

Or install a local build:

```bash
bash scripts/install_macos.sh -f dist/n8n-launcher-macos.dmg
```

**2. De-quarantine an app you already downloaded:**

```bash
bash scripts/dequarantine.sh
bash scripts/dequarantine.sh /path/to/n8n-launcher.app
```

**3. Manual bypass (no terminal).** Right-click `n8n-launcher.app` → *Open* → *Open*. macOS 15+ re-applies the quarantine after a relaunch, so prefer option 1.

- **Windows**: run or pin `n8n-launcher.exe` from the taskbar / Start menu.
- **Linux**: make the AppImage executable and launch it — it integrates with your desktop environment's app menu.

## Releases

The launcher version is a strict SemVer (`MAJOR.MINOR.PATCH`), defined in a single place — `__version__` in `src/n8n_launcher/__init__.py` (currently **5.0.3**). `pyproject.toml` inherits it (`dynamic = ["version"]`), `scripts/build.py` embeds it into the bundle and `platform/updater.py` compares it against GitHub releases. Bump it by hand in `__init__.py` before a release.

Releases are published **only from `main`**. When a push to `main` carries a new source version, the release workflow tags it (`v<version>`), runs the tests, builds the per-OS distribution, and attaches all three artifacts to a GitHub Release. Pushes that do not change the version are skipped.
