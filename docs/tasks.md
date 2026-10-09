# docs/tasks.md — read, edit, test

Start here when the task belongs to a known category: one row replaces a dozen
exploratory reads. **Read** before editing, **Edit** is where a change usually
lands, **Test** is the unit file to run (and to extend — every public function
ships with its test). Integration files need a Docker daemon
(`uv run pytest -m integration`).

| Change… | Read | Edit | Test |
|---|---|---|---|
| Owner bootstrap, API key scopes, password rules | `n8n/owner.py`, `n8n/scopes.py` | `n8n/owner.py`, `core/first_launch.py` | `n8n/test_owner.py`, `core/test_first_launch.py` |
| Workflow import/export payloads, name rules | `n8n/exports.py`, `n8n/workflows.py` | `n8n/exports.py`, `n8n/workflows.py` | `n8n/test_exports.py`, `n8n/test_workflows.py` |
| n8n HTTP client (public API) | `n8n/api.py` | `n8n/api.py` | `n8n/test_api.py` |
| Compose rendering (services, env, ports, volumes) | `docker/compose.py` | `docker/compose.py` | `docker/test_compose.py` |
| Docker state, container labels, CLI discovery | `docker/manager.py` | `docker/manager.py` | `docker/test_docker_manager.py` |
| Start/stop lifecycle, observers, `ensure_serving` | `workspaces/manager.py` | `workspaces/manager.py` | `workspaces/test_workspace_manager.py` |
| Close-all ordering (reconcile → export → sync → stop) | `workspaces/close.py` | `workspaces/close.py` | `workspaces/test_close_sequence.py` |
| Card status, labels, which actions are offered | `workspaces/status.py` | `workspaces/status.py` | `workspaces/test_status.py` |
| Card look: pill, hover ring, port/folder, sizing | `gui/card_delegate.py`, `gui/board.py` | `gui/card_delegate.py` | `gui/test_card_delegate.py`, `gui/test_board.py` |
| Window gestures, header, selection restore | `gui/window.py`, `gui/board.py` | `gui/window.py` | `gui/test_window.py` |
| Chromium app window, raising an existing one | `gui/browser.py` | `gui/browser.py` | `gui/test_browser.py` |
| Window sizes, responsive contract | `gui_utils/responsive.py` (AGENTS §Responsive) | the view + `gui_utils/responsive.py` | `gui_utils/test_responsive_contract.py`, `gui/test_responsive.py -m responsive` |
| Table/column sizing, ellipsis | `gui_utils/text.py` (AGENTS §The sizing rule) | `gui_utils/text.py` | `gui_utils/test_text.py` |
| Git sync, branch model, per-workspace lock, publish | `git/manager.py` | `git/manager.py` | `git/test_git_manager.py` |
| GitHub repo, Actions runs, job logs, token lookup | `github/api.py`, `github/auth.py` | `github/api.py`, `github/auth.py` | `github/test_github_api.py`, `github/test_auth.py` |
| CI eligibility and the saved selection | `workspaces/ci.py` | `workspaces/ci.py` | `workspaces/test_ci.py` |
| CI harness files (workflow, validate, runner) | `workspaces/ci.py`, `workspaces/templates/` | `workspaces/templates/*.tmpl`, `ci.py` | `workspaces/test_ci_runner.py` (+ `test_ci_runner_smoke.py`, integration) |
| Remote publish: hook, server deploy script, statuses | `remote/deploy.py`, `remote/templates/` | `remote/templates/*.tmpl`, `remote/deploy.py` | `remote/test_deploy.py` (+ `test_remote_deploy.py`, integration) |
| SSH plumbing and the read-only remote queries | `remote/ssh.py` | `remote/ssh.py` | `remote/test_ssh.py`, `remote/test_ssh_observability.py` |
| DB layout detection, migrations, n8n credentials | `database/` | `database/` | `database/test_database.py` |
| Config store, paths, locks, generated-artefact templates | `core/config.py`, `core/paths.py`, `core/templates.py` | those modules | `core/test_config.py`, `core/test_paths.py`, `core/test_templates.py` |
| Event journal, redaction, logging bootstrap | `monitoring/store.py`, `monitoring/redaction.py` | those modules | `monitoring/test_store.py`, `monitoring/test_redaction.py` |
| Self-update flow and per-platform assets | `platform/update_flow.py`, `platform/updater.py` | those modules | `platform/test_update_controller.py`, `platform/test_updater.py` |
| First-launch wizard rules | `core/first_launch.py`, `gui/first_launch.py` | `core/first_launch.py` | `core/test_first_launch.py`, `gui/test_first_launch_wizard.py` |
| Packaging, bundle completeness, hidden imports | AGENTS §Commands, `scripts/build.py` | `scripts/build.py` | `scripts/build.py --verify <root>`, `--self-test` (CI build job) |

Paths are relative to `src/n8n_launcher/` and `tests/unit/` respectively.
