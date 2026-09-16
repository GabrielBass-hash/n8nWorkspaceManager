from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from n8n_launcher.git import (
    GitError,
    ensure_gitignore,
    git_add,
    git_add_remote,
    git_clone,
    git_commit,
    git_has_remote,
    git_has_uncommitted,
    git_has_unpushed_commits,
    git_init,
    git_is_repo,
    git_pull,
    git_push,
    git_remote_url,
    git_remove_remote,
    git_set_remote_url,
)


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return CompletedProcess(["git"], returncode, stdout, stderr)


def test_git_is_repo_true_when_rev_parse_succeeds(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "/tmp/.git\n"),
    ) as run:
        assert git_is_repo(tmp_path) is True
    assert run.call_args.args[0] == ["git", "rev-parse", "--git-dir"]
    assert run.call_args.kwargs["cwd"] == tmp_path


def test_git_is_repo_false_when_rev_parse_fails(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: not a git repository"),
    ):
        assert git_is_repo(tmp_path) is False


def test_git_init_initializes_repo_with_main_branch(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_init(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "init"],
        ["git", "branch", "-M", "main"],
        ["git", "config", "--get", "user.name"],
        ["git", "config", "--get", "user.email"],
        ["git", "config", "user.name", "n8n-launcher"],
        ["git", "config", "user.email", "n8n-launcher@local"],
    ]
    assert (tmp_path / ".git" / "info" / "exclude").is_file()


def test_git_init_honors_existing_global_identity(tmp_path: Path) -> None:
    # A global identity must not be clobbered by launcher defaults.
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "Jane Doe\n"),
    ) as run:
        git_init(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert ["git", "config", "user.name", "n8n-launcher"] not in calls
    assert ["git", "config", "user.email", "n8n-launcher@local"] not in calls


def test_git_init_adds_remote_when_provided(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_init(tmp_path, remote_url="https://example.test/repo.git")

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "init"],
        ["git", "branch", "-M", "main"],
        ["git", "config", "--get", "user.name"],
        ["git", "config", "--get", "user.email"],
        ["git", "config", "user.name", "n8n-launcher"],
        ["git", "config", "user.email", "n8n-launcher@local"],
        ["git", "remote", "add", "origin", "https://example.test/repo.git"],
    ]


def test_ensure_gitignore_writes_default_only_once(tmp_path: Path) -> None:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    excludes = tmp_path / ".git" / "info" / "exclude"

    ensure_gitignore(tmp_path)
    first = excludes.read_text(encoding="utf-8")
    assert ".env" in first

    ensure_gitignore(tmp_path)
    assert excludes.read_text(encoding="utf-8") == first


def test_ensure_gitignore_preserves_user_entries(tmp_path: Path) -> None:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    excludes = tmp_path / ".git" / "info" / "exclude"
    excludes.write_text("# user rules\nsecret.yml\n", encoding="utf-8")

    ensure_gitignore(tmp_path)

    content = excludes.read_text(encoding="utf-8")
    assert "# user rules" in content
    assert "secret.yml" in content
    assert ".env" in content


def test_git_clone_clones_into_destination(tmp_path: Path) -> None:
    dest = tmp_path / "workspace"
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_clone("https://example.test/repo.git", dest)

    assert run.call_args.args[0] == ["git", "clone", "https://example.test/repo.git", str(dest)]
    assert run.call_args.kwargs["cwd"] == dest.parent


def test_git_add_defaults_to_all_changes(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_add(tmp_path)

    assert run.call_args.args[0] == ["git", "add", "-A"]


def test_git_add_stages_explicit_patterns(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_add(tmp_path, "*.json")

    assert run.call_args.args[0] == ["git", "add", "*.json"]


def test_git_commit_returns_false_when_nothing_to_commit(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ) as run:
        committed = git_commit(tmp_path, "message")

    assert committed is False
    run.assert_called_once


def test_git_commit_commits_staged_changes(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[completed(0, " M file.json\n"), completed(0, ""), completed(0, ""), completed()],
    ) as run:
        committed = git_commit(tmp_path, "sync workflows")

    assert committed is True
    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "status", "--porcelain"],
        ["git", "config", "--get", "user.name"],
        ["git", "config", "--get", "user.email"],
        [
            "git",
            "-c",
            "user.name=n8n-launcher",
            "-c",
            "user.email=n8n-launcher@local",
            "commit",
            "-m",
            "sync workflows",
        ],
    ]


def test_git_commit_uses_existing_identity_without_override(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, " M file.json\n"),
            completed(0, "Jane Doe\n"),
            completed(0, "jane@example.test\n"),
            completed(),
        ],
    ) as run:
        committed = git_commit(tmp_path, "sync workflows")

    assert committed is True
    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1] == ["git", "commit", "-m", "sync workflows"]


def test_git_push_sets_upstream_on_first_push(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "origin\n"),
            completed(0, "main\n"),
            completed(),
        ],
    ) as run:
        git_push(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "remote"],
        ["git", "symbolic-ref", "--short", "HEAD"],
        ["git", "push", "-u", "origin", "main"],
    ]


def test_git_push_skips_when_no_remote(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ) as run:
        git_push(tmp_path)

    run.assert_called_once()


def test_git_pull_rebases_from_upstream(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "origin\n"),
            completed(0, "main\n"),
            completed(),
        ],
    ) as run:
        git_pull(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "remote"],
        ["git", "symbolic-ref", "--short", "HEAD"],
        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
    ]


def test_git_pull_falls_back_on_unrelated_histories(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "origin\n"),
            completed(0, "main\n"),
            completed(1, "", "fatal: refusing to merge unrelated histories"),
            completed(),
        ],
    ) as run:
        git_pull(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1] == [
        "git",
        "pull",
        "--rebase",
        "--autostash",
        "--allow-unrelated-histories",
        "origin",
        "main",
    ]


def test_git_pull_re_raises_other_errors(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "origin\n"),
            completed(0, "main\n"),
            completed(1, "", "fatal: network error"),
        ],
    ):
        with pytest.raises(GitError, match="network error"):
            git_pull(tmp_path)


def test_git_pull_skips_when_no_remote(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ) as run:
        git_pull(tmp_path)

    run.assert_called_once()


def test_git_has_remote_true(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "origin\n"),
    ):
        assert git_has_remote(tmp_path) is True


def test_git_has_remote_false(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ):
        assert git_has_remote(tmp_path) is False


def test_git_remote_url_returns_origin(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "https://example.test/repo.git\n"),
    ):
        assert git_remote_url(tmp_path) == "https://example.test/repo.git"


def test_git_remote_url_none_when_missing(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: no such remote"),
    ):
        assert git_remote_url(tmp_path) is None


def test_git_has_uncommitted_true(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, " M flow.json\n"),
    ):
        assert git_has_uncommitted(tmp_path) is True


def test_git_has_uncommitted_false(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ):
        assert git_has_uncommitted(tmp_path) is False


def test_git_has_unpushed_commits_true(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "abc123 commit message\n"),
    ):
        assert git_has_unpushed_commits(tmp_path) is True


def test_git_has_unpushed_commits_false(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ):
        assert git_has_unpushed_commits(tmp_path) is False


def test_git_add_remote_and_set_url(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_add_remote(tmp_path, "origin", "https://a.test/one.git")
        git_set_remote_url(tmp_path, "origin", "https://a.test/two.git")

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "remote", "add", "origin", "https://a.test/one.git"],
        ["git", "remote", "set-url", "origin", "https://a.test/two.git"],
    ]


def test_git_remove_remote_removes_origin(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_remove_remote(tmp_path, "origin")

    assert run.call_args.args[0] == ["git", "remote", "remove", "origin"]


def test_git_remove_remote_is_noop_when_missing(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(2, "", "fatal: No such remote: 'origin'"),
    ) as run:
        git_remove_remote(tmp_path, "origin")

    assert run.call_args.args[0] == ["git", "remote", "remove", "origin"]


def test_git_error_raised_on_failure(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: repository not found"),
    ):
        with pytest.raises(GitError) as excinfo:
            git_clone("https://bad.test/repo.git", tmp_path / "dest")

    assert "fatal: repository not found" in str(excinfo.value)


def test_git_error_raised_when_git_missing(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=FileNotFoundError(),
    ):
        with pytest.raises(GitError):
            git_init(tmp_path)


def test_git_error_raised_on_timeout(tmp_path: Path) -> None:
    from subprocess import TimeoutExpired

    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=TimeoutExpired("git", 30),
    ):
        with pytest.raises(GitError) as excinfo:
            git_init(tmp_path)

    assert "timed out" in str(excinfo.value)


def test_git_error_raised_on_os_error(tmp_path: Path) -> None:
    """OSError from subprocess.run (e.g. PermissionError on Windows) is converted to GitError."""
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=PermissionError(13, "Permission denied"),
    ):
        with pytest.raises(GitError, match="could not be executed"):
            git_init(tmp_path)


def test_git_is_repo_false_on_os_error(tmp_path: Path) -> None:
    """git_is_repo returns False instead of leaking an OSError."""
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=PermissionError(13, "Permission denied"),
    ):
        assert git_is_repo(tmp_path) is False
