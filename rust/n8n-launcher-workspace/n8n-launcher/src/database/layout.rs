//! Database schema layout detection — migration files and schema declaration.
//! Port de `src/n8n_launcher/database/layout.py`.

use std::path::Path;

/// Return the sorted ``.sql`` files directly inside ``migrations_dir``.
pub fn migrate_files(migrations_dir: &Path) -> Vec<std::path::PathBuf> {
    if !migrations_dir.is_dir() {
        return Vec::new();
    }
    let mut files: Vec<std::path::PathBuf> = match migrations_dir.read_dir() {
        Ok(entries) => entries
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.path().is_file())
            .filter(|entry| {
                entry
                    .path()
                    .extension()
                    .and_then(|ext| ext.to_str())
                    == Some("sql")
            })
            .map(|entry| entry.path())
            .collect(),
        Err(_) => Vec::new(),
    };
    files.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
    files
}

/// Return the sorted ``.sql`` migration files under ``db/migrations/``.
pub fn detect_migrations(workflows_dir: &Path) -> Vec<std::path::PathBuf> {
    migrate_files(&workflows_dir.join("db").join("migrations"))
}

/// Return the ``db/schema.sql`` path when it exists, else None.
pub fn detect_schema(workflows_dir: &Path) -> Option<std::path::PathBuf> {
    let schema = workflows_dir.join("db").join("schema.sql");
    if schema.is_file() {
        Some(schema)
    } else {
        None
    }
}

/// Return True when the folder declares a schema or any migration files.
pub fn has_db_layout(workflows_dir: &Path) -> bool {
    detect_schema(workflows_dir).is_some() || !detect_migrations(workflows_dir).is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_has_db_layout_no_layout() {
        let tmpdir = tempfile::tempdir().unwrap();
        assert!(!has_db_layout(tmpdir.path()));
    }

    #[test]
    fn test_has_db_layout_with_schema() {
        let tmpdir = tempfile::tempdir().unwrap();
        let db_dir = tmpdir.path().join("db");
        fs::create_dir_all(&db_dir).unwrap();
        fs::write(db_dir.join("schema.sql"), "-- schema").unwrap();
        assert!(has_db_layout(tmpdir.path()));
    }

    #[test]
    fn test_has_db_layout_with_migrations() {
        let tmpdir = tempfile::tempdir().unwrap();
        let mig_dir = tmpdir.path().join("db").join("migrations");
        fs::create_dir_all(&mig_dir).unwrap();
        fs::write(mig_dir.join("001_init.sql"), "-- init").unwrap();
        assert!(has_db_layout(tmpdir.path()));
    }

    #[test]
    fn test_detect_migrations_sorted() {
        let tmpdir = tempfile::tempdir().unwrap();
        let mig_dir = tmpdir.path().join("db").join("migrations");
        fs::create_dir_all(&mig_dir).unwrap();
        fs::write(mig_dir.join("002_b.sql"), "-- b").unwrap();
        fs::write(mig_dir.join("001_a.sql"), "-- a").unwrap();
        let migrations = detect_migrations(tmpdir.path());
        assert_eq!(migrations.len(), 2);
        assert_eq!(migrations[0].file_name().unwrap(), "001_a.sql");
        assert_eq!(migrations[1].file_name().unwrap(), "002_b.sql");
    }

    #[test]
    fn test_migrate_files_nonexistent_dir() {
        let files = migrate_files(std::path::Path::new("/tmp/nonexistent_dir_xyz"));
        assert!(files.is_empty());
    }
}
