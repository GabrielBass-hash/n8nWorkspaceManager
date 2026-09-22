"""Git operations for workspace workflow synchronization."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Identity used for launcher-created commits. The launcher owns the git layer:
# a workspace close must never fail to commit just because the machine has no
# global git identity configured.
LAUNCHER_GIT_NAME = "n8n-launcher"
LAUNCHER_GIT_EMAIL = "n8n-launcher@local"

# Default ignore rules written into freshly initialized repos: keeps the
# launcher's ``git add -A`` from sweeping volatile or environment files into
# workflow history.
GITIGNORE_BODY = """# n8n-Launcher : fichiers locaux ou volatiles exclus du versionnement.
.env
.env.*
*.log
.DS_Store
Thumbs.db
.idea/
.vscode/
__pycache__/
*.py[cod]
"""


class GitError(RuntimeError):
    """Raised when a git operation fails."""


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not found in PATH") from None
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out") from exc
    # Windows can surface other OS-level failures (PermissionError, broken pipe,
    # transient locks) when spawning git — report them all as a GitError so
    # diagnostic probes such as git_is_repo() degrade to "not a repo" instead
    # of leaking a raw exception into the caller.
    except OSError as exc:
        raise GitError(f"git {' '.join(args)} could not be executed: {exc}") from exc
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitError(f"git {' '.join(args)} failed: {stderr}")
    return result


def git_is_repo(path: Path) -> bool:
    """Return True if *path* is inside a git repository."""
    try:
        result = _run_git(["rev-parse", "--git-dir"], cwd=path, check=False)
        return result.returncode == 0
    except GitError:
        return False


def git_init(path: Path, *, remote_url: str | None = None) -> None:
    """Initialize a new git repo at *path* and optionally add a remote."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["branch", "-M", "main"], cwd=path)
    _configure_repo_identity(path)
    ensure_gitignore(path)
    if remote_url:
        _run_git(["remote", "add", "origin", remote_url], cwd=path)
    logger.info("Initialized git repo at %s", path)


def _configure_repo_identity(path: Path) -> None:
    """Set a repo-local commit identity, honoring an existing global one."""
    name = _run_git(["config", "--get", "user.name"], cwd=path, check=False)
    email = _run_git(["config", "--get", "user.email"], cwd=path, check=False)
    if not name.stdout.strip():
        _run_git(["config", "user.name", LAUNCHER_GIT_NAME], cwd=path)
    if not email.stdout.strip():
        _run_git(["config", "user.email", LAUNCHER_GIT_EMAIL], cwd=path)


def ensure_gitignore(path: Path) -> None:
    """Apply the launcher's default ignore rules to *path*'s repository.

    Rules are written into the repository-local ``.git/info/exclude`` file
    rather than a worktree ``.gitignore``: that file is never tracked, so it
    can neither be swept into a commit by ``git add -A`` nor collide with an
    upstream-tracked ``.gitignore`` during pulls or rebases. Entries that are
    already present (e.g. user customizations) are preserved.
    """
    excludes = path / ".git" / "info" / "exclude"
    excludes.parent.mkdir(parents=True, exist_ok=True)
    existing = excludes.read_text(encoding="utf-8") if excludes.exists() else ""
    marker = GITIGNORE_BODY.splitlines()[0]
    if marker in existing:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    excludes.write_text(existing + separator + GITIGNORE_BODY, encoding="utf-8")
    logger.info("Ensured launcher ignore rules at %s", excludes)


def git_clone(url: str, dest: Path) -> None:
    """Clone a remote repository into *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", url, str(dest)], cwd=dest.parent)
    logger.info("Cloned %s into %s", url, dest)


def git_add(path: Path, *patterns: str) -> None:
    """Stage files matching *patterns* (default: all changes)."""
    if not patterns:
        patterns = ("-A",)
    _run_git(["add", *patterns], cwd=path)


def git_commit(path: Path, message: str) -> bool:
    """Commit staged changes. Returns True if a commit was created."""
    # Check if there is anything to commit
    status = _run_git(["status", "--porcelain"], cwd=path)
    if not status.stdout.strip():
        return False
    _run_git([*_commit_identity_args(path), "commit", "-m", message], cwd=path)
    logger.info("Committed in %s: %s", path, message)
    return True


def _commit_identity_args(path: Path) -> list[str]:
    """Return ``-c`` args guaranteeing the commit identity, unless one exists."""
    name = _run_git(["config", "--get", "user.name"], cwd=path, check=False)
    email = _run_git(["config", "--get", "user.email"], cwd=path, check=False)
    if name.stdout.strip() and email.stdout.strip():
        return []
    return [
        "-c",
        f"user.name={LAUNCHER_GIT_NAME}",
        "-c",
        f"user.email={LAUNCHER_GIT_EMAIL}",
    ]


def git_push(path: Path) -> None:
    """Push the current branch to its upstream remote.

    Sets the upstream (``-u``) on the first push so a branch created by the
    launcher can seed a fresh remote repository.
    """
    if not git_has_remote(path):
        logger.debug("No remote configured, skipping push for %s", path)
        return
    branch = _current_branch(path)
    if branch:
        _run_git(["push", "-u", "origin", branch], cwd=path)
    else:
        _run_git(["push"], cwd=path)
    logger.info("Pushed from %s", path)


def git_has_unpushed_commits(path: Path) -> bool:
    """Return True if the repo has commits not yet pushed to any remote."""
    result = _run_git(
        ["log", "--branches", "--not", "--remotes", "--oneline"], cwd=path, check=False
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def git_seed_remote(
    path: Path, remote_url: str, token: str, *, message: str = "n8n-launcher: initial"
) -> None:
    """Seed a freshly created remote with the current branch, authenticating once.

    When the branch is still unborn (no commit yet), an initial commit is
    created first so seeding an empty workspace folder succeeds. The token is
    embedded only in this single ``git push`` invocation's URL (via
    :func:`_tokenized_remote`), never persisted in the repository's git
    config: the caller then stores the clean URL as the remote.
    """
    branch = _current_branch(path)
    if not branch:
        raise GitError("can't seed remote: repository has no active branch")
    head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=path, check=False)
    if head.returncode != 0:
        git_add(path)
        _run_git([*_commit_identity_args(path), "commit", "--allow-empty", "-m", message], cwd=path)
    # Pushing without ``-u`` on purpose: ``-u`` would store the tokenized URL as
    # the branch's upstream in .git/config, persisting the credential. The
    # launcher always spells origin (or an explicit URL) for pull/push, so no
    # tracking ref is required here.
    _run_git(["push", _tokenized_remote(remote_url, token), branch], cwd=path)
    logger.info("Seeded remote %s from %s", remote_url, path)


def _tokenized_remote(remote_url: str, token: str) -> str:
    """Embed *token* into a GitHub HTTPS URL for a single git invocation."""
    if not remote_url.startswith("https://"):
        raise GitError("remote URL must be https to be seeded with a token")
    return remote_url.replace("https://", f"https://{quote(token, safe='')}@", 1)


def git_pull(path: Path) -> None:
    """Pull changes from the upstream remote, autostashing local edits.

    The remote/branch pair is spelled out explicitly so the first pull works
    even though ``git init`` never sets upstream tracking information, and
    ``--rebase`` (which merges cleanly even across unrelated histories in
    modern git) keeps the history linear.
    """
    if not git_has_remote(path):
        logger.debug("No remote configured, skipping pull for %s", path)
        return
    branch = _current_branch(path)
    try:
        if branch:
            _run_git(["pull", "--rebase", "--autostash", "origin", branch], cwd=path)
        else:
            _run_git(["pull", "--rebase", "--autostash"], cwd=path)
    except GitError as exc:
        detail = str(exc).lower()
        # Older git refuses to rebase histories with no common ancestor; a
        # fresh remote seeded by a hosting provider (README commit) is exactly
        # that case, so force the merge only when git asks for it.
        if "unrelated histories" not in detail and "no common ancestor" not in detail:
            raise
        logger.warning(
            "Unrelated histories detected for %s; pulling with --allow-unrelated-histories", path
        )
        if branch:
            _run_git(
                [
                    "pull",
                    "--rebase",
                    "--autostash",
                    "--allow-unrelated-histories",
                    "origin",
                    branch,
                ],
                cwd=path,
            )
        else:
            _run_git(["pull", "--rebase", "--autostash", "--allow-unrelated-histories"], cwd=path)
    logger.info("Pulled into %s", path)


def git_has_remote(path: Path) -> bool:
    """Return True if the repo has at least one remote configured."""
    result = _run_git(["remote"], cwd=path)
    return bool(result.stdout.strip())


def git_remote_url(path: Path) -> str | None:
    """Return the URL of the 'origin' remote, or None."""
    result = _run_git(["remote", "get-url", "origin"], cwd=path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def git_has_uncommitted(path: Path) -> bool:
    """Return True if the working tree has uncommitted changes."""
    result = _run_git(["status", "--porcelain"], cwd=path)
    return bool(result.stdout.strip())


def git_add_remote(path: Path, name: str, url: str) -> None:
    """Add a named remote. Raises if it already exists."""
    _run_git(["remote", "add", name, url], cwd=path)


def git_remove_remote(path: Path, name: str) -> None:
    """Remove a named remote. No-op when the remote does not exist."""
    result = _run_git(["remote", "remove", name], cwd=path, check=False)
    if result.returncode != 0:
        # Removing a missing remote is already the desired end-state (git exits
        # with a non-zero code and no effect in that case).
        logger.debug("Remote %r not present at %s; nothing to remove", name, path)
        return
    logger.info("Removed git remote %r at %s", name, path)


def git_set_remote_url(path: Path, name: str, url: str) -> None:
    """Set the URL of an existing remote."""
    _run_git(["remote", "set-url", name, url], cwd=path)


def _current_branch(path: Path) -> str:
    """Return the active branch name, or empty for a detached HEAD.

    ``symbolic-ref --short`` keeps working while the branch is still unborn
    (no commits yet), which is exactly the state during the launcher's very
    first pull or push on a freshly initialized repository.
    """
    result = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=path, check=False)
    return result.stdout.strip()
