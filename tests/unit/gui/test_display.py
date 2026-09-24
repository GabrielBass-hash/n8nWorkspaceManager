import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from n8n_launcher.core.models import (
    DbConfig,
    DbMode,
    GitConfig,
    ServerConfig,
    Workspace,
    WorkspaceState,
)
from n8n_launcher.git import GitProbeStatus
from n8n_launcher.gui.display import (
    GitRowStatus,
    ci_enabled,
    ci_tooltip,
    db_connected,
    format_row,
    git_repo_status,
    git_row_label,
    git_row_status,
    invalidate_git_status,
    pipelines_count,
    server_label,
    server_tooltip,
)


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return CompletedProcess(["git"], returncode, stdout, stderr)


def make_workspace(path: Path, *, mode: DbMode = DbMode.MANAGED) -> Workspace:
    return Workspace(
        id="w1",
        name="Demo",
        workflows_dir=path,
        port=5678,
        db=DbConfig(mode=mode),
    )


def add_migration(path: Path) -> None:
    (path / "db" / "migrations").mkdir(parents=True)
    (path / "db" / "migrations" / "001.sql").write_text("select 1;")


def add_pipelines(path: Path) -> None:
    pipelines = path / "n8nPipelines"
    pipelines.mkdir(parents=True, exist_ok=True)
    (pipelines / "hello-1.json").write_text("{}")


def add_ci_export(path: Path, rel: str) -> None:
    pipeline = path / rel
    pipeline.parent.mkdir(parents=True, exist_ok=True)
    pipeline.write_text(
        json.dumps(
            {
                "name": "CI",
                "nodes": [
                    {"name": "Bouton", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1}
                ],
                "connections": {},
                "settings": {},
            }
        )
    )


def test_git_repo_status_detects_git_directory(tmp_path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "/tmp/.git\n"),
    ):
        assert git_repo_status(tmp_path) is True


def test_git_repo_status_detects_git_worktree_marker(tmp_path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "/tmp/.git\n"),
    ):
        assert git_repo_status(tmp_path) is True


def test_git_repo_status_missing(tmp_path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: not a git repository"),
    ):
        assert git_repo_status(tmp_path) is False


def test_db_connected_managed_requires_layout(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    assert db_connected(workspace) is False

    add_migration(tmp_path)
    assert db_connected(workspace) is True


def test_format_row_shows_running_state_and_pipeline_count(tmp_path) -> None:
    add_migration(tmp_path)
    add_pipelines(tmp_path)
    workspace = make_workspace(tmp_path)
    workspace.state = WorkspaceState.RUNNING
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "/tmp/.git\n"),
    ):
        assert (
            format_row(workspace) == "Demo | running | :5678 | db locale | git oui | n8nPipelines 1"
        )


def test_format_row_shows_none_db_and_no_pipelines(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: not a git repository"),
    ):
        assert (
            format_row(workspace) == "Demo | stopped | :5678 | db aucune | git non | n8nPipelines 0"
        )


def test_db_label_maps_mode(tmp_path) -> None:
    from n8n_launcher.gui.display import db_label as db_lbl

    assert db_lbl(make_workspace(tmp_path, mode=DbMode.MANAGED)) == "locale"
    assert db_lbl(make_workspace(tmp_path, mode=DbMode.NONE)) == "aucune"


def test_pipelines_count_counts_json_files(tmp_path) -> None:
    assert pipelines_count(tmp_path) == 0
    add_pipelines(tmp_path)
    assert pipelines_count(tmp_path) == 1


def test_server_label_off_when_disabled(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    assert server_label(workspace) == "serv"
    assert "désactivé" in server_tooltip(workspace)


def test_server_label_ok_when_deployed(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    workspace.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    assert server_label(workspace) == "serv"
    tooltip = server_tooltip(workspace)
    assert "prod.example.test" in tooltip
    assert "réussi" in tooltip


def test_server_label_ko_on_last_error(tmp_path) -> None:
    workspace = make_workspace(tmp_path)
    workspace.server = ServerConfig(enabled=True, host="prod.example.test", user="deploy")
    workspace.server_last_error = "migration 001: boom"
    assert server_label(workspace) == "serv KO"
    tooltip = server_tooltip(workspace)
    assert "migration 001: boom" in tooltip


def test_git_row_status_flat_when_not_a_repo(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)

    with (
        patch("n8n_launcher.gui.display.git_probe_status", return_value=GitProbeStatus()),
        patch("n8n_launcher.gui.display.git_has_unpushed_commits") as unpushed,
    ):
        status = git_row_status(workspace)

    assert status == GitRowStatus()
    unpushed.assert_not_called()


def test_git_row_status_clean_repo(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)

    with patch(
        "n8n_launcher.gui.display.git_probe_status",
        return_value=GitProbeStatus(is_repo=True),
    ):
        status = git_row_status(workspace)

    assert status == GitRowStatus(is_repo=True)


def test_git_row_status_reports_dirty_diverged_and_remote(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    workspace.git_push_failed = True
    probe = GitProbeStatus(
        is_repo=True,
        dirty=True,
        upstream="origin/n8n/w1",
        ahead=1,
        remote_url="https://example.test/repo.git",
    )

    with patch("n8n_launcher.gui.display.git_probe_status", return_value=probe):
        status = git_row_status(workspace)

    assert status.is_repo is True
    assert status.dirty is True
    assert status.diverged is True
    assert status.push_failed is True
    assert status.remote_url == "https://example.test/repo.git"


def test_git_row_status_falls_back_to_unpushed_probe_without_upstream(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    probe = GitProbeStatus(is_repo=True, remote_url="https://example.test/repo.git")

    with (
        patch("n8n_launcher.gui.display.git_probe_status", return_value=probe),
        patch("n8n_launcher.gui.display.git_has_unpushed_commits", return_value=True) as unpushed,
    ):
        status = git_row_status(workspace)

    assert status.diverged is True
    unpushed.assert_called_once_with(tmp_path)


def test_git_row_status_reads_workspace_fields_live_through_cache(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    workspace.git_push_failed = True
    probe = GitProbeStatus(is_repo=True, dirty=True)

    with patch("n8n_launcher.gui.display.git_probe_status", return_value=probe) as probed:
        first = git_row_status(workspace)
        workspace.git_push_failed = False
        second = git_row_status(workspace)

    assert probed.call_count == 1  # probe cached; workspace fields read live
    assert first.push_failed is True
    assert second.push_failed is False
    assert second.dirty is True


def test_git_row_status_cache_expires_after_ttl(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    invalidate_git_status(workspace)
    probe = GitProbeStatus(is_repo=True, dirty=True)
    clock = [0.0, 0.1, 4.0]

    with (
        patch("n8n_launcher.gui.display.git_probe_status", return_value=probe) as probed,
        patch("n8n_launcher.gui.display.time.monotonic", side_effect=lambda: clock.pop(0)),
    ):
        git_row_status(workspace)  # miss, caches at t=0.0
        git_row_status(workspace)  # hit at t=0.1
        git_row_status(workspace)  # expired at t=4.0

    assert probed.call_count == 2


def test_invalidate_git_status_forces_reprobe(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    invalidate_git_status(workspace)
    probes = iter(
        [
            GitProbeStatus(is_repo=True, dirty=True),
            GitProbeStatus(is_repo=True, dirty=False),
        ]
    )

    with patch(
        "n8n_launcher.gui.display.git_probe_status", side_effect=lambda _f: next(probes)
    ) as probed:
        first = git_row_status(workspace)
        assert first.dirty is True
        second = git_row_status(workspace)
        assert second.dirty is True  # cached
        invalidate_git_status(workspace)
        third = git_row_status(workspace)
        assert third.dirty is False  # re-probed after invalidation

    assert probed.call_count == 2


def test_git_row_label_maps_status() -> None:
    assert git_row_label(GitRowStatus()) == "git"
    assert git_row_label(GitRowStatus(is_repo=True)) == "git"
    assert git_row_label(GitRowStatus(is_repo=True, dirty=True)) == "git"
    assert git_row_label(GitRowStatus(is_repo=True, diverged=True)) == "git <>"
    assert git_row_label(GitRowStatus(is_repo=True, push_failed=True)) == "git KO"


def test_git_row_status_tooltip() -> None:
    status = GitRowStatus(
        is_repo=True,
        dirty=True,
        diverged=True,
        push_failed=True,
        remote_url="https://example.test/repo.git",
    )
    assert "https://example.test/repo.git" in status.tooltip
    assert "non commités" in status.tooltip
    assert "pousser" in status.tooltip
    assert "push a échoué" in status.tooltip
    assert "Dépôt Git initialisé" in GitRowStatus(is_repo=True).tooltip


def test_ci_enabled_reflects_gitconfig(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    assert ci_enabled(workspace) is False
    workspace.git = GitConfig(ci_enabled=True)
    assert ci_enabled(workspace) is True


def test_ci_tooltip_disabled_offers_configure(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    assert "désactivés" in ci_tooltip(workspace)


def test_ci_tooltip_reports_selection_counts(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    workspace.git = GitConfig(ci_enabled=True)
    add_ci_export(tmp_path, "n8nPipelines/manual.json")
    selection = tmp_path / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text('{"selected": ["n8nPipelines/manual.json"]}', encoding="utf-8")

    tooltip = ci_tooltip(workspace)

    assert "activés" in tooltip
    assert "1 pipeline(s) sélectionnée(s) sur 1 testable(s)" in tooltip


def test_ci_tooltip_warns_when_selected_no_longer_testable(tmp_path) -> None:
    workspace = make_workspace(tmp_path, mode=DbMode.NONE)
    workspace.git = GitConfig(ci_enabled=True)
    add_ci_export(tmp_path, "n8nPipelines/aa-manual.json")
    # A webhook export whose trigger is not pinned is ineligible.
    (tmp_path / "n8nPipelines" / "zz-hook.json").write_text(
        json.dumps(
            {
                "name": "Hook",
                "nodes": [
                    {"name": "Webhook", "type": "n8n-nodes-base.webhookTrigger", "typeVersion": 1}
                ],
                "connections": {},
                "settings": {},
            }
        )
    )
    selection = tmp_path / ".n8n-tests" / "tests.json"
    selection.parent.mkdir(parents=True)
    selection.write_text(
        '{"selected": ["n8nPipelines/aa-manual.json", "n8nPipelines/zz-hook.json"]}',
        encoding="utf-8",
    )

    tooltip = ci_tooltip(workspace)

    assert "plus testables" in tooltip
