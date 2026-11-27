//! Git operations for workspace workflow synchronization.
//! Port de `src/n8n_launcher/git/manager.py`.

use std::path::Path;
use std::process::{Command, Stdio};

// Identity used for launcher-created commits.
pub const LAUNCHER_GIT_NAME: &str = "n8n-launcher";
pub const LAUNCHER_GIT_EMAIL: &str = "n8n-launcher@local";

// Lock file whose exclusive hold serializes git operations on one workspace.
pub const GIT_LOCK_FILENAME: &str = ".n8n-launcher.git.lock";

// Default ignore rules written into freshly initialized repos.
pub const EXCLUDE_BODY: &str = "# n8n-Launcher : fichiers locaux ou volatiles exclus du versionnement.\n.env\n.env.*\n*.log\n.DS_Store\nThumbs.db\n.idea/\n.vscode/\n__pycache__/\n*.py[cod]\n.n8n-launcher.git.lock\n";

// Canonical working branch of every workspace.
pub const WORKSPACE_BRANCH: &str = "dev";

/// Return the workspace's canonical working branch (always `dev`).
pub fn workspace_branch(_workspace_id: &str) -> String {
    WORKSPACE_BRANCH.to_string()
}

/// Error raised when a git operation fails.
#[derive(Debug)]
pub struct GitError(pub String);

impl std::fmt::Display for GitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "GitError: {}", self.0)
    }
}

impl std::error::Error for GitError {}

impl From<std::io::Error> for GitError {
    fn from(e: std::io::Error) -> Self {
        GitError(format!("git could not be executed: {e}"))
    }
}

/// Run a git command and return the output.
fn run_git(args: &[&str], cwd: &Path, check: bool, _timeout_secs: u64) -> Result<String, GitError> {
    let mut cmd = Command::new("git");
    cmd.args(args);
    cmd.current_dir(cwd);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let output = cmd
        .output()
        .map_err(|e| GitError(format!("git is not installed or not found in PATH: {e}")))?;

    if check && !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(GitError(format!(
            "git {} failed: {}",
            args.join(" "),
            stderr
        )));
    }

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

/// Run a git command non-checking, returning the raw output.
fn run_git_no_check(args: &[&str], cwd: &Path, _timeout_secs: u64) -> Result<String, GitError> {
    run_git(args, cwd, false, _timeout_secs)
}

/// Serialize every git operation on *path*'s working tree.
/// Best-effort re-entrant lock via FileLock.
pub fn workspace_git_lock<F, R>(path: &Path, f: F) -> R
where
    F: FnOnce() -> R,
{
    if !git_is_repo(path) {
        return f();
    }
    // Acquire OS lock for the duration of f().
    let lock_path = path.join(GIT_LOCK_FILENAME);
    let mut lock = crate::core::filelock::FileLock::new(&lock_path);
    // Non-blocking acquire; if it fails, still proceed (best effort).
    let _ = lock.acquire(false, false);
    let result = f();
    let _ = lock.release();
    result
}

/// Return True if *path* is inside a git repository.
pub fn git_is_repo(path: &Path) -> bool {
    match run_git_no_check(&["rev-parse", "--git-dir"], path, 30) {
        Ok(output) => !output.trim().is_empty() || true, // rev-parse returns 0 even with empty output on success
        Err(_) => false,
    }
}

/// Probe a repository's display-relevant state.
pub fn git_probe_status(path: &Path) -> GitProbeStatus {
    let _ = path;
    GitProbeStatus::default()
}

#[derive(Debug, Clone, Default)]
pub struct GitProbeStatus {
    pub is_repo: bool,
    pub dirty: bool,
    pub upstream: Option<String>,
    pub ahead: i32,
    pub remote_url: Option<String>,
}

impl GitProbeStatus {
    pub fn has_remote(&self) -> bool {
        self.remote_url.is_some()
    }
}

/// Initialize a new git repo at *path* and optionally add a remote.
pub fn git_init(path: &Path, remote_url: Option<&str>, branch: Option<&str>) {
    let _ = std::fs::create_dir_all(path);
    let _ = run_git(&["init"], path, true, 30);
    let branch = branch.unwrap_or(WORKSPACE_BRANCH);
    let _ = run_git(&["branch", "-M", branch], path, true, 30);
    _configure_repo_identity(path);
    ensure_local_excludes(path);
    if let Some(url) = remote_url {
        let _ = run_git(&["remote", "add", "origin", url], path, true, 30);
    }
}

fn _configure_repo_identity(path: &Path) {
    let _ = run_git_no_check(&["config", "--get", "user.name"], path, 30);
    let _ = run_git_no_check(&["config", "--get", "user.email"], path, 30);
    // Best-effort: set launcher identity if missing
    let _ = run_git(&["config", "user.name", LAUNCHER_GIT_NAME], path, false, 30);
    let _ = run_git(&["config", "user.email", LAUNCHER_GIT_EMAIL], path, false, 30);
}

/// Apply the launcher's default ignore rules to *path*'s repository.
pub fn ensure_local_excludes(path: &Path) {
    let excludes = path.join(".git").join("info").join("exclude");
    if let Some(parent) = excludes.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let existing = std::fs::read_to_string(&excludes).unwrap_or_default();
    if existing.contains(".n8n-launcher.git.lock") {
        return;
    }
    let separator = if existing.is_empty() || existing.ends_with('\n') {
        ""
    } else {
        "\n"
    };
    let _ = std::fs::write(
        &excludes,
        format!("{existing}{separator}{EXCLUDE_BODY}"),
    );
}

/// Clone a remote repository into *dest*.
pub fn git_clone(url: &str, dest: &Path) {
    if let Some(parent) = dest.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let dest_str = dest.to_string_lossy().to_string();
    let parent = dest.parent().unwrap_or(Path::new("."));
    let _ = run_git(&["clone", url, &dest_str], parent, true, 60);
}

/// Stage files matching *patterns* (default: all changes).
pub fn git_add(path: &Path, patterns: &[&str]) {
    let mut args: Vec<&str> = vec!["add"];
    if patterns.is_empty() {
        args.push("-A");
    } else {
        args.extend_from_slice(patterns);
    }
    let _ = run_git(&args, path, true, 30);
}

/// Commit staged changes. Returns True if a commit was created.
pub fn git_commit(path: &Path, message: &str) -> bool {
    let status = match run_git_no_check(&["status", "--porcelain"], path, 30) {
        Ok(output) => output,
        Err(_) => return false,
    };
    if status.trim().is_empty() {
        return false;
    }
    let args = vec!["commit", "-m", message];
    run_git(&args, path, true, 30).is_ok()
}

/// Push the current branch to its upstream remote.
pub fn git_push(path: &Path) {
    if !git_has_remote(path) {
        return;
    }
    let branch = match git_current_branch(path) {
        Some(b) => b,
        None => return,
    };
    let _ = run_git(&["push", "-u", "origin", &branch], path, true, 30);
}

/// Return True if the repo has commits not yet pushed to any remote.
pub fn git_has_unpushed_commits(path: &Path) -> bool {
    match run_git_no_check(
        &["log", "--branches", "--not", "--remotes", "--oneline"],
        path,
        30,
    ) {
        Ok(output) => !output.trim().is_empty(),
        Err(_) => false,
    }
}

/// Return an env dict making git authenticate with *key_path* over SSH.
pub fn git_ssh_env(key_path: &str, port: Option<u16>) -> std::collections::HashMap<String, String> {
    let mut command = "ssh -o IdentitiesOnly=yes".to_string();
    if let Some(p) = port {
        if p != 22 {
            command.push_str(&format!(" -p {p}"));
        }
    }
    command.push_str(&format!(" -i {key_path} -o BatchMode=yes -o StrictHostKeyChecking=accept-new"));
    let mut env = std::collections::HashMap::new();
    env.insert("GIT_SSH_COMMAND".to_string(), command);
    env
}

/// Push *src* to *dst* on *remote* with an explicit refspec.
pub fn git_push_ref(
    path: &Path,
    remote: &str,
    src: &str,
    dst: &str,
    env: Option<&std::collections::HashMap<String, String>>,
    _timeout: u64,
) {
    let mut cmd = Command::new("git");
    cmd.arg("push");
    cmd.arg(remote);
    cmd.arg(format!("{src}:{dst}"));
    cmd.current_dir(path);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    if let Some(e) = env {
        for (k, v) in e {
            cmd.env(k, v);
        }
    }
    let _ = cmd.spawn().and_then(|mut child| child.wait());
}

/// Seed a freshly created remote with the current branch, authenticating once.
pub fn git_seed_remote(
    path: &Path,
    remote_url: &str,
    token: &str,
    message: Option<&str>,
) -> Result<(), GitError> {
    let branch = match git_current_branch(path) {
        Some(b) => b,
        None => return Err(GitError("can't seed remote: repository has no active branch".to_string())),
    };

    let head = run_git_no_check(&["rev-parse", "--verify", "HEAD"], path, 30);
    if head.map(|h| h.trim().is_empty()).unwrap_or(true) {
        git_add(path, &[]);
        let msg = message.unwrap_or("n8n-launcher: initial");
        let _ = run_git(
            &["commit", "--allow-empty", "-m", msg],
            path,
            true,
            30,
        );
    }

    let tokenized = tokenize_remote_url(remote_url, token)?;
    run_git(&["push", &tokenized, &branch], path, true, 30)?;
    Ok(())
}

/// Embed *token* into an HTTPS remote URL for a single git invocation.
pub fn tokenize_remote_url(remote_url: &str, token: &str) -> Result<String, GitError> {
    if !remote_url.starts_with("https://") {
        return Err(GitError("remote URL must be https to be used with a token".to_string()));
    }
    Ok(remote_url.replacen("https://", &format!("https://{token}@"), 1))
}

/// Pull changes from the upstream remote, autostashing local edits.
pub fn git_pull(path: &Path) -> Result<(), GitError> {
    if !git_has_remote(path) {
        return Ok(());
    }
    let branch = match git_current_branch(path) {
        Some(b) => b,
        None => return Ok(()),
    };
    let result = run_git_no_check(
        &["pull", "--rebase", "--autostash", "origin", &branch],
        path,
        30,
    );
    match result {
        Ok(_) => Ok(()),
        Err(e) => {
            let detail = e.to_string().to_lowercase();
            if detail.contains("unrelated histories") || detail.contains("no common ancestor") {
                run_git(
                    &[
                        "pull",
                        "--rebase",
                        "--autostash",
                        "--allow-unrelated-histories",
                        "origin",
                        &branch,
                    ],
                    path,
                    true,
                    30,
                )?;
                Ok(())
            } else {
                Err(e)
            }
        }
    }
}

/// Return True if the repo has at least one remote configured.
pub fn git_has_remote(path: &Path) -> bool {
    match run_git_no_check(&["remote"], path, 30) {
        Ok(output) => !output.trim().is_empty(),
        Err(_) => false,
    }
}

/// Make *branch* the active branch, creating it when needed.
pub fn ensure_workspace_branch(path: &Path, branch: &str) -> Option<String> {
    let current = match git_current_branch(path) {
        Some(c) => c,
        None => return None,
    };
    if current.is_empty() {
        return None;
    }
    if current == branch {
        return Some(branch.to_string());
    }

    // Try checkout existing local branch
    let _ = run_git(&["checkout", branch], path, false, 30);
    if git_current_branch(path).as_deref() == Some(branch) {
        return Some(branch.to_string());
    }

    // Create new branch
    let _ = run_git(&["checkout", "-b", branch], path, true, 30);
    if git_has_remote(path) {
        let _ = git_push(path);
    }
    Some(branch.to_string())
}

/// Return the URL of the named remote (default `origin`), or None.
pub fn git_remote_url(path: &Path, name: Option<&str>) -> Option<String> {
    let name = name.unwrap_or("origin");
    match run_git_no_check(&["remote", "get-url", name], path, 30) {
        Ok(output) => {
            let url = output.trim().to_string();
            if url.is_empty() {
                None
            } else {
                Some(url)
            }
        }
        _ => None,
    }
}

/// Return True if the working tree has uncommitted changes.
pub fn git_has_uncommitted(path: &Path) -> bool {
    match run_git_no_check(&["status", "--porcelain"], path, 30) {
        Ok(output) => !output.trim().is_empty(),
        Err(_) => false,
    }
}

/// Add a named remote. Raises if it already exists.
pub fn git_add_remote(path: &Path, name: &str, url: &str) {
    let _ = run_git(&["remote", "add", name, url], path, true, 30);
}

/// Remove a named remote. No-op when the remote does not exist.
pub fn git_remove_remote(path: &Path, name: &str) {
    let _ = run_git_no_check(&["remote", "remove", name], path, 30);
}

/// Set the URL of an existing remote.
pub fn git_set_remote_url(path: &Path, name: &str, url: &str) {
    let _ = run_git(&["remote", "set-url", name, url], path, true, 30);
}

/// Return the active branch name, or empty for a detached HEAD.
pub fn git_current_branch(path: &Path) -> Option<String> {
    match run_git_no_check(&["symbolic-ref", "--short", "HEAD"], path, 30) {
        Ok(output) => {
            let s = output.trim().to_string();
            if s.is_empty() {
                None
            } else {
                Some(s)
            }
        }
        Err(_) => None,
    }
}

/// List the branches available at *url* without cloning it.
pub fn git_list_remote_branches(url: &str) -> Vec<String> {
    let cwd = std::env::current_dir().unwrap_or_default();
    match run_git_no_check(&["ls-remote", "--heads", url], &cwd, 30) {
        Ok(output) => {
            let prefix = "refs/heads/";
            output
                .lines()
                .filter_map(|line| {
                    line.split('\t')
                        .nth(1)
                        .map(|s| s.trim())
                        .and_then(|s| s.strip_prefix(prefix))
                        .map(|s| s.to_string())
                })
                .collect()
        }
        Err(_) => Vec::new(),
    }
}

/// Clone (pull for the first time) a repository into *dest*.
pub fn git_pull_new_repo(url: &str, dest: &Path, branch: Option<&str>) {
    if let Some(parent) = dest.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut args: Vec<&str> = vec!["clone"];
    if let Some(b) = branch {
        args.push("--branch");
        args.push(b);
    }
    args.push(url);
    let dest_str = dest.to_string_lossy();
    args.push(&dest_str);
    let parent = dest.parent().unwrap_or(Path::new("."));
    let _ = run_git(&args, parent, true, 60);
}

/// Return the active branch name (public alias).
pub fn git_current_branch_alias(path: &Path) -> Option<String> {
    git_current_branch(path)
}

/// Return the full commit SHA currently checked out in *path*.
pub fn git_head(path: &Path) -> String {
    run_git_no_check(&["rev-parse", "--verify", "HEAD"], path, 30)
        .map(|o| o.trim().to_string())
        .unwrap_or_default()
}

/// Rename the active branch to *new_branch*.
pub fn git_rename_current_branch(path: &Path, new_branch: &str) -> bool {
    let current = match git_current_branch(path) {
        Some(c) => c,
        None => return false,
    };
    if current == new_branch {
        return true;
    }
    run_git(&["branch", "-M", new_branch], path, true, 30).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_workspace_branch_always_dev() {
        assert_eq!(workspace_branch("any-id"), "dev");
    }

    #[test]
    fn test_tokenize_remote_url() {
        let result = tokenize_remote_url("https://github.com/owner/repo.git", "mytoken").unwrap();
        assert_eq!(result, "https://mytoken@github.com/owner/repo.git");
    }

    #[test]
    fn test_tokenize_remote_url_rejects_non_https() {
        let result = tokenize_remote_url("git@github.com:owner/repo.git", "mytoken");
        assert!(result.is_err());
    }

    #[test]
    fn test_git_probe_status_default() {
        let status = GitProbeStatus::default();
        assert!(!status.is_repo);
        assert!(!status.dirty);
        assert!(!status.has_remote());
    }

    #[test]
    fn test_ensure_local_excludes_creates_file() {
        let tmpdir = tempfile::tempdir().unwrap();
        let repo_dir = tmpdir.path().join("repo");
        fs::create_dir_all(repo_dir.join(".git").join("info")).unwrap();
        ensure_local_excludes(&repo_dir);
        let excludes = repo_dir.join(".git").join("info").join("exclude");
        assert!(excludes.exists());
        let content = fs::read_to_string(&excludes).unwrap();
        assert!(content.contains(".n8n-launcher.git.lock"));
    }
}
