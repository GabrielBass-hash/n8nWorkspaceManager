# n8n-launcher

Cross-platform launcher for isolated n8n workspaces, rewritten in Rust.
Windows / macOS / Linux. Version **6.0.0**.

The whole application lives in `rust/n8n-launcher-workspace/`: a Cargo workspace
(resolver 2, edition 2024, `rust-version = 1.97`) with a single member crate,
`n8n-launcher`, and a single binary of the same name. The crate declares
`default = ["gui"]`; the optional `gui` feature gates the iced shell. The former
Python package has been removed from this branch.

Each workspace is a user-defined n8n instance bound to a folder of workflow
exports, with its own Compose project, data volume and optional Postgres.

> **The rewrite is still partial.** The backend behaviour below is implemented
> and tested, but the iced view is minimal and a few ports are stubs. See
> [État de la réécriture / Écarts connus](#état-de-la-réécriture--écarts-connus)
> before assuming a feature is reachable from the window.

## Current behaviour

- A run takes the single-instance lock (`launcher.lock`), resolves the Docker
  CLI, opens the event journal, loads `launcher.db`, and then opens the iced
  window. A second instance prints a message and exits cleanly.
- A config that exists but cannot be read is renamed to
  `launcher.db.corrupt-<timestamp>` and the run stops — it is never treated as a
  first launch. A missing config goes through `prompt_first_launch()`, which
  currently writes nothing (see the gaps section).
- Closing — window close, Ctrl-C, or the ordered `close_session()` path — stops
  every workspace via `stop_with_sync()` (export and git push **before**
  teardown) and logs the closing event before the store is closed. Every run
  traces `Surveillance active` and ends with
  `Surveillance terminée — session de h:mm:ss, N workspace(s) en cours à la
  fermeture`; persistence of that bracket into `EventStore` is not wired yet
  (see the gaps section).
- `n8n-launcher --self-test` checks for a display, builds a throwaway config in
  a temp dir and proves the iced application can open; CI runs it on the
  finished binary on all three OS.

The iced view is deliberately small for now: a header with a *Nouveau* button
and a list of workspace buttons. Lifecycle messages only change a status string.
The rules a fuller view will need already exist as toolkit-free functions in
`workspaces/status.rs` (French labels, status tones, `can_start` / `can_stop` /
`can_open`, `primary_action`, `card_actions`), but they are **not wired into the
view** yet.

## Key concepts

### Workspace isolation and database modes

A workspace gets an isolated Compose project `n8n-ws-<id>`. The YAML is written
to `<config_dir>/workspaces/<id>/compose.yml` and declares the named volume
`n8ndata-<id>`; in managed mode it also declares `pgdata-<id>`. Lifecycle
`docker compose` calls (`up` / `down`) pin the project with `-p n8n-ws-<id>`.

- **`MANAGED`** adds a Postgres service with a healthcheck and a `depends_on`
  condition. Missing data-DB parameters are filled in with database `data`,
  user `n8ndata` and a random password. The n8n image defaults to `2.40.0`;
  `postgres_image` and `postgres_preload_timescaledb` are per-workspace.
- **`NONE`** omits the database service entirely.

SQL files in `db/migrations/` (or `db/schema.sql`) are detected by
`database/layout.rs` and can be applied by the migration runner through
`compose exec`. Creating a workspace with no explicit DB config selects `MANAGED`
when such a layout exists, `NONE` otherwise. `platform/ports.rs` suggests a free
port and `create()` re-checks it under the config transaction.

### Git synchronization

Git is optional per workspace (`GitConfig { enabled, remote_url, branch, … }`).
Launcher-created repositories use the branch **`dev`**
(`WORKSPACE_BRANCH = "dev"`); legacy `main` / `n8n/*` branches are renamed once
on the first `ensure_running()`. Lifecycle git calls run under the reentrant
per-workspace lock `.n8n-launcher.git.lock`, which is listed in `EXCLUDE_BODY`
so it is never staged. The lock is best effort and non-blocking — it is not a
guarantee of mutual exclusion.

- **Auto-pull on start** — `sync_git(workspace, false)` runs after Docker is up
  and the workflows are ready; it is best effort and never blocks the start.
- **Auto-push on close** — `stop_with_sync()` exports the workflows and calls
  `sync_git(workspace, true)` before `stop()`. `stop()` itself only writes
  `STOPPING`, runs `docker compose down`, then writes `STOPPED`.
- **`dev` is the work and CI branch; `main` exists only on the server's bare
  repository**, where `publish()` pushes `dev → main` with a 300 s timeout.

### Workflow import and export

`n8n/workflows.rs::import_all()` scans a directory for `*.json` and creates each
workflow through the public API. Files without a `nodes` key, unreadable files,
and workflows already present (by id derived from the export name, or by name)
are skipped. `n8n/exports.rs::create_payload()` is a whitelist of the fields the
create schema accepts; server-only fields are dropped.

`export_all(api, dir, mirror)` refreshes `n8nPipelines/<name>-<id>.json` for each
workflow and, when a mirror is given, writes the same bodies at the workspace
root. Cleanup in both places is restricted to launcher-named files
(`EXPORT_NAME_RE = .+-\d+\.json$`), so hand-written JSON is never touched.
`start()` imports from `n8nPipelines/` and the root; `stop_with_sync()` and
`publish()` export to `n8nPipelines/` with a root mirror. Consumers that read
both locations dedup by basename in favour of `n8nPipelines/`.

The n8n integration points are baked in: owner bootstrap uses the internal
`/rest/owner/setup`, `/rest/login` and `/rest/api-keys` endpoints with the
`n8n-auth` cookie, API keys carry a `scopes` array and a numeric `expiresAt`,
and readiness is polled on `/healthz/readiness` with a UI-root fallback. The
scope lists in `n8n/scopes.rs` and `remote/deploy.rs` currently **differ** (see
the gaps section).

### Remote deployment and read-only supervision

`install_server()` initializes the bare repository before writing the
`post-receive` hook, then ships the hook and the generated `deploy.py` through
`remote/ssh.rs::write_remote_file()`, which writes to `<path>.tmp` and
`mv -f`s it into place. Both templates are embedded in the binary with
`include_str!` and populated by `__TOKEN__` substitution; they are marked as
generated and must not be edited by hand.

The hook begins with `cd "$HOME"`, shells-quotes every generated path, takes an
exclusive `flock`, archives `refs/heads/main` into the checkout, runs
`docker compose -f "$WORKFLOW/compose.yml" -p "$PROJECT" up -d`, then executes
`deploy.py`. Logs (redacted) and `last-deploy.json` are written atomically; the
remote Compose file pins a top-level `name: n8n-ws-<id>` and binds n8n to
loopback only. Activation is best effort through the deploy's owner session.
`publish()` exports credentials into a `secrets.json` uploaded over SSH, pushes
`dev:main`, then polls the marker until its sha matches HEAD.

`server_health()`, `server_logs()` and `server_deploy_status()` are bounded,
read-only SSH reads that degrade to an empty result instead of raising. The
status sets (`TERMINAL_EXECUTION_STATUSES`, `PENDING_EXECUTION_STATUSES`) and
`REMOTE_STATUS_CAPABILITY` are injected into the generated script so n8n's
vocabulary has one definition. See the gaps section for the execution-status
read.

### GitHub Actions CI

`workspaces/ci.rs::render_harness(n8n_version)` emits
`.github/workflows/n8n-ci.yml`, `.n8n-tests/validate.py`, `.n8n-tests/runner.py`
and `.n8n-tests/tests.json`, all marker-commented as generated, with the image
pinned through a version check on the tag. `enable_ci()` requires a Git repo and
a GitHub remote, writes those files (keeping an existing `tests.json`), commits
and pushes, and tries to set the repository default branch to `dev`.
`workflow_eligibility()` decides whether a pipeline is testable — a manual,
schedule or *pinned* webhook/chat trigger plus covered credential types — and
returns a French rationale with the verdict. `save_ci_selection()` writes the
selection without enforcing eligibility, so callers must ask first. Only
`(name, type)` credential metadata is stored.

### Event journal and updates

`monitoring/store.rs` keeps structured events in `logs_dir()/events.db` (SQLite,
WAL-style pragmas, 30-day retention). `EventStore.search_events()` is the single
SQL filter; `latest_events()` and `export_events()` build on it, and
`monitoring/redaction.rs` masks secret-shaped values before they are persisted.
`bootstrap_logging()` opens the journal before the run and degrades to no
journal when the directory cannot be used.

`platform/update_flow.rs::UpdateController` sequences check → offer → download →
install with **every** question injected as a callback (`set_status`, `on_offer`,
`on_error`, `on_status_link`, `schedule`, `is_closed`, `finish_close`); results
return through an event queue. `platform/updater.rs` compares the version with
the latest GitHub release and picks a compatible asset — all testable headless.

## Module map

All paths are relative to `rust/n8n-launcher-workspace/n8n-launcher/src/`.

| Module | Role |
|---|---|
| `main.rs` | Entry point: `--self-test`, lock, journal, config, iced shell, ordered shutdown |
| `app.rs` | Iced shell, shutdown flag, signal handlers, session bracketing (minimal view) |
| `core/{config,models,paths,filelock,templates}.rs` | SQLite config, domain models, platform paths, advisory locks, template loader |
| `core/first_launch.rs` | Toolkit-free wizard contract (`validate_password`, `build_initial_config`, `run_first_launch`) |
| `database/{credentials,layout,migrations}.rs` | DB credentials, migration detection, SQL runner |
| `docker/{compose,manager}.rs` | Compose rendering; `docker compose` subprocess wrapper and CLI resolution |
| `git/manager.rs` | Per-workspace git operations, `dev` branch, reentrant lock, tokenized URLs |
| `github/{api,auth}.rs` | GitHub REST client; token resolution (stub) |
| `monitoring/{events,store,redaction,bootstrap}.rs` | Structured journal, SQL search, redaction, logging bootstrap |
| `n8n/{api,owner,scopes,workflows,exports}.rs` | HTTP client, owner bootstrap, scope list, import/export rules |
| `platform/{ports,update_flow,updater}.rs` | Port selection, injected update flow, GitHub release download |
| `remote/{ssh,deploy}.rs` + `remote/templates/` | SSH plumbing, generated hook/`deploy.py`, read-only server status |
| `workspaces/{manager,status,close,dialogs,ci}.rs` | Lifecycle controller, status decisions, ordered close, collected plans, CI harness |

`n8n-launcher/tests/docker_integration.rs` holds the Docker-dependent lifecycle
tests; they self-skip when no daemon is available.

## Development

Rust 1.97 or newer, with `cargo` from a stable toolchain.

```bash
cd rust/n8n-launcher-workspace
cargo test --workspace --all-features        # 142 inline unit tests
cargo test --test docker_integration         # 3 tests; needs a Docker daemon
cargo build --release
cd ../..
bash scripts/build.sh                        # copies the binary into dist/
dist/n8n-launcher --self-test                # smoke-test that artifact
```

`cargo test --workspace --all-features` currently runs **142 inline unit tests**;
the integration target adds **3 Docker tests** (145 in total). The default build
has the GUI feature enabled, so `--all-features` tests the code a release binary
actually ships.

In VS Code, the **Run** button uses the first profile in `.vscode/launch.json`
(*Run n8n-launcher*) and launches `cargo run` in the integrated terminal — no
debugger extension to install. A second profile runs `--self-test` headless.
The workspace recommends `rust-analyzer`, and `Ctrl+Shift+B` runs `cargo build`.

## CI and releases

`.github/workflows/rust.yml` runs `cargo test --workspace --all-features` on
`ubuntu-latest`, `macos-latest` and `windows-latest` (`fail-fast: false`), then
`cargo test --test docker_integration` on Ubuntu and
`cargo build --workspace --release` plus a `--self-test` smoke test on all three
OS. The binaries are uploaded as artifacts.

`.github/workflows/release.yml` fires only on pushes to `main`. It reads the
version from `[workspace.package]` in `rust/n8n-launcher-workspace/Cargo.toml`,
compares it with the last `v<version>` tag, and builds and publishes three raw
binaries only when the version has changed. The version is single-source SemVer:
the crate inherits it through `version.workspace = true`. Bump it by hand; there
is no automatic bump. Releases ship **raw binaries** — there is no `.dmg` and no
AppImage in this rewrite.

## État de la réécriture / Écarts connus

These are deliberate limits of the current branch, verified from source. They
are not finished features:

- **The iced view is minimal.** `app.rs::view()` renders a header with a
  *Nouveau* button and one button per workspace. `Message::StartWorkspace`,
  `StopWorkspace`, `DeleteWorkspace`, `OpenWorkspace` and `NewWorkspace` only
  set a status string; they do **not** call the manager. There are no card
  gestures, no status pill, no right-click menu and no window reuse. The
  decisions in `workspaces/status.rs` exist but are not bound to the view.
- **No wizard.** `app.rs::prompt_first_launch()` raises `GuiUnavailable` without
  a display and otherwise returns `Ok(false)` without writing a config. The
  toolkit-free rules in `core/first_launch.rs` are real; the graphical dialog is
  not. A launcher with no config simply stops.
- **No browser module.** Nothing opens or reuses an n8n Chromium window.
- **GitHub token resolution is a stub.** `github/auth.rs::resolve_github_token()`
  always returns `None`, so no automatic `gh auth token` / `git credential fill`
  fallback exists yet. `git/manager.rs::tokenize_remote_url()` exists but has no
  caller yet.
- **`remote/ssh.rs::scp()` returns `"Not implemented"`.** SSH command execution
  and atomic remote file writes work; SCP copy does not.
- **`monitoring/bootstrap.rs::capture_exceptions()` is a no-op**, so uncaught
  exceptions are not funnelled into the journal. The session bracket is traced
  but not yet persisted into `EventStore`.
- **`core/templates.rs::read_template()` reads a `templates/<package>/<name>`
  directory that does not exist and is unused.** The live remote templates are
  the `include_str!` files under `remote/templates/`.
- **The two scope lists diverge.** `n8n/scopes.rs` still carries legacy scope
  names while `remote/deploy.rs` injects a different six-scope list into the
  generated script, despite the comment saying they must match.
- **`workspaces/manager.rs::server_execution_status()` returns an empty string**;
  the generated script's execution-status vocabulary exists, but the launcher
  read is not implemented.
- **`disable_ci()` only flips the persisted flag** — unlike the old behaviour it
  does not delete the generated workflow, validator or runner files, and
  `ci_credentials_payload()` currently just serializes the selected values.
- **No application packaging or lint gate.** The PyInstaller, DMG and AppImage
  scripts are gone; `scripts/build.sh` only copies the binary to `dist/`, and
  `rust.yml` configures neither `cargo fmt --check` nor `cargo clippy`.
