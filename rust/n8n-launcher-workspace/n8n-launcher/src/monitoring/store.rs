//! Event store: SQLite journal.
//! Port de `src/n8n_launcher/monitoring/store.py`.

use std::path::PathBuf;
use std::sync::Mutex;
use thiserror::Error;

use crate::core::paths::logs_dir;
use crate::monitoring::events::Event;

pub const RETENTION_DAYS: u32 = 30;
const _EVENTS_FILENAME: &str = "events.db";
const _BUSY_TIMEOUT_MS: u32 = 10_000;

const _SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name      TEXT NOT NULL,
    level     TEXT NOT NULL,
    message   TEXT NOT NULL,
    context   TEXT NOT NULL,
    exception TEXT
);
CREATE INDEX IF NOT EXISTS events_timestamp_idx ON events(timestamp);
CREATE INDEX IF NOT EXISTS events_name_idx ON events(name);
CREATE INDEX IF NOT EXISTS events_level_idx ON events(level);
";

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("String error: {0}")]
    String(String),
    #[error("event store is closed")]
    Closed,
}

impl From<String> for StoreError {
    fn from(s: String) -> Self {
        StoreError::String(s)
    }
}

pub struct EventStore {
    _path: PathBuf,
    pub retention_days: u32,
    connection: Mutex<Option<rusqlite::Connection>>,
}

impl EventStore {
    /// Ouvre le store d'événements dans le répertoire de logs par défaut.
    pub fn new() -> Result<Self, StoreError> {
        let path = logs_dir()?.join(_EVENTS_FILENAME);
        Self::with_path(path, RETENTION_DAYS)
    }

    /// Ouvre le store d'événements sur un chemin explicite.
    pub fn with_path(path: PathBuf, retention_days: u32) -> Result<Self, StoreError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let connection = rusqlite::Connection::open(&path)?;
        connection.busy_timeout(std::time::Duration::from_millis(_BUSY_TIMEOUT_MS as u64))?;
        connection.execute_batch(_SCHEMA)?;

        let store = Self {
            _path: path,
            retention_days,
            connection: Mutex::new(Some(connection)),
        };

        store.prune_old_events()?;
        Ok(store)
    }

    /// Retourne si le store a encore une connexion ouverte.
    pub fn connection_is_open(&self) -> bool {
        self.connection.lock().unwrap().is_some()
    }

    /// Insère *event*, lui assigne son identifiant de ligne et applique la rétention.
    pub fn append(&self, event: &Event) -> Result<i64, StoreError> {
        let mut connection = self.connection.lock().unwrap();
        let connection = connection.as_mut().ok_or(StoreError::Closed)?;

        let context_json = serde_json::to_string(&event.context)?;
        let exception = event.exception.as_deref();

        let mut stmt = connection.prepare(
            "INSERT INTO events(timestamp, name, level, message, context, exception) \
             VALUES(?1, ?2, ?3, ?4, ?5, ?6)",
        )?;
        stmt.execute(
            (
                &event.timestamp,
                &event.name,
                &event.level,
                &event.message,
                &context_json,
                exception,
            ),
        )?;

        let event_id = connection.last_insert_rowid();

        let cutoff = _retention_cutoff(self.retention_days);
        connection.execute("DELETE FROM events WHERE timestamp < ?1", [&cutoff])?;

        Ok(event_id)
    }

    /// Supprime les événements hors fenêtre de rétention, retourne le nombre supprimé.
    pub fn prune_old_events(&self) -> Result<u32, StoreError> {
        let cutoff = _retention_cutoff(self.retention_days);
        let mut connection = self.connection.lock().unwrap();
        let connection = connection.as_mut().ok_or(StoreError::Closed)?;

        let count = connection.execute("DELETE FROM events WHERE timestamp < ?1", [&cutoff])?;
        Ok(count as u32)
    }

    /// Lit les événements dans l'ordre chronologique, optionnellement bornés par temps ou nombre.
    pub fn read_events(
        &self,
        limit: Option<u32>,
        since: Option<&str>,
        until: Option<&str>,
    ) -> Result<Vec<Event>, StoreError> {
        _validate_limit(limit)?;
        _validate_interval(since, until)?;

        self.prune_old_events()?;

        let mut clauses = Vec::new();
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
        _add_time_filters(&mut clauses, &mut params, since, until);

        let mut connection = self.connection.lock().unwrap();
        let connection = connection.as_mut().ok_or(StoreError::Closed)?;

        let mut sql = "SELECT id, timestamp, name, level, message, context, exception FROM events".to_string();
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        sql.push_str(" ORDER BY timestamp ASC, id ASC");
        if let Some(limit) = limit {
            sql.push_str(" LIMIT ?1");
            params.push(Box::new(limit));
        }

        let mut stmt = connection.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(params.into_iter()), _row_to_event)?;
        let mut events = Vec::new();
        for row in rows {
            events.push(row?);
        }
        drop(stmt);
        Ok(events)
    }

    /// Lit les événements les plus récents en premier, bornés par *limit*.
    pub fn latest_events(&self, limit: u32) -> Result<Vec<Event>, StoreError> {
        _validate_limit(Some(limit))?;
        self.prune_old_events()?;

        let mut connection = self.connection.lock().unwrap();
        let connection = connection.as_mut().ok_or(StoreError::Closed)?;

        let sql = "SELECT id, timestamp, name, level, message, context, exception \
                   FROM events ORDER BY timestamp DESC, id DESC LIMIT ?1";
        let mut stmt = connection.prepare(sql)?;
        let rows = stmt.query_map([&limit], _row_to_event)?;
        let mut events = Vec::new();
        for row in rows {
            events.push(row?);
        }
        Ok(events)
    }

    /// Recherche des événements par nom, message, contexte ou texte d'exception.
    pub fn search_events(
        &self,
        query: Option<&str>,
        name: Option<&str>,
        level: Option<&str>,
        since: Option<&str>,
        until: Option<&str>,
        limit: Option<u32>,
    ) -> Result<Vec<Event>, StoreError> {
        _validate_limit(limit)?;
        _validate_interval(since, until)?;
        self.prune_old_events()?;

        let mut clauses = Vec::new();
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

        if let Some(q) = query {
            let pattern = _like_pattern(q);
            clauses.push(
                "(name LIKE ?1 ESCAPE '\\' OR message LIKE ?2 ESCAPE '\\' \
                 OR context LIKE ?3 ESCAPE '\\' OR exception LIKE ?4 ESCAPE '\\')"
                    .to_string(),
            );
            params.push(Box::new(pattern.clone()));
            params.push(Box::new(pattern.clone()));
            params.push(Box::new(pattern.clone()));
            params.push(Box::new(pattern));
        }
        if let Some(n) = name {
            clauses.push("name LIKE ? ESCAPE '\\'".to_string());
            params.push(Box::new(_like_pattern(n)));
        }
        if let Some(lvl) = level {
            clauses.push("level = ?".to_string());
            params.push(Box::new(lvl.to_uppercase()));
        }
        _add_time_filters(&mut clauses, &mut params, since, until);

        let mut connection = self.connection.lock().unwrap();
        let connection = connection.as_mut().ok_or(StoreError::Closed)?;

        let mut sql = "SELECT id, timestamp, name, level, message, context, exception FROM events".to_string();
        if !clauses.is_empty() {
            sql.push_str(" WHERE ");
            sql.push_str(&clauses.join(" AND "));
        }
        sql.push_str(" ORDER BY timestamp ASC, id ASC");
        if let Some(limit) = limit {
            sql.push_str(" LIMIT ?");
            params.push(Box::new(limit));
        }

        let mut stmt = connection.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(params.into_iter()), _row_to_event)?;
        let mut events = Vec::new();
        for row in rows {
            events.push(row?);
        }
        drop(stmt);
        Ok(events)
    }

    /// Écrit les événements correspondants sous forme de tableau JSON et retourne le nombre de lignes.
    pub fn export_events(
        &self,
        target: &std::path::Path,
        query: Option<&str>,
        name: Option<&str>,
        level: Option<&str>,
        since: Option<&str>,
        until: Option<&str>,
        limit: Option<u32>,
    ) -> Result<u32, StoreError> {
        let events = self.search_events(query, name, level, since, until, limit)?;

        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let payload = serde_json::to_string_pretty(&events)?;
        let mut payload_with_newline = payload;
        payload_with_newline.push('\n');

        let temp_name = target.with_extension("tmp");
        {
            use std::io::Write;
            let mut file = std::fs::File::create(&temp_name)?;
            file.write_all(payload_with_newline.as_bytes())?;
            file.flush()?;
            file.sync_all()?;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&temp_name, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(&temp_name, target)?;
        _restrict_file(target)?;

        Ok(events.len() as u32)
    }

    /// Ferme la connexion SQLite.
    pub fn close(&self) -> Result<(), StoreError> {
        let mut connection = self.connection.lock().unwrap();
        if let Some(conn) = connection.take() {
            match conn.close() {
                Ok(()) => Ok(()),
                Err((_conn, e)) => Err(StoreError::Sqlite(e)),
            }
        } else {
            Ok(())
        }
    }
}

impl Drop for EventStore {
    fn drop(&mut self) {
        let _ = self.close();
    }
}
/// Retourne le pattern LIKE avec les caractères spéciaux échappés.
fn _like_pattern(value: &str) -> String {
    let escaped = value
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    format!("%{escaped}%")
}

/// Retourne l'horodatage de rétention avant lequel les événements sont expirés.
fn _retention_cutoff(retention_days: u32) -> String {
    let cutoff = chrono::Utc::now() - chrono::Duration::days(retention_days as i64);
    cutoff.format("%Y-%m-%dT%H:%M:%S%.6fZ").to_string()
}

/// Retourne l'horodatage actuel au format de stockage.
fn _now_timestamp() -> Result<String, StoreError> {
    Ok(chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S%.6fZ").to_string())
}

/// Ajoute les filtres de temps inclusifs à une requête SQL.
fn _add_time_filters(
    clauses: &mut Vec<String>,
    params: &mut Vec<Box<dyn rusqlite::ToSql>>,
    since: Option<&str>,
    until: Option<&str>,
) {
    if let Some(s) = since {
        clauses.push("timestamp >= ?".to_string());
        params.push(Box::new(s.to_string()));
    }
    if let Some(u) = until {
        clauses.push("timestamp <= ?".to_string());
        params.push(Box::new(u.to_string()));
    }
}

/// Valide une limite optionnelle.
fn _validate_limit(limit: Option<u32>) -> Result<(), StoreError> {
    if let Some(limit) = limit {
        if limit < 1 {
            return Err(StoreError::String("limit must be greater than zero".to_string()));
        }
    }
    Ok(())
}

/// Valide un intervalle de temps optionnel.
fn _validate_interval(since: Option<&str>, until: Option<&str>) -> Result<(), StoreError> {
    match (since, until) {
        (None, _) | (_, None) => Ok(()),
        (Some(s), Some(u)) => {
            if s > u {
                Err(StoreError::String("since must not be after until".to_string()))
            } else {
                Ok(())
            }
        }
    }
}

/// Convertit une ligne SQLite en événement.
fn _row_to_event(row: &rusqlite::Row<'_>) -> Result<Event, rusqlite::Error> {
    let raw_context: String = row.get("context")?;
    let context: Option<serde_json::Value> = serde_json::from_str(&raw_context).ok();
    let exception: Option<String> = row.get("exception")?;

    Ok(Event {
        id: Some(row.get::<_, i64>("id")?),
        timestamp: row.get("timestamp")?,
        name: row.get("name")?,
        level: row.get("level")?,
        message: row.get("message")?,
        context,
        exception,
    })
}

/// Crée un répertoire privé et resserre ses droits sur POSIX.
fn _restrict_dir(path: &std::path::Path) -> Result<(), StoreError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

/// Resserre les droits d'un fichier de monitoring sur POSIX.
fn _restrict_file(path: &std::path::Path) -> Result<(), StoreError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

/// Resserre les droits de la base de données et de ses sidecars WAL/SHM.
fn _restrict_db_files(path: &std::path::Path) -> Result<(), StoreError> {
    _restrict_file(path)?;
    for suffix in ["-wal", "-shm"] {
        let sidecar = path.with_file_name(
            path.file_name().unwrap_or_default().to_string_lossy().to_string() + suffix,
        );
        if sidecar.exists() {
            _restrict_file(&sidecar)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_store() -> Result<(EventStore, tempfile::TempDir), StoreError> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("events.db");
        let store = EventStore::with_path(path, 30)?;
        Ok((store, dir))
    }
    #[test]
    fn test_append_and_latest() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        let event = Event::new("test".to_string(), "INFO".to_string(), "hello".to_string());
        let id = store.append(&event)?;
        assert!(id > 0);

        let latest = store.latest_events(10)?;
        assert_eq!(latest.len(), 1);
        assert_eq!(latest[0].name, "test");
        assert_eq!(latest[0].message, "hello");
        assert_eq!(latest[0].id, Some(id));
        Ok(())
    }

    #[test]
    fn test_read_events_chronological() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("a".to_string(), "INFO".to_string(), "first".to_string()))?;
        store.append(&Event::new("b".to_string(), "INFO".to_string(), "second".to_string()))?;

        let events = store.read_events(None, None, None)?;
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].name, "a");
        assert_eq!(events[1].name, "b");
        Ok(())
    }

    #[test]
    fn test_search_by_name() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("deploy".to_string(), "INFO".to_string(), "started".to_string()))?;
        store.append(&Event::new("build".to_string(), "ERROR".to_string(), "failed".to_string()))?;

        let events = store.search_events(None, Some("deploy"), None, None, None, None)?;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].name, "deploy");
        Ok(())
    }

    #[test]
    fn test_search_by_level() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("a".to_string(), "INFO".to_string(), "x".to_string()))?;
        store.append(&Event::new("b".to_string(), "ERROR".to_string(), "y".to_string()))?;

        let events = store.search_events(None, None, Some("error"), None, None, None)?;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].level, "ERROR");
        Ok(())
    }

    #[test]
    fn test_search_by_query() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("deploy".to_string(), "INFO".to_string(), "workspace ws1 started".to_string()))?;
        store.append(&Event::new("build".to_string(), "INFO".to_string(), "workspace ws2 built".to_string()))?;

        let events = store.search_events(Some("ws1"), None, None, None, None, None)?;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].name, "deploy");
        Ok(())
    }

    #[test]
    fn test_prune_old_events() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("old".to_string(), "INFO".to_string(), "x".to_string()))?;
        let count = store.prune_old_events()?;
        assert_eq!(count, 0);
        Ok(())
    }

    #[test]
    fn test_connection_is_open() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        assert!(store.connection_is_open());
        store.close()?;
        assert!(!store.connection_is_open());
        Ok(())
    }

    #[test]
    fn test_export_events() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.append(&Event::new("export_test".to_string(), "INFO".to_string(), "data".to_string()))?;

        let dir = tempfile::tempdir()?;
        let target = dir.path().join("export.json");
        let count = store.export_events(&target, None, None, None, None, None, None)?;
        assert_eq!(count, 1);
        assert!(target.exists());
        Ok(())
    }

    #[test]
    fn test_closed_store_returns_error() -> Result<(), StoreError> {
        let (store, _dir) = test_store()?;
        store.close()?;
        let result = store.append(&Event::new("x".to_string(), "INFO".to_string(), "y".to_string()));
        assert!(matches!(result, Err(StoreError::Closed)));
        Ok(())
    }

    #[test]
    fn test_validate_limit() {
        assert!(_validate_limit(None).is_ok());
        assert!(_validate_limit(Some(1)).is_ok());
        assert!(_validate_limit(Some(0)).is_err());
    }

    #[test]
    fn test_validate_interval() {
        assert!(_validate_interval(None, None).is_ok());
        assert!(_validate_interval(Some("a"), None).is_ok());
        assert!(_validate_interval(None, Some("b")).is_ok());
        assert!(_validate_interval(Some("b"), Some("a")).is_err());
        assert!(_validate_interval(Some("a"), Some("b")).is_ok());
    }

    #[test]
    fn test_like_pattern() {
        assert_eq!(_like_pattern("ws_1"), "%ws\\_1%");
        assert_eq!(_like_pattern("100%"), "%100\\%%");
        assert_eq!(_like_pattern("normal"), "%normal%");
    }

    #[test]
    fn test_drop_closes() -> Result<(), StoreError> {
        {
            let (store, _dir) = test_store()?;
            assert!(store.connection_is_open());
            let _ = store;
        }
        Ok(())
    }

    // Helper to make tempdir live long enough in tests
    #[allow(dead_code)]
    fn _keep_dir(_dir: tempfile::TempDir) {}
}
