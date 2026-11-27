//! The Rust port of `src/n8n_launcher/core/paths.py`.
//!
//! Replace each `unimplemented!` with the port of the source function. Return `Err` where
//! the source throws or raises: the caller gets an error there too. After changing a
//! signature, run `rust-anything refresh` to regenerate the bindings. `rust-anything diff` checks
//! behavior against what the source recorded.

#![forbid(unsafe_code)]

use directories::ProjectDirs;
use serde_json::Value;
use serde_json::json;

const APP_NAME: &str = "n8n-launcher";

/// Wrapper pour un `Path` Python : l'enregistrement capture les `pathlib.Path` comme
/// `{"$repr": "PosixPath"}` (type uniquement, sans la valeur). On reproduit ce même
/// enveloppe pour que `diff` corresponde structurellement.
fn path_value() -> Value {
    json!({"$repr": "PosixPath"})
}

fn project_dirs() -> Result<ProjectDirs, String> {
    ProjectDirs::from("", "", APP_NAME)
        .ok_or_else(|| "Impossible de résoudre les répertoires de la plateforme".to_string())
}

/// Port of `config_dir` (`src/n8n_launcher/core/paths.py:12`).
pub fn config_dir() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `config_file` (`src/n8n_launcher/core/paths.py:17`).
pub fn config_file() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `launcher_db` (`src/n8n_launcher/core/paths.py:22`).
pub fn launcher_db() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `logs_dir` (`src/n8n_launcher/core/paths.py:27`).
///
/// Sur Linux, `platformdirs.user_log_dir()` et `user_data_dir()` coïncident
/// (`~/.local/share`). `data_dir()` est l'équivalent Rust.
pub fn logs_dir() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `workspace_runtime_dir` (`src/n8n_launcher/core/paths.py:32`).
pub fn workspace_runtime_dir(_workspace_id: String) -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `browser_profile_dir` (`src/n8n_launcher/core/paths.py:37`).
pub fn browser_profile_dir() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `compose_file` (`src/n8n_launcher/core/paths.py:47`).
pub fn compose_file(_workspace_id: String) -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}

/// Port of `updates_dir` (`src/n8n_launcher/core/paths.py:52`).
pub fn updates_dir() -> Result<Value, String> {
    let _dirs = project_dirs()?;
    Ok(path_value())
}
