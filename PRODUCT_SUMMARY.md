# n8n-launcher — Product Summary

**n8n-launcher** is a cross-platform application (Rust, Windows / macOS /
Linux) for managing isolated Docker-based n8n workspaces. Each workspace is its
own Docker Compose project with its own data volume and optional PostgreSQL
database. This branch contains **only the Rust rewrite** under
`rust/n8n-launcher-workspace/`; the former Python implementation has been
removed.

- **Version**: 6.0.0 — single-source SemVer in the workspace `Cargo.toml`
  (`[workspace.package] version`), inherited via `version.workspace = true`.
- **Entry point**: binary `n8n-launcher`, with `--self-test` short-circuiting
  before the config, the lock or Docker are touched.
- **Interface**: iced 0.13 shell (`default = ["gui"]`). The current view is a
  header with a *Nouveau* button and a list of workspace buttons; lifecycle
  messages only change a status string.
- **Dependencies**: serde/serde_json, clap 4, iced 0.13 (tokio), reqwest,
  rusqlite (bundled), bcrypt, tracing, regex, tempfile, ctrlc, plus small
  helpers (`uuid`, `chrono`, `directories`, `walkdir`, `which`, `libc`).
- **Configuration**: `platformdirs`-equivalent path from `directories` →
  `<config_dir>/launcher.db` (SQLite, WAL, `0o600` on non-Windows). The journal
  lives in `logs_dir()/events.db` with 30-day retention.

## Architecture

```
rust/n8n-launcher-workspace/
├── Cargo.toml                 # resolver 2, edition 2024, rust-version 1.97
└── n8n-launcher/
    ├── src/
    │   ├── main.rs            # self-test → lock → journal → config → iced → ordered shutdown
    │   ├── lib.rs             # public module surface
    │   ├── app.rs             # iced shell, shutdown flag, signal handlers, session bracketing
    │   ├── core/
    │   │   ├── models.rs      # DbMode, WorkspaceState, Workspace, AppConfig, GitConfig, ServerConfig
    │   │   ├── config.rs      # ConfigStore: SQLite load/save/mutate, file permissions, config backup
    │   │   ├── filelock.rs    # FileLock (reentrant), single-instance launcher.lock
    │   │   ├── first_launch.rs# password/config rules, Docker check, initial save
    │   │   ├── paths.rs       # config/log/runtime directories, compose_file(id)
    │   │   └── templates.rs   # unused read_template() over a missing directory
    │   ├── database/
    │   │   ├── credentials.rs # DB target resolution, n8n credential creation
    │   │   ├── layout.rs      # db/migrations + db/schema.sql detection
    │   │   └── migrations.rs  # SQL runner via `compose exec`
    │   ├── docker/
    │   │   ├── compose.rs     # render_compose(), render_remote_compose(), write_compose()
    │   │   └── manager.rs     # `docker compose` wrapper, label parsing, CLI resolution
    │   ├── git/manager.rs     # init/pull/add/commit/push, workspace_branch(), reentrant lock
    │   ├── github/
    │   │   ├── api.rs         # REST client: owner, repos, runs, jobs, logs, dispatch, default branch
    │   │   └── auth.rs        # resolve_github_token() — stub returning None
    │   ├── monitoring/
    │   │   ├── events.rs      # Event record
    │   │   ├── store.rs       # EventStore: 30-day retention, SQL search/export, close
    │   │   ├── redaction.rs   # mask secret-shaped values before persistence
    │   │   └── bootstrap.rs   # journal bootstrap; capture_exceptions() is a no-op
    │   ├── n8n/
    │   │   ├── api.rs         # public REST client (workflows, credentials)
    │   │   ├── owner.rs       # /rest/* owner bootstrap, bcrypt hash, readiness wait
    │   │   ├── scopes.rs      # local scope list (differs from the remote one — see gaps)
    │   │   ├── workflows.rs   # import_all(), export_all(mirror)
    │   │   └── exports.rs     # whitelist payload, export filename/id rules
    │   ├── platform/
    │   │   ├── ports.rs       # is_port_available(), suggest_port()
    │   │   ├── updater.rs     # GitHub release compare, asset selection, download, installer
    │   │   └── update_flow.rs # UpdateController: check → offer → download → install
    │   ├── remote/
    │   │   ├── ssh.rs         # ssh_run, atomic write_remote_file, chmod, probes (scp is a stub)
    │   │   ├── deploy.rs      # path helpers, rendered hook/deploy script, secrets document
    │   │   └── templates/     # embedded hook.txt and deploy.txt
    │   └── workspaces/
    │       ├── manager.rs     # CRUD, lifecycle, git sync, CI, install_server/publish, status reads
    │       ├── status.rs      # labels, tones, can_start/can_stop/can_open, card actions
    │       ├── close.rs       # CloseSequence with injected CloseHooks
    │       ├── dialogs.rs     # plans a form collects
    │       └── ci.rs          # harness generation, eligibility, selection
    └── tests/docker_integration.rs  # 3 Docker lifecycle tests
```

## Behaviour

### Entry point and shutdown

- `main()` handles `--self-test` first, then resolves paths, takes the
  single-instance lock, bootstraps the journal and logs `Surveillance active`.
- A loaded config opens the iced shell. A config that exists but is unreadable
  is renamed to `launcher.db.corrupt-<timestamp>` and stops the run. A missing
  config calls `prompt_first_launch()` (currently a stub).
- `close_session()` counts workspaces that were not `STOPPED`, stops each one
  through `stop_with_sync()`, and logs `Surveillance terminée` before the store
  is closed. The Ctrl-C handler calls `stop_all()` and requests shutdown; the
  200 ms tick keeps that flag observable while iced waits in its event loop.

### Workspace lifecycle

- States: `STOPPED`, `STARTING`, `RUNNING`, `STOPPING`, `ERROR`. `stop()` writes
  `STOPPING`, runs `docker compose down`, then writes `STOPPED`.
- `start()` writes the Compose file, brings the stack up, waits on
  `/healthz/readiness`, bootstraps the owner, saves the API key, ensures DB
  credentials, pulls git and imports workflows. Those last steps are best
  effort: they do not fail the start.
- `ensure_running()` asks Docker (`live_state()`) rather than the persisted
  state, so a stack started outside the launcher is opened, not started twice.
  `reconcile_all()` batch-reads `docker ps` and writes state changes in one
  config transaction; a workspace whose Compose file is gone keeps its state.

### Docker Compose

- Project `n8n-ws-<id>`; named volumes `n8ndata-<id>` and, in managed mode,
  `pgdata-<id>`. `DbMode::NONE` omits the database service.
- Managed defaults: database `data`, user `n8ndata`, random password;
  `postgres_image` defaults to `postgres:16` and n8n to `2.40.0`.
- `render_remote_compose()` pins a top-level `name: n8n-ws-<id>`, mounts
  `./:/workflows` and binds the n8n port to loopback only.

### Git and workflows

- Every launcher repository works on `dev`; a legacy branch is renamed once.
  Git calls take the reentrant, best-effort and non-blocking
  `.n8n-launcher.git.lock`, which is gitignored via `EXCLUDE_BODY`. Auto-pull
  runs on start; `stop_with_sync()` exports and pushes
  before stopping. `dev` is the CI branch and `main` exists only on the server's
  bare repository, where `publish()` pushes `dev:main` with a 300 s timeout.
- Import scans `*.json` in both `n8nPipelines/` and the workspace root, skips
  unreadable or non-workflow files and duplicates by id or name, and uses a
  field whitelist for the create payload. Export refreshes
  `n8nPipelines/<name>-<id>.json` and optionally mirrors identical copies at the
  root; cleanup only touches launcher-named files.

### GitHub Actions CI

- `render_harness()` emits `.github/workflows/n8n-ci.yml`,
  `.n8n-tests/validate.py`, `.n8n-tests/runner.py` and `.n8n-tests/tests.json`,
  all marker-commented as generated. `enable_ci()` requires a Git repo with a
  GitHub remote, preserves an existing selection file, commits, pushes and
  attempts to set the default branch to `dev`.
- `workflow_eligibility()` returns a verdict plus a French rationale: a manual,
  schedule or pinned webhook/chat trigger, with every non-pinned node's
  credential types covered. `save_ci_selection()` does not enforce it.

### Remote deployment and monitoring

- `install_server()` initializes the bare repo before writing the hook, and
  ships the generated hook and `deploy.py` atomically. The hook `flock`s,
  archives `main`, runs Compose up, then runs `deploy.py`; logs and
  `last-deploy.json` are written atomically and redacted. Credentials travel as
  a `secrets.json` uploaded over SSH.
- `server_health()`, `server_logs()` and `server_deploy_status()` are bounded,
  read-only SSH reads that degrade to empty output on failure. Terminal and
  pending execution-status sets and `REMOTE_STATUS_CAPABILITY` live in
  `remote/deploy.rs` and are injected into the generated script.
- The session bracket (`Surveillance active` / `Surveillance terminée`) is
  traced but **not persisted** into `EventStore`. Stored events are redacted
  before persistence, pruned after 30 days, and filtered only in SQL by
  `search_events()`. The update flow (`UpdateController`) asks every question
  through injected callbacks and never blocks startup.

### First launch

There is no graphical wizard: `prompt_first_launch()` returns `Ok(false)` without
writing a config. The rules exist in `core/first_launch.rs`, but a launcher with
no config simply stops.

## Quality gates

- `cargo test --workspace --all-features`: **142 inline unit tests**;
  `cargo test --test docker_integration`: **3 tests**, self-skipping without Docker.
- `cargo build --workspace --release` and `--self-test` on the produced binary
  on all three operating systems in CI.
- No Python lint/format/type-check gates remain; the Rust pipeline is
  `.github/workflows/rust.yml`. Neither `cargo fmt --check` nor `cargo clippy`
  is configured in CI — a known gap, not an omission in this document.

## Distribution

Releases publish raw binaries (`n8n-launcher` on Linux/macOS,
`n8n-launcher.exe` on Windows). `scripts/build.sh` copies the release binary to
`dist/`; there is no PyInstaller bundle, `.dmg` or AppImage in this branch.

## État de la réécriture / Écarts connus

Verified limits, not finished features:

- The iced view has only a *Nouveau* header button and workspace buttons; all
  lifecycle messages merely set a status string and do not call the manager.
  No card gestures, status pill, context menu or browser reuse exist; the
  decisions in `workspaces/status.rs` are not wired to the view.
- `prompt_first_launch()` is a no-op (`Ok(false)`); no graphical wizard.
- `github/auth.rs::resolve_github_token()` always returns `None`;
  `remote/ssh.rs::scp()` returns `"Not implemented"`.
- `monitoring/bootstrap.rs::capture_exceptions()` is a no-op and
  `server_execution_status()` returns an empty string.
- `core/templates.rs::read_template()` targets a nonexistent directory and is
  unused; the live templates are the embedded `remote/templates/` files.
- The local `n8n/scopes.rs` list diverges from the six scopes injected into the
  generated deploy script by `remote/deploy.rs`, despite a comment saying they
  must match. `git_seed_remote()` / `tokenize_remote_url()` have no caller and
  `CloseSequence` is defined but not used by `app.rs`.
- `disable_ci()` only flips the persisted flag (no generated files deleted);
  `ci_credentials_payload()` merely serializes its input.
