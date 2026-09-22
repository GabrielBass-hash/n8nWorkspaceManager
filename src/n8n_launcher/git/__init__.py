"""Git operations for workspace workflow synchronization.

The public surface is re-exported here so consumers can import from
``n8n_launcher.git`` without knowing the internal file layout.
"""

from .manager import (
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

__all__ = [
    "GitError",
    "ensure_gitignore",
    "git_add",
    "git_add_remote",
    "git_clone",
    "git_commit",
    "git_has_remote",
    "git_has_uncommitted",
    "git_has_unpushed_commits",
    "git_init",
    "git_is_repo",
    "git_pull",
    "git_push",
    "git_remote_url",
    "git_remove_remote",
    "git_set_remote_url",
]
