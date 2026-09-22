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

## GitHub Actions CI for pipelines

Each workspace that has Git enabled with a **GitHub** remote can also run its
exported pipelines as GitHub Actions tests, configured entirely from the GUI:

- **Enabling** (`Configurer les tests GitHub Actions…`) generates three
  files in the workspace repo — `.github/workflows/n8n-ci.yml`,
  `.n8n-tests/validate.py` and `.n8n-tests/runner.py` — plus the machine-managed
  selection file `.n8n-tests/tests.json`. Every generated file carries the
  marker *« n8n-launcher : généré — ne pas modifier à la main »*. The changes
  are committed and pushed on enable.
- **Selection**: the pipeline tree dialog only lets you tick *complete*
  pipelines that can be started without external input: a manual trigger, a
  schedule trigger, or a webhook/chat trigger pinned in the editor (pinData).
  Every non-pinned node's credentials must also be covered by the credentials
  you record locally. Ineligible pipelines are greyed out with the reason.
- **Credentials**: values are read once, live from the running workspace's n8n
  instance, and copied to the clipboard as a JSON document to paste into the
  GitHub Actions secret **`N8N_CI_CREDENTIALS`**. The launcher then keeps only
  the name/type metadata, never the values.
- **What the workflow does**: a `validate` job checks the exports statically
  (JSON shape, duplicate names, selection consistency), then a `test` job runs
  the real pipelines in a throwaway `docker` n8n container, imported via the
  public API, using the pinned image `docker.n8n.io/n8nio/n8n:<workspace
  version>` (overridable with the `N8N_IMAGE` repository variable).
- **Disabling** removes only the generated harness files and keeps
  `tests.json`, so the selection survives a disable/enable cycle.

## Development

Requires Python 3.12 or newer. The project uses **uv** as the single package
manager; every command below runs inside the uv-managed virtualenv.

```bash
uv sync
uv run pytest
```

Lint, format, and typecheck (Ruff + basedpyright + repo hooks). The single
pre-commit command runs exactly what CI runs for static checks:

```bash
uv run pre-commit run --all-files
```

The underlying tools are also available directly if you want to run one alone:

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Integration tests are opt-in:

```bash
uv run pytest -m integration
```

Build the desktop distribution locally with:

```bash
uv sync --group packaging
uv run python scripts/build.py
```

On macOS the onedir `.app` bundle embeds the app icon and a full `Info.plist`
(display name, version, retina support), is ad-hoc signed, and is packaged into
a styled drag-and-drop `.dmg` layout rendered by `dmgbuild` (no Finder or GUI
session required, so CI produces the same result). Because the app may be
launched from the Dock/Finder where the `PATH` is minimal, the launcher
resolves the `docker` CLI from the standard macOS install locations instead of
relying on the environment `PATH`.

On Linux, additionally build the AppImage with:

```bash
bash scripts/build_appimage.sh
```

The output depends on the platform:

| Platform | Output |
|---|---|
| macOS | `dist/n8n-launcher-macos.dmg` (drag-and-drop installer) |
| Linux | one-file `dist/n8n-launcher` or `dist/n8n-launcher-linux-x86_64.AppImage` |
| Windows | `dist/n8n-launcher.exe` |

## Installation

### macOS — no paid Apple Developer account needed

The macOS build is ad-hoc signed but **not notarized**. macOS Gatekeeper blocks
apps "not confirmed by Apple" (translation of *"Apple could not confirm that
'n8n-launcher' did not contain malicious software"*). The block only triggers
on files tagged with the `com.apple.quarantine` attribute, which Safari/Finder
add when you download the `.dmg`. Three free routes avoid it:

**1. Terminal installer (recommended, reliable on macOS 15+).** Downloading
with `curl` never adds the quarantine attribute, so the app installs and runs
with no Apple Developer Program and no Gatekeeper dialog:

```bash
curl -fsSL https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest/download/install_macos.sh | bash
```

Or install a local build (useful to test a freshly built `.dmg`):

```bash
bash scripts/install_macos.sh -f dist/n8n-launcher-macos.dmg
```

**2. De-quarantine an app you already downloaded.** If the app is already in
`/Applications` and Gatekeeper refuses it, strip the flag (see the "Apple could
not confirm" error before/after this step):

```bash
bash scripts/dequarantine.sh                         # defaults to /Applications/n8n-launcher.app
bash scripts/dequarantine.sh /path/to/n8n-launcher.app
```

**3. Manual bypass (no terminal).** Right-click `n8n-launcher.app` → *Open* →
*Open*. Works, but macOS 15+ re-applies the quarantine after a relaunch, so
prefer option 1.

- **Windows**: run or pin `n8n-launcher.exe` from the taskbar / Start menu.
- **Linux**: make the AppImage executable (`chmod +x n8n-launcher-linux-x86_64.AppImage`) and launch it — it integrates with your desktop environment's app menu.

## Releases

The launcher version is a strict SemVer (`MAJOR.MINOR.PATCH`), defined in a
single place — `__version__` in `src/n8n_launcher/__init__.py`. `pyproject.toml`
inherits it (`dynamic = ["version"]`), `scripts/build.py` embeds it into the
bundle and `platform/updater.py` compares it against GitHub releases, so a bump
never goes out of sync.

Bump the version explicitly (never by editing the file by hand):

```bash
python scripts/bump_version.py patch   # 4.0.2 -> 4.0.3 (bugfix)
python scripts/bump_version.py minor   # 4.0.3 -> 4.1.0 (feature)
python scripts/bump_version.py major   # 4.1.0 -> 5.0.0 (breaking)
python scripts/bump_version.py --to 4.2.0     # pin an exact version
python scripts/bump_version.py --dry-run patch  # preview only
```

The script refuses to run on a dirty working tree and refuses to produce a
version whose `v<version>` tag already exists (`--force` bypasses both).

Releases are published **only from `main`**. When a push to `main` carries a
new source version, the release workflow tags it (`v<version>`), runs the tests,
builds the per-OS distribution above and attaches all three artifacts to a
GitHub Release. Pushes that do not change the version are skipped, so there is
no automatic bumping and no release spam from `dev` or feature branches.

The launcher is independent of the source repository that inspired some of its API and workflow-sync boundaries. It does not reuse that repository's weather database schema, runtime state, or Docker sync service.
