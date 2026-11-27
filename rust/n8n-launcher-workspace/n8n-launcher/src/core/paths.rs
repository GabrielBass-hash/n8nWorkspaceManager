//! Platform-specific launcher paths.
//! Port de `src/n8n_launcher/core/paths.py`.

use directories::ProjectDirs;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

pub const APP_NAME: &str = "n8n-launcher";

/// Return the platform-specific configuration directory for n8n-launcher.
pub fn config_dir() -> Result<PathBuf, String> {
    let dirs = ProjectDirs::from("", "", APP_NAME)
        .ok_or_else(|| "Impossible de résoudre les répertoires de la plateforme".to_string())?;
    Ok(dirs.config_dir().to_path_buf())
}

/// Return the path of the legacy JSON configuration file.
pub fn config_file() -> Result<PathBuf, String> {
    Ok(config_dir()?.join("config.json"))
}

/// Return the full path of the SQLite configuration database.
pub fn launcher_db() -> Result<PathBuf, String> {
    Ok(config_dir()?.join("launcher.db"))
}

/// Return the platform-specific log directory for n8n-launcher.
pub fn logs_dir() -> Result<PathBuf, String> {
    let dirs = ProjectDirs::from("", "", APP_NAME)
        .ok_or_else(|| "Impossible de résoudre les répertoires de la plateforme".to_string())?;
    // Sur Linux, `platformdirs.user_log_dir()` et `user_data_dir()` coïncident
    // (`~/.local/share`). `data_dir()` est l'équivalent Rust.
    Ok(dirs.data_dir().to_path_buf())
}

/// Return the runtime directory for a workspace (Compose file, etc.).
pub fn workspace_runtime_dir(workspace_id: &str) -> Result<PathBuf, String> {
    Ok(config_dir()?.join("workspaces").join(workspace_id))
}

/// Return the Chromium profile the launcher opens its n8n windows with.
pub fn browser_profile_dir() -> Result<PathBuf, String> {
    Ok(config_dir()?.join("browser"))
}

/// Return the Compose YAML path for a workspace.
pub fn compose_file(workspace_id: &str) -> Result<PathBuf, String> {
    Ok(workspace_runtime_dir(workspace_id)?.join("compose.yml"))
}

/// Return the staging directory for downloaded update artifacts and helpers.
pub fn updates_dir() -> Result<PathBuf, String> {
    Ok(config_dir()?.join("updates"))
}

/// Serializable path wrapper for JSON (de)serialization of config.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SerializablePath(pub PathBuf);

impl From<PathBuf> for SerializablePath {
    fn from(p: PathBuf) -> Self {
        SerializablePath(p)
    }
}

impl From<SerializablePath> for PathBuf {
    fn from(s: SerializablePath) -> Self {
        s.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compose_file_path() {
        let path = compose_file("ws-1234").unwrap();
        assert!(path.to_string_lossy().contains("workspaces"));
        assert!(path.to_string_lossy().contains("ws-1234"));
        assert!(path.to_string_lossy().ends_with("compose.yml"));
    }

    #[test]
    fn test_workspace_runtime_dir_path() {
        let path = workspace_runtime_dir("ws-1234").unwrap();
        assert!(path.to_string_lossy().contains("workspaces"));
        assert!(path.to_string_lossy().contains("ws-1234"));
    }

    #[test]
    fn test_browser_profile_dir() {
        let path = browser_profile_dir().unwrap();
        assert!(path.to_string_lossy().contains("browser"));
    }

    #[test]
    fn test_updates_dir() {
        let path = updates_dir().unwrap();
        assert!(path.to_string_lossy().contains("updates"));
    }

    #[test]
    fn test_config_dir_contains_app_name() {
        let dir = config_dir().unwrap();
        assert!(dir.to_string_lossy().contains("n8n-launcher"));
    }

    #[test]
    fn test_launcher_db_ends_with_db() {
        let path = launcher_db().unwrap();
        assert!(path.to_string_lossy().ends_with("launcher.db"));
    }

    #[test]
    fn test_compose_file_different_ids() {
        let p1 = compose_file("ws-aaaa").unwrap();
        let p2 = compose_file("ws-bbbb").unwrap();
        assert_ne!(p1, p2);
    }
}
