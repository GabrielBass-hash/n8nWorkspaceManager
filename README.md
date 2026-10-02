# n8n-launcher

Cross-platform launcher for isolated n8n workspaces. Python 3.12+, Windows /
macOS / Linux.

**The interface is PySide6.** It is confined to `src/n8n_launcher/gui/` —
every other module imports no toolkit at all, and a unit test enforces that. The
launcher still runs headless: without a display it logs *Interface
indisponible* and exits through the same ordered shutdown as a GUI session.

## Current behaviour

- A run takes the single-instance lock, resolves the Docker CLI, opens the
  event journal, loads the config and opens the window.
- **First launch** (no config) opens the wizard: it checks Docker, collects the
  owner e-mail, an owner password and the work directory, then writes
  `launcher.db`. Cancelling the wizard ends the run cleanly.
- A config that exists but cannot be read is **never** treated as a first
  launch: it is backed up to `launcher.db.corrupt-<timestamp>`, named in the
  journal, and the run stops.
- Closing the window stops every workspace and writes the closing journal
  event, through the same `finally` as every other exit path.
- `n8n-launcher --self-test` builds the window once over a throwaway config and
  exits 0. It is what the release pipeline runs on the finished artifact.

The window shows one card per workspace (status, port, folder). Double-click a
card to open its n8n instance — opening a stopped workspace starts it — and
click its **status pill** to stop a live one; a right-click offers *Ouvrir* /
*Arrêter* / *Supprimer…*. The header keeps *Nouveau* and an *Arrêter* fallback
for the selected card. Long operations (Docker, git) run on
`QThreadPool` workers, and the manager is observed through a plain
`WorkspaceObserver` protocol — a view renders what it is fed and calls back, it
never does its own I/O in a paint handler.

The decisions those pages used to make are plain callables, still unit-tested
without a window: `CreatePlan` (`workspaces/dialogs.py`), `UpdateController`
with injected questions (`platform/update_flow.py`), `CloseSequence`
(`workspaces/close.py`) and the table sizing rules (`gui_utils/text.py`).

## Key concepts

### Workspace isolation

Each workspace is a user-defined n8n instance bound to a folder of workflow
exports. The launcher creates an isolated Compose project `n8n-ws-<id>`, writes
the Compose YAML to `<config_dir>/workspaces/<id>/compose.yml`, and declares the
named volumes `n8ndata-<id>` and — in managed mode — `pgdata-<id>`.

### Database modes

- **`MANAGED`** — a Postgres service runs inside the Compose project. Missing
  DB parameters are filled in: database `data`, user `n8ndata`, a random
  password. The SQL files in `db/migrations/` are detected and applied by
  `start()`.
- **`NONE`** — n8n runs with no database service in the Compose project.

A legacy `"external"` DB mode falls back to `NONE` on load.

### Git synchronization

Optional per workspace, as `Workspace.git` (`GitConfig(enabled, remote_url,
branch)`). Every launcher-created repository works on its own branch **`dev`**
(`git -c init.defaultBranch=dev init`); a legacy `main`/`n8n/*` branch is
renamed to `dev` once on the first `ensure_running()` and `GitConfig.branch` is
persisted. Git lifecycle calls run under a reentrant per-workspace lock
(`.n8n-launcher.git.lock`, gitignored), so the automatic pull on start and the
automatic push on close can never interleave.

- **Auto-pull on start** — `git pull --rebase` runs before the workflow import,
  and only when `git.enabled` is set and the folder is a real repository.
  Failures are warnings; they never block the start.
- **Auto-push on close** — the close sequence exports the workflows, then
  `git add -A`, a timestamped commit and a push. The push is skipped when there
  is nothing to commit *and* no unpushed commit. `git_push` uses `-u origin
  <branch>` so the first push seeds an empty remote, and retries once on a
  server-side rejection (`! [rejected]`, stale info). Push failures are logged,
  never raised.
- **`dev` is the work branch and the CI branch. `main` exists only as the
  production reference on the server's bare repository** — `publish` maps
  `dev → main`.

### GitHub

**Repository creation** goes through `create_repo()` on the GitHub REST API. The
clean URL `https://github.com/<owner>/<name>.git` is what gets persisted, so CI
eligibility (`github_repo_path`) keeps working; the remote is then seeded with a
one-shot tokenized URL (`https://<token>@github.com/…`, no `-u`) so the token
never reaches `.git/config`.

**Token resolution** is automatic: `resolve_github_token()` tries the optional
persisted override (`AppConfig.github_token`), then `gh auth token`, then
`git credential fill` for `github.com` — the same credential `git push` uses —
with a 10 s timeout and prompts disabled. It returns `None` when nothing is
available. Nothing has to be configured, and the token is never logged.

### Workflow import and export

`import_all()` (on start) scans `n8nPipelines/` **and** the workspace root for
`*.json`, and creates each workflow through the public API. Files without a
`nodes` key, unreadable files, and workflows already present (by id **or** name)
are skipped. `_create_payload()` is a whitelist of the fields the create schema
accepts (`name`, `nodes`, `connections`, `settings`, `staticData`, `pinData`,
`nodeGroups`, `projectId`, `parentFolderId`, `settings` defaulting to `{}`);
everything else (`active`, `triggerCount`, `shared`, …) is dropped.

`export_all(mirror=…)` (on close, stop-with-sync and publish) refreshes
`n8nPipelines/<name>-<id>.json` for every workflow, and, when *mirror* is given,
writes the **same bodies as identical copies at the workspace root** — because
import and the CI harness both read the two locations. Cleanup in the mirror is
restricted to launcher-named files (`EXPORT_NAME_RE = .+-\d+\.json$`), so
`package.json` and hand-written exports are never touched.

Consequently every consumer of both locations dedups by basename in favour of
`n8nPipelines/`: `collect_workflows()` in `workspaces/ci.py` and
`collect_workflow_files()` in the generated remote `deploy.py`. Without that
dedup the same workflow would be imported or uploaded twice.

### Remote server deployment

Optional per workspace, persisted as `Workspace.server` (`ServerConfig`).
`publish()` pushes `dev:main` to the server's bare repository and waits for the
`post-receive` hook to confirm through a marker file.

- `install_server()` probes the remote tools, initializes the bare repository
  (`git init --bare`) *before* writing the hook — the hook has to exist at push
  time — and ships the generated `post-receive` hook and `deploy.py` through
  `write_remote_file`, which writes to `<path>.tmp` and `mv -f`s it into place.
- The remote Compose file carries a top-level `name: n8n-ws-<id>` so the project
  is stable regardless of the checkout directory, and the hook runs
  `docker compose -f "$WORKFLOW/compose.yml" -p "$PROJECT" up -d` before
  executing `deploy.py`. Both the hook and the script write `last-deploy.json`
  (`{sha,status,error,at}`) atomically.
- `deploy.py` recreates the credentials and imports the pipelines with the same
  basename dedup, and deliberately **never runs `db/migrations/*.sql`**: SQL
  migrations stay on the launcher-managed local stack. Secrets travel as a
  `secrets.json` scp'd at publish and are never committed.
- Workflow activation is best effort and goes through the deploy's owner
  session (`/rest/workflows/{id}` then `/rest/workflows/{id}/activate`), because
  the public activate endpoint is refused by n8n 2.x for a workflow the key's
  user cannot activate. A refusal is logged, never raised.
- The publish push uses a 300 s timeout instead of the 30 s every other git
  call gets, because the hook runs Compose up and the import synchronously.

### GitHub Actions CI

Optional per workspace; it requires `git.enabled` with a GitHub remote and is
persisted as `GitConfig.ci_enabled` / `GitConfig.ci_credentials`.

- **Enable** writes `.github/workflows/n8n-ci.yml`, `.n8n-tests/validate.py` and
  `.n8n-tests/runner.py` (all marker-commented as generated, the n8n image
  pinned through the `__N8N_IMAGE__` token), commits, pushes, and sets the
  repository default branch to `dev`. **Disable** removes those three files and
  keeps `.n8n-tests/tests.json`, so the selection survives a disable/enable
  cycle.
- The workflow has two jobs — `validate` (JSON shape, duplicate names, selection
  consistency) and `test` (the runner) — triggered by `workflow_dispatch` and by
  pushes on `dev`.
- **Eligibility**: a pipeline is testable iff it has a manual trigger, a
  schedule trigger, or a *pinned* webhook/chat trigger (`pinData`), and every
  non-pinned node's credential types are covered by the recorded CI credentials.
  Each verdict carries a human-readable French reason.
- **Selection** is machine-managed: `tests.json` is `{"selected": [...]}`, never
  hand-edited.
- **Credentials**: only `(name, type)` metadata is persisted. `ci_credentials_payload(id)`
  requires a started workspace with an `api_key` and reads the values once to
  build the `N8N_CI_CREDENTIALS` secret.
- **Runs** are read through `github/api.py` (`list_workflow_runs`,
  `list_run_jobs`, `fetch_job_logs`); `dispatch_workflow()` fires a
  `workflow_dispatch` on a chosen ref. GitHub matches the dispatch on the bare
  file name, so the `.github/workflows/` prefix is stripped first.

### n8n integration notes

Facts baked into the code:

- Owner bootstrap uses the internal REST endpoints (`/rest/owner/setup`,
  `/rest/login`, `/rest/api-keys`) rather than `N8N_INSTANCE_OWNER_*` env vars.
  n8n returns the session as an HttpOnly `n8n-auth` cookie, never as a body
  token, and `Authorization: Bearer <JWT>` on `/rest/*` is rejected.
- API keys require a `scopes` array and a numeric `expiresAt` (`0` = no expiry).
  `n8n/scopes.py` is the single list used both by the local bootstrap and by the
  generated remote `deploy.py`.
- During startup n8n answers with transient HTML (`n8n is starting up`,
  `Cannot POST …`), so the bootstrap retries until it gets JSON or learns the
  owner already exists.
- `POST /api/v1/workflows` validates with `additionalProperties: false`; sending
  a server-only field returns HTTP 400.
- The generated CI runner starts its disposable container with
  `N8N_SECURE_COOKIE=false`, because urllib refuses to replay a cookie flagged
  `Secure` over plain HTTP, and without it `POST /rest/api-keys` 401s on every
  version. Listing credentials through the public API additionally needs the
  `credential:list` scope, or `GET /api/v1/credentials` answers 403.
- `GET /rest/executions` returns no `finished` flag, so it is derived from n8n's
  own vocabulary (`TERMINAL_EXECUTION_STATUSES` ⇒ `True`,
  `PENDING_EXECUTION_STATUSES` ⇒ `False`, anything else ⇒ `None`, an explicit
  boolean always winning). A meta-test asserts the generated server parser and
  the launcher parser agree for every status.
- `docker ps` labels are not always a map: `parse_container_labels()` accepts an
  object, a JSON string, and the flat `k=v,k=v` string Compose 2.35 emits on
  Docker Desktop.

### Password policy

`core/first_launch.py::validate_password` mirrors n8n's rule — 8 to 64
characters, at least one digit and at least one uppercase letter — and the
wizard refuses to continue until it passes. It is a launcher-side mirror, not a
source of truth: n8n still enforces its own policy when the owner account is
set, so the two must be re-derived together when either moves.

### macOS PATH in bundle-launched apps

Finder, Dock and Launchpad start an app with a minimal `PATH`, so `docker` is
located by `resolve_docker_command()` — probing `/opt/homebrew/bin`,
`/opt/homebrew/sbin`, `/usr/local/bin`, `/usr/local/sbin` and
`/Applications/Docker.app/Contents/Resources/bin` before falling back to a
`PATH` lookup — rather than read from the environment.

## Development

Python 3.12+. **uv** is the single package manager; every command below runs
inside the uv-managed virtualenv.

```bash
uv sync                        # runtime + dev tooling (dev group by default)
uv sync --group packaging      # build tooling

uv run pytest                  # unit tests (integration excluded by default)
uv run pytest -m integration   # integration tests (Docker daemon required)
uv run pytest tests/unit/docker/test_compose.py::test_render_compose_managed

uv run pre-commit run --all-files   # ruff check + ruff format + basedpyright
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

`basedpyright` runs in `typeCheckingMode = "standard"` and excludes
`tests/**` and `scripts/**` only: every module under `src/n8n_launcher/` must
typecheck. The coverage floor is `--cov-fail-under=80`, the same one CI uses.

## Distribution

| Platform | Artifact | How |
|---|---|---|
| macOS | `dist/n8n-launcher-macos.dmg` | `scripts/build.py`: PyInstaller onedir `.app` (icon via `sips`/`iconutil`, full `Info.plist`) → ad-hoc signature → `dmgbuild` styled DMG |
| Linux | `dist/n8n-launcher/` and `dist/n8n-launcher-linux-x86_64.AppImage` | `scripts/build.py` then `bash scripts/build_appimage.sh` |
| Windows | `dist/n8n-launcher/` | `scripts/build.py`: PyInstaller onedir |

Every platform ships an **onedir** directory, never a single file: Qt has to
find its platform plugin at runtime, and a one-file archive would unpack the
whole bundle into a temporary directory on every launch. The AppImage *is* the
single-file Linux artifact — it is the packager, not PyInstaller.

`scripts/build.py` refuses to finish a build whose Qt platform plugin is
missing (`verify_qt_bundle`, also reachable as
`python scripts/build.py --verify <dist-root>`): a bundle without it starts,
prints nothing and dies with no window, which is the one packaging failure a
packager cannot see. The macOS signature is applied deepest-first (nested
dylibs, then Qt bundles, then the app) instead of with the deprecated
`codesign --deep`.

PyInstaller targets the thin root `run.py` with `--paths src`, so the package
keeps its relative imports.

The macOS build is ad-hoc signed and **not notarized**, so Gatekeeper blocks it
when it carries the quarantine attribute a browser or Finder adds on download.
The release publishes `n8n-launcher-macos.dmg`; three ways to install it:

1. **curl, which never quarantines.** Fetch the DMG from the release and run the
   repo's installer against it:

   ```bash
   curl -fLO https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest/download/n8n-launcher-macos.dmg
   bash scripts/install_macos.sh -f n8n-launcher-macos.dmg
   ```

   The same script installs a local build:
   `bash scripts/install_macos.sh -f dist/n8n-launcher-macos.dmg`.
2. **De-quarantine an app you already downloaded:**
   `bash scripts/dequarantine.sh` (optionally with a path). macOS 15+ re-applies
   the attribute after a relaunch, so prefer 1.
3. **Manual bypass, no terminal.** Right-click `n8n-launcher.app` → *Open* →
   *Open*.

## Tests et CI

### En local

```bash
uv sync            # deps runtime + dev
uv run pytest      # unitaires (les intégrations sont exclues par addopts)
```

`addopts` is `-m 'not integration' --cov=src/n8n_launcher
--cov-report=term-missing`, so a plain `pytest` runs the unit suite with
coverage. The integrations need a Docker daemon:
`uv run pytest tests/integration -m integration`.

### Ce que fait la CI

`.github/workflows/ci.yml` déclenche 4 jobs sur chaque PR visant `dev` ou
`main` :

1. **`lint`** — `ubuntu-latest` : `ruff check .`, `ruff format --check .` puis
   `basedpyright`. Échoue si le code est sale ; bloque `test` et `integration`.
2. **`test`** — matrix `ubuntu-latest` / `macos-latest` / `windows-latest`,
   `fail-fast: false` : `uv sync --frozen`, `pytest --cov-fail-under=80
   --junitxml=pytest-report-<os>.xml`, upload du rapport par OS. Qt tourne avec
   `QT_QPA_PLATFORM=offscreen`, donc ni `python3-tk` ni `xvfb` : la suite
   s'exécute telle quelle sur un runner sans écran. La jambe ubuntu installe
   les librairies systèmes dont Qt a besoin (`libgl1`, `libegl1`,
   `libxkbcommon0`, …).
3. **`integration`** — `ubuntu-latest`, en parallèle de `test` : `pytest
   tests/integration -m integration`, `timeout-minutes: 60`. Linux seulement,
   parce que le sandbox sshd est un conteneur Linux et que le harness atteint le
   n8n déployé par un tunnel SSH inverse. Le runner ubuntu a déjà le socket
   Docker par défaut, donc aucun `N8N_LAUNCHER_TEST_DOCKER_SOCKET` n'est requis.
4. **`build`** — après `test` : `scripts/build.py` sur les trois OS,
   `scripts/build.py --verify <dist-root>` (plugin Qt présent),
   `scripts/build_appimage.sh` sur la jambe ubuntu, `--self-test` sur chaque
   artefact construit *et* sur l'AppImage, `install_macos.sh -n` sur la jambe
   macOS, puis upload de chaque
   artifact.

Les jobs **`parity`** et les suites `tests/test_structure.py` /
`tests/test_parity.py` ont été supprimés avec l'interface : ils capturaient
l'arbre des widgets sous un Xvfb et comparaient les trois OS entre eux.

`.github/workflows/release.yml` ne se déclenche que sur un push vers `main` :
il lit `__version__`, la compare au dernier tag, et ne tag/build/publie que si
la version a bougé. Il construit la même matrix que `build` et joint tous les
artifacts à la GitHub Release.

## Releases

The version is a strict SemVer (`MAJOR.MINOR.PATCH`) declared in exactly one
place — `__version__` in `src/n8n_launcher/__init__.py` (currently **6.0.0**).
`pyproject.toml` inherits it through `dynamic = ["version"]`, `scripts/build.py`
embeds it in the bundle, and `platform/updater.py` compares it against the
GitHub releases. Bump it by hand, in a PR, before releasing.
