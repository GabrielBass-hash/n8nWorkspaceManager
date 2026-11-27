//! Per-workspace data-database connection parameters and n8n credentials.
//! Port de `src/n8n_launcher/database/credentials.py`.

use crate::core::models::{
    DbMode, WORKSPACE_DATA_DB_NAME, WORKSPACE_DATA_DB_PASSWORD, WORKSPACE_DATA_DB_USER,
};
use crate::core::models::Workspace;
use crate::n8n::api::N8nApiClient;

/// Connection parameters for a workspace's managed Postgres service.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DatabaseTarget {
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
}

/// Return the Postgres connection target for a MANAGED workspace, else None.
pub fn data_db_target(workspace: &Workspace) -> Option<DatabaseTarget> {
    if workspace.db.mode == DbMode::MANAGED {
        Some(DatabaseTarget {
            host: "postgres".to_string(),
            port: 5432,
            database: workspace.db.database_name.clone().unwrap_or_else(|| WORKSPACE_DATA_DB_NAME.to_string()),
            user: workspace.db.username.clone().unwrap_or_else(|| WORKSPACE_DATA_DB_USER.to_string()),
            password: workspace.db.password.clone().unwrap_or_else(|| WORKSPACE_DATA_DB_PASSWORD.to_string()),
        })
    } else {
        None
    }
}

/// Ensure the workspace's Postgres credential exists inside n8n.
///
/// Returns `True` when a credential was created or already existed.
pub fn configure_db_credential(api: &N8nApiClient, workspace: &Workspace) -> Result<bool, String> {
    let target = match data_db_target(workspace) {
        Some(t) => t,
        None => return Ok(false),
    };

    api.ensure_postgres_credential(
        &format!("workspace-{}", workspace.name),
        &target.host,
        target.port,
        &target.database,
        &target.user,
        &target.password,
    )
    .map_err(|e| e.to_string())?;

    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::{DbConfig, Workspace};

    #[test]
    fn test_data_db_target_none_mode() {
        let workspace = Workspace::new(
            "ws-1".to_string(),
            "Test".to_string(),
            std::path::PathBuf::from("/tmp"),
            5678,
            DbConfig::new(DbMode::NONE),
        );
        assert!(data_db_target(&workspace).is_none());
    }

    #[test]
    fn test_data_db_target_managed_mode_defaults() {
        let db = DbConfig::new(DbMode::MANAGED);
        let workspace = Workspace::new(
            "ws-1".to_string(),
            "My WS".to_string(),
            std::path::PathBuf::from("/tmp/workflows"),
            5678,
            db,
        );
        let target = data_db_target(&workspace).unwrap();
        assert_eq!(target.host, "postgres");
        assert_eq!(target.port, 5432);
        assert_eq!(target.database, "data");
        assert_eq!(target.user, "n8ndata");
        assert!(!target.password.is_empty());
    }

    #[test]
    fn test_data_db_target_managed_mode_explicit() {
        let mut db = DbConfig::new(DbMode::MANAGED);
        db.database_name = Some("mydb".to_string());
        db.username = Some("myuser".to_string());
        db.password = Some("mypassword".to_string());
        let workspace = Workspace::new(
            "ws-1".to_string(),
            "My WS".to_string(),
            std::path::PathBuf::from("/tmp/workflows"),
            5678,
            db,
        );
        let target = data_db_target(&workspace).unwrap();
        assert_eq!(target.database, "mydb");
        assert_eq!(target.user, "myuser");
        assert_eq!(target.password, "mypassword");
    }
}
