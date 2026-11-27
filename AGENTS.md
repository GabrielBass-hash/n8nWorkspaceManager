# AGENTS.md — n8n-launcher

Cross-platform PySide6 launcher for isolated n8n workspaces (Python 3.12+). **uv is the only package manager**; run every command inside the uv-managed virtualenv. Entry point: `run.py` → `n8n_launcher.__main__:main`.

## Developer commands

- `uv sync` — install runtime + dev tooling (default group)
- `uv sync --group packaging` — PyInstaller / dmgbuild for builds
- `uv run pytest` — unit suite; integration excluded by addopts (`-m 'not integration'`), coverage `--cov=src/n8n_launcher --cov-report=term-missing`
- `uv run pytest <path>::test_name` — run one test
- `uv run pytest -m integration` — integration tests; **require a Docker daemon** (`tests/integration`, 60 min in CI)
- `uv run pre-commit run --all-files` — ruff check + ruff format + basedpyright (the pre-commit hook runs `uv run basedpyright` in the project venv)
- `uv run ruff check .` / `uv run ruff format --check .` / `uv run basedpyright` — individually
- `n8n-launcher --self-test` / `uv run python run.py --self-test` — builds the window once over a throwaway config, exits 0; CI runs it on every built artifact

Command order CI enforces: **lint → typecheck → test → build**; `lint` blocks `test` and `integration`.

## Typechecking

`basedpyright` in `typeCheckingMode = "standard"`; **zero errors**. Only `tests/**` and `scripts/**` are excluded — every module under `src/n8n_launcher/` must typecheck. Coverage floor is `--cov-fail-under=80` (enforced locally and in CI).

## GUI toolkit confinement

PySide6 imports are confined to `src/n8n_launcher/gui/`; a unit test enforces it. Everything else (docker, git, github, remote, database managers) is headless-importable without Qt. GUI tests run headless via `QT_QPA_PLATFORM=offscreen` (set in `tests/conftest.py`; CI sets it explicitly). The integration job additionally uses `-p no:pytest-qt` because the plugin imports QtGui at configure time and fails with `ImportError: libEGL.so.1` on runners without Qt system libs.

## Packaging (dist/)

`scripts/build.py` builds PyInstaller **onedir** directories (never one-file — Qt needs its platform plugin at runtime). `scripts/build.py --verify <dist-root>` asserts the Qt platform plugin is present (a bundle without it starts, prints nothing, dies with no window). Smoke test artifacts with `--self-test`. Linux also builds `dist/n8n-launcher-linux-x86_64.AppImage` via `scripts/build_appimage.sh`. macOS `.dmg` is ad-hoc signed and **not notarized** — Gatekeeper blocks quarantined downloads; install via `curl` + `scripts/install_macos.sh -f <dmg>` or `bash scripts/dequarantine.sh`.

## Version

Single source of truth: `__version__` in `src/n8n_launcher/__init__.py` (currently 6.0.0). `pyproject.toml` reads it dynamically; bump by hand in a PR before releasing. `release.yml` only triggers on pushes to `main` and only tags/builds/publishes when the version changed.

## Navigation

- `docs/architecture.md` — module map (where each behaviour lives) and measured-gotchas index. Read before touching `n8n/`, `remote/`, `docker/`, `git/`, `github/`, `database/`, `monitoring/`, `platform/`, or the GUI card modules.
- `docs/tasks.md` — change → read / edit / test file mapping for every area.
- `.pre-commit-config.yaml` — ruff + basedpyright hooks; ruff line-length 100, py312 target.
- `tests/integration` needs Docker; `tests/unit` does not.
- `gitignore`: `dist/`, `build/`, `.venv/`, `workspaces/`, `state/`, `logs/`, `artifacts/`, `*.env*` are local state, never committed.
