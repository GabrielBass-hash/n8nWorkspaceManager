"""Git operations for workspace workflow synchronization.

Entry: ``git_push``, ``git_seed_remote``, ``workspace_git_lock``
(``manager.py``); ``WORKSPACE_BRANCH`` is ``"dev"`` — the work branch and the
CI branch of every workspace.
Gotcha: every git lifecycle call runs inside ``workspace_git_lock`` — auto-pull
on start and auto-push on close/publish/CI must never interleave.
The public surface is re-exported here so consumers can import from
``n8n_launcher.git`` without knowing the internal file layout.
Map: ``docs/architecture.md``.
"""

from .manager import (
    GitError,
    GitProbeStatus,
    ensure_local_excludes,
    ensure_workspace_branch,
    git_add,
    git_add_remote,
    git_clone,
    git_commit,
    git_current_branch,
    git_has_remote,
    git_has_uncommitted,
    git_has_unpushed_commits,
    git_head,
    git_init,
    git_is_repo,
    git_list_remote_branches,
    git_probe_status,
    git_pull,
    git_pull_new_repo,
    git_push,
    git_push_ref,
    git_remote_url,
    git_remove_remote,
    git_rename_current_branch,
    git_seed_remote,
    git_set_remote_url,
    git_ssh_env,
    tokenize_remote_url,
    workspace_branch,
    workspace_git_lock,
)

__all__ = [
    "GitError",
    "GitProbeStatus",
    "ensure_local_excludes",
    "ensure_workspace_branch",
    "git_add",
    "git_add_remote",
    "git_clone",
    "git_commit",
    "git_current_branch",
    "git_has_remote",
    "git_has_uncommitted",
    "git_has_unpushed_commits",
    "git_head",
    "git_init",
    "git_is_repo",
    "git_list_remote_branches",
    "git_probe_status",
    "git_pull",
    "git_pull_new_repo",
    "git_push",
    "git_push_ref",
    "git_remote_url",
    "git_remove_remote",
    "git_rename_current_branch",
    "git_seed_remote",
    "git_set_remote_url",
    "git_ssh_env",
    "tokenize_remote_url",
    "workspace_branch",
    "workspace_git_lock",
]
