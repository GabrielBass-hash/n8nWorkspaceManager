"""Git operations for workspace workflow synchronization."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    """Raised when a git operation fails."""


def _run_git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not found in PATH")
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out")
    # Windows can surface other OS-level failures (PermissionError, broken pipe,
    # transient locks) when spawning git — report them all as a GitError so
    # diagnostic probes such as git_is_repo() degrade to "not a repo" instead
    # of leaking a raw exception into the caller.
    except OSError as exc:
        raise GitError(f"git {' '.join(args)} could not be executed: {exc}")
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
    if remote_url:
        _run_git(["remote", "add", "origin", remote_url], cwd=path)
    logger.info("Initialized git repo at %s", path)


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
    _run_git(["commit", "-m", message], cwd=path)
    logger.info("Committed in %s: %s", path, message)
    return True


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
    result = _run_git(["log", "--branches", "--not", "--remotes", "--oneline"], cwd=path, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def git_pull(path: Path) -> None:
    """Pull changes from the upstream remote."""
    if not git_has_remote(path):
        logger.debug("No remote configured, skipping pull for %s", path)
        return
    _run_git(["pull", "--rebase"], cwd=path)
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


def git_set_remote_url(path: Path, name: str, url: str) -> None:
    """Set the URL of an existing remote."""
    _run_git(["remote", "set-url", name, url], cwd=path)


def _current_branch(path: Path) -> str:
    """Return the active branch name, or empty for a detached HEAD."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, check=False)
    branch = result.stdout.strip()
    return "" if branch == "HEAD" else branch
