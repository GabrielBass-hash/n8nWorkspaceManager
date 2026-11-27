//! Persistent application configuration, stored in SQLite.
//! Port de `src/n8n_launcher/core/config.py`.

use crate::core::models::AppConfig;
use serde_json::Value;
use std::path::{Path, PathBuf};
use thiserror::Error;

/// Raised when launcher configuration cannot be read or written.
#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("La configuration du launcher n'existe pas")]
    NotFound,
    #[error("Invalid launcher configuration: {0}")]
    Invalid(String),
    #[error("Could not save launcher configuration: {0}")]
    SaveError(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
";

const _APP_KEYS: &[&str] = &["owner_email", "owner_password", "work_dir", "github_token"];
const SCHEMA_VERSION: i32 = 1;
const BUSY_TIMEOUT_MS: u32 = 10_000;

fn store_meta(config: &AppConfig) -> Vec<(String, String)> {
    vec![
        ("owner_email".to_string(), config.owner_email.clone()),
        ("owner_password".to_string(), config.owner_password.clone()),
        ("work_dir".to_string(), config.work_dir.to_string_lossy().to_string()),
        (
            "github_token".to_string(),
            config.github_token.as_deref().unwrap_or("").to_string(),
        ),
    ]
}

/// Load and save `AppConfig` to a SQLite database.
pub struct ConfigStore {
    path: PathBuf,
    legacy_json: Option<PathBuf>,
}

impl Clone for ConfigStore {
    fn clone(&self) -> Self {
        Self {
            path: self.path.clone(),
            legacy_json: self.legacy_json.clone(),
        }
    }
}

impl ConfigStore {
    pub fn new<P: Into<PathBuf>>(path: P) -> Self {
        Self {
            path: path.into(),
            legacy_json: None,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<AppConfig, ConfigError> {
        if !self.path.exists() {
            if let Some(legacy) = &self.legacy_json {
                if legacy.exists() {
                    self.migrate_legacy()?;
                } else {
                    return Err(ConfigError::NotFound);
                }
            } else {
                return Err(ConfigError::NotFound);
            }
        }
        let conn = self.connect()?;
        self.read(&conn)
    }

    pub fn save(&self, config: &AppConfig) -> Result<(), ConfigError> {
        let mut conn = self.connect()?;
        let tx = conn.transaction()?;
        self.write(&tx, config)?;
        tx.commit()?;
        Ok(())
    }

    pub fn mutate<T, F>(&self, f: F) -> Result<T, ConfigError>
    where
        F: FnOnce(&mut AppConfig) -> T,
    {
        if !self.path.exists() {
            if let Some(legacy) = &self.legacy_json {
                if legacy.exists() {
                    self.migrate_legacy()?;
                } else {
                    return Err(ConfigError::NotFound);
                }
            } else {
                return Err(ConfigError::NotFound);
            }
        }
        let mut conn = self.connect()?;
        let tx = conn.transaction()?;
        let mut config = self.read(&tx)?;
        let result = f(&mut config);
        self.write(&tx, &config)?;
        tx.commit()?;
        Ok(result)
    }

    pub fn export_json(&self, target: &Path) -> Result<(), ConfigError> {
        let config = self.load()?;
        let json = serde_json::to_string_pretty(&config.to_value()).map_err(|e| {
            ConfigError::SaveError(format!("Could not serialize configuration: {e}"))
        })?;
        std::fs::create_dir_all(target.parent().unwrap_or(Path::new(".")))?;
        std::fs::write(target, json).map_err(|e| ConfigError::SaveError(e.to_string()))?;
        Ok(())
    }

    pub fn close(&self) -> Result<(), ConfigError> {
        // No persistent connection to close; no-op for API compatibility.
        Ok(())
    }

    fn connect(&self) -> Result<rusqlite::Connection, ConfigError> {
        restrict_dir(self.path.parent().unwrap_or(Path::new(".")))?;
        let conn = rusqlite::Connection::open(&self.path)?;
        config_pragma(&conn, "PRAGMA journal_mode=WAL")?;
        config_pragma(&conn, &format!("PRAGMA busy_timeout={BUSY_TIMEOUT_MS}"))?;
        config_pragma(&conn, "PRAGMA synchronous=NORMAL")?;
        conn.execute_batch(SCHEMA)?;
        restrict_db_files(&self.path)?;
        Ok(conn)
    }

    fn read(&self, conn: &rusqlite::Connection) -> Result<AppConfig, ConfigError> {
        let mut meta_map: std::collections::HashMap<String, String> = std::collections::HashMap::new();
        let mut stmt = conn.prepare("SELECT key, value FROM meta")?;
        let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
        for row in rows {
            let (k, v) = row?;
            meta_map.insert(k, v);
        }

        if !meta_map.contains_key("owner_email") {
            return Err(ConfigError::Invalid("Configuration launcher vide ou incomplète".to_string()));
        }

        let payload = serde_json::json!({
            "owner_email": meta_map.get("owner_email"),
            "owner_password": meta_map.get("owner_password"),
            "work_dir": meta_map.get("work_dir"),
            "github_token": meta_map.get("github_token"),
            "workspaces": self.read_workspaces(conn)?,
        });

        let config: AppConfig = serde_json::from_value(payload).map_err(|e| {
            ConfigError::Invalid(format!("Invalid launcher configuration: {e}"))
        })?;
        Ok(config)
    }

    fn read_workspaces(&self, conn: &rusqlite::Connection) -> Result<Vec<Value>, ConfigError> {
        let mut stmt = conn.prepare("SELECT data FROM workspaces ORDER BY rowid")?;
        let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
        let mut workspaces = Vec::new();
        for row in rows {
            let data = row?;
            workspaces.push(serde_json::from_str(&data).map_err(|e| {
                ConfigError::Invalid(format!("Invalid workspace data: {e}"))
            })?);
        }
        Ok(workspaces)
    }

    fn write(&self, conn: &rusqlite::Connection, config: &AppConfig) -> Result<(), ConfigError> {
        let previous: std::collections::HashMap<String, String> = {
            let mut stmt = conn.prepare("SELECT id, data FROM workspaces")?;
            let rows = stmt.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)))?;
            let mut map = std::collections::HashMap::new();
            for row in rows {
                let (id, data) = row?;
                map.insert(id, data);
            }
            map
        };

        let current: std::collections::HashMap<String, String> = config
            .workspaces
            .iter()
            .map(|ws| (ws.id.clone(), serde_json::to_string(&ws.to_value()).unwrap()))
            .collect();

        for (key, value) in store_meta(config) {
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                &[&key, &value],
            )?;
        }

        for (workspace_id, data) in &current {
            if previous.get(workspace_id) != Some(data) {
                conn.execute(
                    "INSERT INTO workspaces(id, data) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                    &[workspace_id, data],
                )?;
            }
        }

        for workspace_id in previous.keys() {
            if !current.contains_key(workspace_id) {
                conn.execute("DELETE FROM workspaces WHERE id = ?", &[workspace_id])?;
            }
        }

        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            &[&SCHEMA_VERSION.to_string()],
        )?;

        Ok(())
    }

    fn migrate_legacy(&self) -> Result<(), ConfigError> {
        let legacy = self
            .legacy_json
            .as_deref()
            .ok_or_else(|| ConfigError::NotFound)?;
        let content = std::fs::read_to_string(legacy).map_err(|e| ConfigError::Invalid(e.to_string()))?;
        let config: AppConfig = serde_json::from_str(&content).map_err(|e| ConfigError::Invalid(e.to_string()))?;
        let mut conn = self.connect()?;
        let tx = conn.transaction()?;
        self.write(&tx, &config)?;
        tx.commit()?;
        tracing::info!("Migrated legacy config {} to SQLite database {}", legacy.display(), self.path.display());
        Ok(())
    }

    /// Backup an unreadable config aside.
    pub fn backup_unreadable(&self) -> Result<(), ConfigError> {
        let timestamp = chrono::Utc::now().format("%Y%m%d-%H%M%S");
        let file_name = self.path.file_name().unwrap().to_string_lossy();
        let backup = self.path.with_file_name(format!("{file_name}.corrupt-{timestamp}"));
        std::fs::rename(&self.path, &backup).map_err(|e| {
            tracing::warn!("Configuration illisible : aucune sauvegarde créée ({e})");
            ConfigError::Io(e)
        })?;
        tracing::warn!(
            "Configuration illisible : copie de sauvegarde créée sous {} (elle contient vos identifiants — ne la partagez pas)",
            backup.display()
        );
        Ok(())
    }
}

fn config_pragma(conn: &rusqlite::Connection, sql: &str) -> Result<(), ConfigError> {
    match conn.query_row(sql, [], |_| Ok(())) {
        Ok(()) => Ok(()),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(()),
        Err(e) => Err(ConfigError::Sqlite(e)),
    }
}

fn restrict_dir(path: &Path) -> Result<(), ConfigError> {
    std::fs::create_dir_all(path).map_err(ConfigError::Io)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o700);
        std::fs::set_permissions(path, perms).map_err(ConfigError::Io)?;
    }
    Ok(())
}

fn restrict_db_files(path: &Path) -> Result<(), ConfigError> {
    restrict_file(path)?;
    for suffix in ["-wal", "-shm"] {
        let sidecar = PathBuf::from(format!("{}{}", path.display(), suffix));
        if sidecar.exists() {
            restrict_file(&sidecar)?;
        }
    }
    Ok(())
}

fn restrict_file(path: &Path) -> Result<(), ConfigError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o600);
        std::fs::set_permissions(path, perms).map_err(ConfigError::Io)?;
    }
    Ok(())
}

impl AppConfig {
    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::{DbConfig, DbMode, Workspace};
    use std::path::PathBuf;

    fn test_config_store(tmpdir: &std::path::Path) -> ConfigStore {
        ConfigStore::new(tmpdir.join("launcher.db"))
    }

    #[test]
    fn test_save_and_load_config() {
        let tmpdir = tempfile::tempdir().unwrap();
        let store = test_config_store(tmpdir.path());

        let config = AppConfig::new(
            "owner@example.com".to_string(),
            "Password123".to_string(),
            tmpdir.path().join("work"),
        );
        store.save(&config).unwrap();

        let loaded = store.load().unwrap();
        assert_eq!(loaded.owner_email, "owner@example.com");
        assert_eq!(loaded.owner_password, "Password123");
        assert_eq!(loaded.work_dir, tmpdir.path().join("work"));
    }

    #[test]
    fn test_load_missing_config_returns_not_found() {
        let tmpdir = tempfile::tempdir().unwrap();
        let store = test_config_store(tmpdir.path());

        let result = store.load();
        assert!(matches!(result, Err(ConfigError::NotFound)));
    }

    #[test]
    fn test_mutate_adds_workspace() {
        let tmpdir = tempfile::tempdir().unwrap();
        let store = test_config_store(tmpdir.path());

        let config = AppConfig::new(
            "owner@example.com".to_string(),
            "Password123".to_string(),
            tmpdir.path().join("work"),
        );
        store.save(&config).unwrap();

        let workspace = Workspace::new(
            "ws-1".to_string(),
            "Test WS".to_string(),
            PathBuf::from("/tmp/ws1"),
            5678,
            DbConfig::new(DbMode::NONE),
        );

        let result = store.mutate(|config| {
            config.workspaces.push(workspace.clone());
            workspace.id.clone()
        }).unwrap();

        assert_eq!(result, "ws-1");

        let loaded = store.load().unwrap();
        assert_eq!(loaded.workspaces.len(), 1);
        assert_eq!(loaded.workspaces[0].id, "ws-1");
    }

    #[test]
    fn test_export_json() {
        let tmpdir = tempfile::tempdir().unwrap();
        let store = test_config_store(tmpdir.path());

        let config = AppConfig::new(
            "owner@example.com".to_string(),
            "Password123".to_string(),
            tmpdir.path().join("work"),
        );
        store.save(&config).unwrap();

        let export_path = tmpdir.path().join("export.json");
        store.export_json(&export_path).unwrap();

        assert!(export_path.exists());
        let content = std::fs::read_to_string(&export_path).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(parsed["owner_email"], "owner@example.com");
    }

    #[test]
    fn test_config_error_display() {
        let err = ConfigError::NotFound;
        assert_eq!(err.to_string(), "La configuration du launcher n'existe pas");

        let err = ConfigError::Invalid("test".to_string());
        assert_eq!(err.to_string(), "Invalid launcher configuration: test");
    }

    #[test]
    fn test_restrict_file_permissions() {
        let tmpdir = tempfile::tempdir().unwrap();
        let file = tmpdir.path().join("test.txt");
        std::fs::write(&file, "hello").unwrap();

        restrict_file(&file).unwrap();

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let meta = file.metadata().unwrap();
            let mode = meta.permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }
}
