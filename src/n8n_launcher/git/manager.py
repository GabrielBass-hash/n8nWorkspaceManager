"""Git operations for workspace workflow synchronization."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from ..core.filelock import FileLock

logger = logging.getLogger(__name__)

# Identity used for launcher-created commits. The launcher owns the git layer:
# a workspace close must never fail to commit just because the machine has no
# global git identity configured.
LAUNCHER_GIT_NAME = "n8n-launcher"
LAUNCHER_GIT_EMAIL = "n8n-launcher@local"

# Lock file whose exclusive hold serializes git operations on one workspace
# (state-changing reads + commit + push must never interleave, whether from a
# background thread or from a second launcher process racing the first).
GIT_LOCK_FILENAME = ".n8n-launcher.git.lock"

# Default ignore rules written into freshly initialized repos: keeps the
# launcher's ``git add -A`` from sweeping volatile or environment files into
# workflow history. The git lock file itself is ignored so a ``git add -A``
# never stages a lock that exist only while the launcher runs.
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
.n8n-launcher.git.lock
"""


def workspace_branch(workspace_id: str) -> str:
    """Return the per-workspace git branch the launcher coordinates on.

    Every workspace syncs on its own ``n8n/<id>`` branch, so two workspaces
    pushing from different machines (or two instances of the same workspace)
    never fight over ``dev``/``main``: each branch behaves like an append-only
    per-workspace queue that a remote server can draft spontaneously.
    """
    return f"n8n/{workspace_id}"


class _GitLockOwner:
    """Per-workflow-dir holder combining a thread RLock and an OS file lock."""

    def __init__(self, lock_file: FileLock) -> None:
        self.lock_file = lock_file
        self.rlock = threading.RLock()
        self.depth = 0


_lock_owners: dict[Path, _GitLockOwner] = {}
_lock_owners_guard = threading.Lock()


@contextmanager
def workspace_git_lock(path: Path) -> Iterator[None]:
    """Serialize every git operation on *path*'s working tree.

    Re-entrant across the call stack of the current thread (nested layers such
    as ``sync_git`` → ``_commit_and_push`` share one OS lock) but exclusive
    between threads and between separate processes. The OS lock lives in a
    dedicated ``.n8n-launcher.git.lock`` file inside the working tree, ignored
    by ``git add -A`` (see :data:`GITIGNORE_BODY`).
    """
    if not git_is_repo(path):
        yield
        return
    with _lock_owners_guard:
        owner = _lock_owners.get(path)
        if owner is None:
            owner = _lock_owners[path] = _GitLockOwner(FileLock(path / GIT_LOCK_FILENAME))
    with owner.rlock:
        owner.depth += 1
        if owner.depth == 1:
            owner.lock_file.acquire()
        try:
            yield
        finally:
            owner.depth -= 1
            if owner.depth == 0:
                owner.lock_file.release()


class GitError(RuntimeError):
    """Raised when a git operation fails."""


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    cmd = ["git", *args]
    full_env = {**os.environ, **(env or {})}
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            env=full_env,
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


def git_init(path: Path, *, remote_url: str | None = None, branch: str | None = None) -> None:
    """Initialize a new git repo at *path* and optionally add a remote.

    The primary branch is *branch* (the per-workspace ``n8n/<id>`` when called
    by the manager), falling back to ``dev`` for backward compatibility.
    """
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["branch", "-M", branch or "dev"], cwd=path)
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


def git_ssh_env(key_path: str) -> dict[str, str]:
    """Return an env dict making git authenticate with *key_path* over SSH.

    Used for the launcher's own ``server`` remote: git spawns system ``ssh``,
    which needs the workspace's configured key (the machine's default identity
    is usually a different, personal key). Commands run in batch mode so a
    missing key or unknown host fails loudly instead of hanging on a prompt.
    """
    return {
        "GIT_SSH_COMMAND": (
            f"ssh -i {key_path} -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
        )
    }


def git_push_ref(
    path: Path,
    remote: str,
    src: str,
    dst: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Push *src* to *dst* on *remote* with an explicit refspec (no ``-u``).

    Publishing uses ``HEAD:main`` so the production branch is the local current
    state whatever the branch is called — no local ``main`` branch required.
    """
    _run_git(["push", remote, f"{src}:{dst}"], cwd=path, env=env)
    logger.info("Pushed %s:%s to %s in %s", src, dst, remote, path)


def git_seed_remote(
    path: Path, remote_url: str, token: str, *, message: str = "n8n-launcher: initial"
) -> None:
    """Seed a freshly created remote with the current branch, authenticating once.

    When the branch is still unborn (no commit yet), an initial commit is
    created first so seeding an empty workspace folder succeeds. The token is
    embedded only in this single ``git push`` invocation's URL (via
    :func:`tokenize_remote_url`), never persisted in the repository's git
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
    _run_git(["push", tokenize_remote_url(remote_url, token), branch], cwd=path)
    logger.info("Seeded remote %s from %s", remote_url, path)


def tokenize_remote_url(remote_url: str, token: str) -> str:
    """Embed *token* into an HTTPS remote URL for a single git invocation.

    GitHub accepts the token as the basic-auth username; callers must reset
    the clean URL immediately after the command so the token never lands in
    the repository's ``.git/config`` (see :func:`git_seed_remote` and
    :meth:`WorkspaceManager.clone_from_git`).
    """
    if not remote_url.startswith("https://"):
        raise GitError("remote URL must be https to be used with a token")
    return remote_url.replace("https://", f"https://{quote(token, safe='')}@", 1)


# Backwards-compatible alias for the historical private name.
_tokenized_remote = tokenize_remote_url


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


def ensure_workspace_branch(path: Path, branch: str) -> str | None:
    """Make *branch* the active branch, creating it when needed.

    Idempotent and best-effort:
    - already on *branch* → nothing;
    - an existing local *branch* is checked out as-is;
    - a *branch* known on ``origin`` (tracking ref, or discovered by fetching)
      is checked out from it;
    - otherwise *branch* is created from the current HEAD and pushed to
      ``origin``. A repository whose historical ``main``/``dev`` content must
      keep working therefore inherits it into the new branch automatically.

    Returns the active branch name, or ``None`` when *path* is not a repository.
    Never raises: git failures at any step degrade to a warning and a return.
    """
    try:
        current = _current_branch(path)
    except GitError:
        return None
    if not current:
        # Detached HEAD or not a repository at all: nothing to ensure.
        return None
    if current == branch:
        return branch
    has_head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=path, check=False)
    if has_head.returncode != 0:
        # Unborn HEAD (no commits yet): point the active branch ref at *branch*.
        try:
            _run_git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"], cwd=path)
        except GitError as exc:
            logger.warning("Could not rename the unborn branch to %s: %s", branch, exc)
            return current
        logger.info("Renamed the unborn branch to %s in %s", branch, path)
        return branch
    local = _run_git(["branch", "--list", branch], cwd=path)
    if local.stdout.strip():
        _run_git(["checkout", branch], cwd=path)
        return branch
    remote = _run_git(
        ["for-each-ref", "--format=%(refname)", f"refs/remotes/origin/{branch}"],
        cwd=path,
        check=False,
    )
    if not remote.stdout.strip():
        try:
            _run_git(["fetch", "origin"], cwd=path)
        except GitError as exc:
            logger.warning("Could not fetch origin for %s: %s", path, exc)
        remote = _run_git(
            ["for-each-ref", "--format=%(refname)", f"refs/remotes/origin/{branch}"],
            cwd=path,
            check=False,
        )
    if remote.stdout.strip():
        _run_git(["checkout", "-b", branch, f"origin/{branch}"], cwd=path)
        return branch
    _run_git(["checkout", "-b", branch], cwd=path)
    try:
        if git_has_remote(path):
            git_push(path)
    except GitError as exc:
        logger.warning("Could not push the fresh %s branch for %s: %s", branch, path, exc)
    return branch


def git_remote_url(path: Path, name: str = "origin") -> str | None:
    """Return the URL of the named remote (default ``origin``), or None."""
    result = _run_git(["remote", "get-url", name], cwd=path, check=False)
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


def git_list_remote_branches(url: str, *, cwd: Path | None = None) -> list[str]:
    """List the branches available at *url* without cloning it.

    Uses ``git ls-remote --heads`` so callers can present a picker of the
    repositories/branches offered by a remote before pulling a brand-new
    repository into the workspace. The URL is passed verbatim, so it works
    for HTTPS, SSH and local-path remotes, and no credentials are required
    for public remotes. Results are de-duplicated and sorted.
    """
    # ls-remote does not require a repo, but _run_git needs a cwd.
    result = _run_git(["ls-remote", "--heads", url], cwd=cwd or Path.cwd())
    prefix = "refs/heads/"
    branches: set[str] = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("\t")
        ref = ref.strip()
        if ref.startswith(prefix):
            branches.add(ref[len(prefix) :])
    return sorted(branches)


def git_pull_new_repo(url: str, dest: Path, *, branch: str | None = None) -> None:
    """Clone (pull for the first time) a repository into *dest*.

    Optionally selects a specific *branch* — typically one returned by
    :func:`git_list_remote_branches` — so the launcher can let a user pick
    which available repository to bring into the workspace. *dest* must not
    already contain a repository; use :func:`git_pull` for an existing one.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if branch:
        args += ["--branch", branch]
    args += [url, str(dest)]
    _run_git(args, cwd=dest.parent)
    logger.info(
        "Pulled new repo %s%s into %s",
        url,
        f" (branch {branch})" if branch else "",
        dest,
    )


def git_current_branch(path: Path) -> str:
    """Return the active branch name (public alias of :func:`_current_branch`)."""
    return _current_branch(path)
