//! Git module: workflow synchronization.
//! Port de `src/n8n_launcher/git/`.

pub mod manager;

pub use manager::{
    workspace_branch, workspace_git_lock, GitProbeStatus,
    LAUNCHER_GIT_EMAIL, LAUNCHER_GIT_NAME, WORKSPACE_BRANCH, GIT_LOCK_FILENAME, EXCLUDE_BODY,
};

/// Re-export commonly used git functions at module level.
pub use manager::{
    git_add, git_add_remote, git_clone, git_commit, git_current_branch, git_head,
    git_has_remote, git_has_uncommitted, git_has_unpushed_commits, git_init, git_is_repo,
    git_remote_url, git_remove_remote, git_set_remote_url, git_push, git_push_ref, git_seed_remote,
    git_ssh_env, tokenize_remote_url, git_probe_status, ensure_local_excludes,
    ensure_workspace_branch, git_list_remote_branches, git_pull_new_repo, git_rename_current_branch,
};
pub use manager::GitError;
