from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from n8n_launcher.git import (
    GitError,
    ensure_dev_branch,
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
    git_list_remote_branches,
    git_pull,
    git_pull_new_repo,
    git_push,
    git_push_ref,
    git_remote_url,
    git_remove_remote,
    git_set_remote_url,
    tokenize_remote_url,
)
from n8n_launcher.git.manager import git_current_branch


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


def test_git_init_initializes_repo_with_dev_branch(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_init(tmp_path)

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "init"],
        ["git", "branch", "-M", "dev"],
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
        ["git", "branch", "-M", "dev"],
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
    run.assert_called_once()


def test_git_commit_commits_staged_changes(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, " M file.json\n"),
            completed(0, ""),
            completed(0, ""),
            completed(),
        ],
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
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            side_effect=[
                completed(0, "origin\n"),
                completed(0, "main\n"),
                completed(1, "", "fatal: network error"),
            ],
        ),
        pytest.raises(GitError, match="network error"),
    ):
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


def test_tokenize_remote_url_embeds_token() -> None:
    assert (
        tokenize_remote_url("https://github.com/octo/flows.git", "ghp_secret")
        == "https://ghp_secret@github.com/octo/flows.git"
    )
    assert (
        tokenize_remote_url("https://a.test/repo.git", "t0k!n") == "https://t0k%21n@a.test/repo.git"
    )


def test_tokenize_remote_url_rejects_non_https() -> None:
    with pytest.raises(GitError):
        tokenize_remote_url("git@github.com:octo/flows.git", "ghp_secret")
    with pytest.raises(GitError):
        tokenize_remote_url("ssh://git@example.test/flows.git", "ghp_secret")


def test_git_error_raised_on_failure(tmp_path: Path) -> None:
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            return_value=completed(128, "", "fatal: repository not found"),
        ),
        pytest.raises(GitError) as excinfo,
    ):
        git_clone("https://bad.test/repo.git", tmp_path / "dest")

    assert "fatal: repository not found" in str(excinfo.value)


def test_git_error_raised_when_git_missing(tmp_path: Path) -> None:
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            side_effect=FileNotFoundError(),
        ),
        pytest.raises(GitError),
    ):
        git_init(tmp_path)


def test_git_error_raised_on_timeout(tmp_path: Path) -> None:
    from subprocess import TimeoutExpired

    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            side_effect=TimeoutExpired("git", 30),
        ),
        pytest.raises(GitError) as excinfo,
    ):
        git_init(tmp_path)

    assert "timed out" in str(excinfo.value)


def test_git_error_raised_on_os_error(tmp_path: Path) -> None:
    """OSError from subprocess.run (e.g. PermissionError on Windows) is converted to GitError."""
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            side_effect=PermissionError(13, "Permission denied"),
        ),
        pytest.raises(GitError, match="could not be executed"),
    ):
        git_init(tmp_path)


def test_git_is_repo_false_on_os_error(tmp_path: Path) -> None:
    """git_is_repo returns False instead of leaking an OSError."""
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=PermissionError(13, "Permission denied"),
    ):
        assert git_is_repo(tmp_path) is False


def test_git_list_remote_branches_parses_refs(tmp_path: Path) -> None:
    payload = (
        "1111111111111111111111111111111111111111\trefs/heads/main\n"
        "2222222222222222222222222222222222222222\trefs/heads/feature/x\n"
        "3333333333333333333333333333333333333333\trefs/tags/v1\n"
        "4444444444444444444444444444444444444444\trefs/heads/feature/x\n"
    )
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, payload),
    ) as run:
        branches = git_list_remote_branches("https://example.test/repo.git", cwd=tmp_path)

    assert branches == ["feature/x", "main"]
    assert run.call_args.args[0] == [
        "git",
        "ls-remote",
        "--heads",
        "https://example.test/repo.git",
    ]
    assert run.call_args.kwargs["cwd"] == tmp_path


def test_git_list_remote_branches_returns_empty_when_no_heads(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, ""),
    ):
        assert git_list_remote_branches("https://example.test/repo.git", cwd=tmp_path) == []


def test_git_list_remote_branches_raises_on_failure(tmp_path: Path) -> None:
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            return_value=completed(128, "", "fatal: repository not found"),
        ),
        pytest.raises(GitError, match="repository not found"),
    ):
        git_list_remote_branches("https://bad.test/repo.git", cwd=tmp_path)


def test_git_pull_new_repo_clones_without_branch(tmp_path: Path) -> None:
    dest = tmp_path / "workspace"
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_pull_new_repo("https://example.test/repo.git", dest)

    assert run.call_args.args[0] == [
        "git",
        "clone",
        "https://example.test/repo.git",
        str(dest),
    ]
    assert run.call_args.kwargs["cwd"] == dest.parent


def test_git_pull_new_repo_clones_selected_branch(tmp_path: Path) -> None:
    dest = tmp_path / "workspace"
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_pull_new_repo("https://example.test/repo.git", dest, branch="feature/x")

    assert run.call_args.args[0] == [
        "git",
        "clone",
        "--branch",
        "feature/x",
        "https://example.test/repo.git",
        str(dest),
    ]


def test_git_pull_new_repo_propagates_git_error(tmp_path: Path) -> None:
    dest = tmp_path / "workspace"
    with (
        patch(
            "n8n_launcher.git.manager.subprocess.run",
            return_value=completed(128, "", "fatal: destination path already exists"),
        ),
        pytest.raises(GitError, match="destination path already exists"),
    ):
        git_pull_new_repo("https://example.test/repo.git", dest)


def test_git_pull_new_repo_then_list_round_trip(tmp_path: Path) -> None:
    """A caller can list branches, pick one, and pull it in one flow."""
    dest = tmp_path / "workspace"
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "aaaa\trefs/heads/main\nbbbb\trefs/heads/staging\n"),
            completed(),
        ],
    ) as run:
        branches = git_list_remote_branches("https://example.test/repo.git", cwd=tmp_path)
        git_pull_new_repo("https://example.test/repo.git", dest, branch=branches[0])

    calls = [call.args[0] for call in run.call_args_list]
    assert calls == [
        ["git", "ls-remote", "--heads", "https://example.test/repo.git"],
        ["git", "clone", "--branch", "main", "https://example.test/repo.git", str(dest)],
    ]


def test_git_current_branch_returns_active_branch(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "release\n"),
    ):
        assert git_current_branch(tmp_path) == "release"


def test_git_current_branch_empty_when_detached(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(1, "", "fatal: ref HEAD is not a symbolic ref"),
    ):
        assert git_current_branch(tmp_path) == ""


def test_git_push_ref_pushes_src_to_dst(tmp_path: Path) -> None:
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_push_ref(tmp_path, "server", "HEAD", "main")

    assert run.call_args.args[0] == ["git", "push", "server", "HEAD:main"]


def test_git_push_ref_forwards_env(tmp_path: Path) -> None:
    env = {"GIT_SSH_COMMAND": "ssh -i /tmp/key"}
    with patch("n8n_launcher.git.manager.subprocess.run", return_value=completed()) as run:
        git_push_ref(tmp_path, "server", "dev", "main", env=env)

    assert run.call_args.args[0] == ["git", "push", "server", "dev:main"]
    forwarded = run.call_args.kwargs["env"]
    assert forwarded["GIT_SSH_COMMAND"] == "ssh -i /tmp/key"
    assert "PATH" in forwarded


def test_git_ssh_env_embeds_key_and_batch_mode(tmp_path: Path) -> None:
    from n8n_launcher.git.manager import git_ssh_env

    env = git_ssh_env("/home/me/.ssh/id_ed25519")
    command = env["GIT_SSH_COMMAND"]
    assert "/home/me/.ssh/id_ed25519" in command
    assert "BatchMode=yes" in command
    assert "StrictHostKeyChecking=accept-new" in command


def test_ensure_dev_branch_noop_when_already_dev(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(0, "dev\n"),
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"
    assert run.call_count == 1
    assert run.call_args.args[0] == ["git", "symbolic-ref", "--short", "HEAD"]


def test_ensure_dev_branch_returns_none_when_not_a_repo(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        return_value=completed(128, "", "fatal: not a git repository"),
    ):
        assert ensure_dev_branch(tmp_path) is None


def test_ensure_dev_branch_switches_to_existing_local_dev(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "main\n"),
            completed(0, ""),  # HEAD exists
            completed(0, "  dev\n"),  # local dev branch present
            completed(),  # checkout dev
        ],
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"

    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1] == ["git", "checkout", "dev"]


def test_ensure_dev_branch_switches_to_remote_dev_without_push(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "main\n"),
            completed(0, ""),  # HEAD exists
            completed(0, ""),  # no local dev
            completed(0, "refs/remotes/origin/dev\n"),  # origin/dev known
            completed(),  # checkout -b dev origin/dev
        ],
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"

    calls = [call.args[0] for call in run.call_args_list]
    assert calls[-1] == ["git", "checkout", "-b", "dev", "origin/dev"]


def test_ensure_dev_branch_creates_local_dev_and_pushes_when_origin_lacks_dev(
    tmp_path: Path,
) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "main\n"),  # symbolic-ref
            completed(0, ""),  # HEAD exists
            completed(0, ""),  # no local dev
            completed(0, ""),  # no origin/dev tracking
            completed(),  # fetch origin
            completed(0, ""),  # still no origin/dev
            completed(),  # checkout -b dev
            completed(0, "origin\n"),  # ensure_dev_branch git_has_remote
            completed(0, "origin\n"),  # git_push git_has_remote
            completed(0, "dev\n"),  # git_push _current_branch
            completed(),  # push -u origin dev
        ],
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"

    calls = [call.args[0] for call in run.call_args_list]
    assert ["git", "checkout", "-b", "dev"] in calls
    assert ["git", "push", "-u", "origin", "dev"] in calls


def test_ensure_dev_branch_fetches_then_uses_remote_dev(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "main\n"),
            completed(0, ""),  # HEAD exists
            completed(0, ""),  # no local dev
            completed(0, ""),  # no origin/dev tracking yet
            completed(),  # fetch origin
            completed(0, "refs/remotes/origin/dev\n"),  # now present
            completed(),  # checkout -b dev origin/dev
        ],
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"

    calls = [call.args[0] for call in run.call_args_list]
    assert ["git", "fetch", "origin"] in calls
    assert calls[-1] == ["git", "checkout", "-b", "dev", "origin/dev"]


def test_ensure_dev_branch_renames_unborn_main_to_dev(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[
            completed(0, "main\n"),
            completed(128, "", "fatal: bad revision HEAD"),  # unborn
            completed(),  # symbolic-ref HEAD refs/heads/dev
        ],
    ) as run:
        assert ensure_dev_branch(tmp_path) == "dev"

    assert run.call_args.args[0] == ["git", "symbolic-ref", "HEAD", "refs/heads/dev"]


def test_git_remote_url_named(tmp_path: Path) -> None:
    with patch(
        "n8n_launcher.git.manager.subprocess.run",
        side_effect=[completed(0, "deploy@host:repo.git\n"), completed(1, "")],
    ) as run:
        url = git_remote_url(tmp_path, "server")
        missing = git_remote_url(tmp_path, "server")

    assert url == "deploy@host:repo.git"
    assert missing is None
    assert [c.args[0] for c in run.call_args_list] == [
        ["git", "remote", "get-url", "server"],
        ["git", "remote", "get-url", "server"],
    ]
