//! Domain models used by the launcher.
//! Port de `src/n8n_launcher/core/models.py`.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

/// How a workspace accesses its database.
/// ``MANAGED`` runs a local Postgres service inside the Compose project;
/// ``NONE`` runs n8n without any database service.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DbMode {
    MANAGED,
    NONE,
}

impl Default for DbMode {
    fn default() -> Self {
        DbMode::NONE
    }
}

/// Lifecycle states the launcher can persist or display.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum WorkspaceState {
    STOPPED,
    STARTING,
    RUNNING,
    STOPPING,
    ERROR,
}

impl Default for WorkspaceState {
    fn default() -> Self {
        WorkspaceState::STOPPED
    }
}

pub const DEFAULT_POSTGRES_IMAGE: &str = "postgres:16";

// Defaults applied when a managed workspace carries no explicit database parameters.
// Defined here so Compose rendering and the migration runner can both consume them
// without an import cycle.
pub const WORKSPACE_DATA_DB_NAME: &str = "data";
pub const WORKSPACE_DATA_DB_USER: &str = "n8ndata";
pub const WORKSPACE_DATA_DB_PASSWORD: &str = "launcher-managed-data";

// Credentials of the maintenance role n8n itself uses for its own Postgres database
// (``DB_POSTGRESDB_*`` in Compose) and that the migration runner uses to bootstrap
// the data role and database. Values must stay in sync between the Compose renderer
// and `migrations`.
pub const N8N_METADATA_DB_NAME: &str = "n8n";
pub const N8N_METADATA_DB_USER: &str = "n8n";
pub const N8N_METADATA_DB_PASSWORD: &str = "launcher-managed";
pub const MAINTENANCE_DATABASE: &str = "postgres";

/// Git synchronization configuration for a workspace.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct GitConfig {
    pub enabled: bool,
    pub remote_url: Option<String>,
    pub branch: String,
    pub ci_enabled: bool,
    pub ci_credentials: Vec<serde_json::Value>,
}

impl GitConfig {
    pub fn new() -> Self {
        Self::default()
    }
}

/// Remote production deployment settings for a workspace.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ServerConfig {
    pub enabled: bool,
    pub host: String,
    pub ssh_port: u16,
    pub user: String,
    pub key_path: Option<String>,
    pub base_dir: String,
    pub n8n_port: u16,
}

impl ServerConfig {
    pub fn new() -> Self {
        Self {
            ssh_port: 22,
            n8n_port: 5678,
            ..Default::default()
        }
    }
}

/// Database mode plus the parameters needed for a managed data database.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct DbConfig {
    pub mode: DbMode,
    pub database_name: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}

impl DbConfig {
    pub fn new(mode: DbMode) -> Self {
        Self {
            mode,
            database_name: None,
            username: None,
            password: None,
        }
    }
}

/// A user-defined n8n instance bound to a folder of workflow exports.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Workspace {
    pub id: String,
    pub name: String,
    pub workflows_dir: PathBuf,
    pub port: u16,
    pub db: DbConfig,
    pub git: GitConfig,
    pub server: ServerConfig,
    pub git_push_failed: bool,
    pub n8n_version: String,
    pub postgres_image: Option<String>,
    pub postgres_preload_timescaledb: bool,
    pub state: WorkspaceState,
    pub restart_required: bool,
    pub api_key: Option<String>,
    pub server_last_deploy: Option<String>,
    pub server_last_error: Option<String>,
}

impl Workspace {
    pub fn new(id: String, name: String, workflows_dir: PathBuf, port: u16, db: DbConfig) -> Self {
        Self {
            id,
            name,
            workflows_dir,
            port,
            db,
            git: GitConfig::new(),
            server: ServerConfig::new(),
            git_push_failed: false,
            n8n_version: "2.40.0".to_string(),
            postgres_image: None,
            postgres_preload_timescaledb: false,
            state: WorkspaceState::STOPPED,
            restart_required: false,
            api_key: None,
            server_last_deploy: None,
            server_last_error: None,
        }
    }

    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_db_mode_serialization() {
        assert_eq!(serde_json::to_string(&DbMode::MANAGED).unwrap(), "\"managed\"");
        assert_eq!(serde_json::to_string(&DbMode::NONE).unwrap(), "\"none\"");
        assert_eq!(serde_json::from_str::<DbMode>("\"managed\"").unwrap(), DbMode::MANAGED);
        assert_eq!(serde_json::from_str::<DbMode>("\"none\"").unwrap(), DbMode::NONE);
    }

    #[test]
    fn test_workspace_state_default() {
        assert_eq!(WorkspaceState::default(), WorkspaceState::STOPPED);
    }

    #[test]
    fn test_workspace_roundtrip() {
        let workspace = Workspace::new(
            "ws-abc123".to_string(),
            "Mon workspace".to_string(),
            std::path::PathBuf::from("/tmp/workflows"),
            5678,
            DbConfig::new(DbMode::MANAGED),
        );
        let json = serde_json::to_string(&workspace).unwrap();
        let parsed: Workspace = serde_json::from_str(&json).unwrap();
        assert_eq!(workspace, parsed);
        assert_eq!(parsed.state, WorkspaceState::STOPPED);
        assert_eq!(parsed.db.mode, DbMode::MANAGED);
    }

    #[test]
    fn test_app_config_default_workspaces_empty() {
        let config = AppConfig::new(
            "owner@example.com".to_string(),
            "Password123".to_string(),
            std::path::PathBuf::from("/tmp/work"),
        );
        assert!(config.workspaces.is_empty());
        assert!(config.github_token.is_none());
    }

    #[test]
    fn test_default_postgres_image() {
        assert_eq!(DEFAULT_POSTGRES_IMAGE, "postgres:16");
    }

    #[test]
    fn test_workspace_data_defaults() {
        assert_eq!(WORKSPACE_DATA_DB_NAME, "data");
        assert_eq!(WORKSPACE_DATA_DB_USER, "n8ndata");
    }
}

/// Global launcher configuration: owner identity, work dir, workspaces.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppConfig {
    pub owner_email: String,
    pub owner_password: String,
    pub work_dir: PathBuf,
    pub workspaces: Vec<Workspace>,
    pub github_token: Option<String>,
}

impl AppConfig {
    pub fn new(owner_email: String, owner_password: String, work_dir: PathBuf) -> Self {
        Self {
            owner_email,
            owner_password,
            work_dir,
            workspaces: Vec::new(),
            github_token: None,
        }
    }

    pub fn new_test(owner_email: &str, owner_password: &str, work_dir: PathBuf) -> Self {
        Self::new(
            owner_email.to_string(),
            owner_password.to_string(),
            work_dir,
        )
    }
}
