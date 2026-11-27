//! Creation of the data database and idempotent migration application.
//! Port de `src/n8n_launcher/database/migrations.py`.

use std::path::Path;
use std::thread;
use std::time::Duration;

use crate::core::models::{
    DbMode, MAINTENANCE_DATABASE, N8N_METADATA_DB_PASSWORD, N8N_METADATA_DB_USER, Workspace,
};
use crate::database::credentials::data_db_target;
use crate::database::layout::migrate_files;
use crate::docker::manager::{DockerManager, ExecResult};

/// Raised when local data-database migrations cannot be applied.
#[derive(Debug)]
pub struct MigrationError(pub String);

impl std::fmt::Display for MigrationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "MigrationError: {}", self.0)
    }
}

impl std::error::Error for MigrationError {}

const BOOKKEEPING_DDL: &str = "CREATE TABLE IF NOT EXISTS public.schema_migrations (\nfilename text PRIMARY KEY,\napplied_at timestamptz NOT NULL DEFAULT now());\n";

/// Create the data database and apply pending migrations idempotently.
pub struct MigrationRunner {
    docker: DockerManager,
}

impl MigrationRunner {
    pub fn new(docker: DockerManager) -> Self {
        Self { docker }
    }

    /// Create the data role and database if they do not exist yet.
    pub fn ensure(
        &self,
        workspace: &Workspace,
        compose_file: &Path,
    ) -> Result<(), MigrationError> {
        if workspace.db.mode != DbMode::MANAGED {
            return Ok(());
        }
        let target = match data_db_target(workspace) {
            Some(t) => t,
            None => {
                return Err(MigrationError(
                    "cannot run migrations without a managed database target".to_string(),
                ))
            }
        };
        let privileges = if workspace.postgres_preload_timescaledb {
            "LOGIN SUPERUSER"
        } else {
            "LOGIN"
        };
        let role_module = format!(
            "SELECT format('CREATE ROLE %I {} PASSWORD %L', {}, {}) WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname={});\n\\gexec\n",
            privileges,
            sql_literal(&target.user),
            sql_literal(&target.password),
            sql_literal(&target.user)
        );
        let reconcile_role_module = format!(
            "SELECT format('ALTER ROLE %I {} WITH PASSWORD %L', {}, {});\n\\gexec\n",
            privileges,
            sql_literal(&target.user),
            sql_literal(&target.password)
        );
        let database_module = format!(
            "SELECT format('CREATE DATABASE %I OWNER %I', {}, {}) WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname={});\n\\gexec\n",
            sql_literal(&target.database),
            sql_literal(&target.user),
            sql_literal(&target.database)
        );
        let stdin = format!("{}{}{}", role_module, reconcile_role_module, database_module);
        self._run_with_retries(
            workspace,
            compose_file,
            MAINTENANCE_DATABASE,
            N8N_METADATA_DB_USER,
            N8N_METADATA_DB_PASSWORD,
            &stdin,
        )?;
        Ok(())
    }

    /// Apply pending migrations and return their filenames in order.
    pub fn apply(
        &self,
        workspace: &Workspace,
        migrations_dir: &Path,
        compose_file: &Path,
    ) -> Result<Vec<String>, MigrationError> {
        let migrations = migrate_files(migrations_dir);
        if migrations.is_empty() {
            return Ok(Vec::new());
        }
        let target = match data_db_target(workspace) {
            Some(t) => t,
            None => {
                return Err(MigrationError(
                    "cannot run migrations without a managed database target".to_string(),
                ))
            }
        };
        let applied_names = self.applied(workspace, compose_file)?;
        let pending: Vec<_> = migrations
            .iter()
            .filter(|m| {
                applied_names.contains(m.file_name().and_then(|n| n.to_str()).unwrap_or(""))
            })
            .collect();
        for migration in &pending {
            let body = format!(
                "BEGIN;\n{}{}\nINSERT INTO public.schema_migrations(filename) VALUES ({});\nCOMMIT;\n",
                BOOKKEEPING_DDL,
                std::fs::read_to_string(migration).map_err(|e| {
                    MigrationError(format!("cannot read migration file: {e}"))
                })?,
                sql_literal(migration.file_name().and_then(|n| n.to_str()).unwrap_or(""))
            );
            self._run_with_retries(
                workspace,
                compose_file,
                &target.database,
                &target.user,
                &target.password,
                &body,
            )?;
        }
        Ok(pending
            .iter()
            .map(|m| m.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string())
            .collect())
    }

    /// Return the set of migration filenames already recorded as applied.
    pub fn applied(
        &self,
        workspace: &Workspace,
        compose_file: &Path,
    ) -> Result<std::collections::HashSet<String>, MigrationError> {
        let target = match data_db_target(workspace) {
            Some(t) => t,
            None => {
                return Err(MigrationError(
                    "cannot list applied migrations without a managed database target".to_string(),
                ))
            }
        };
        self._run_with_retries(
            workspace,
            compose_file,
            &target.database,
            &target.user,
            &target.password,
            BOOKKEEPING_DDL,
        )?;
        let result = self._run_with_retries(
            workspace,
            compose_file,
            &target.database,
            &target.user,
            &target.password,
            "SELECT filename FROM public.schema_migrations ORDER BY filename;\n",
        )?;
        if result.returncode != 0 {
            let detail = if !result.stderr.trim().is_empty() {
                result.stderr.trim().to_string()
            } else if !result.stdout.trim().is_empty() {
                result.stdout.trim().to_string()
            } else {
                String::new()
            };
            return Err(MigrationError(format!("cannot list applied migrations: {detail}")));
        }
        Ok(result
            .stdout
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect())
    }

    fn _run_with_retries(
        &self,
        workspace: &Workspace,
        compose_file: &Path,
        database: &str,
        user: &str,
        password: &str,
        stdin: &str,
    ) -> Result<ExecResult, MigrationError> {
        let mut last_error: Option<String> = None;
        for _ in 0..12 {
            match self.docker.exec_psql(
                &workspace.id,
                compose_file,
                database,
                user,
                password,
                stdin,
            ) {
                Ok(result) => {
                    if result.returncode == 0 {
                        return Ok(result);
                    }
                    let detail = if !result.stderr.trim().is_empty() {
                        result.stderr.trim().to_string()
                    } else if !result.stdout.trim().is_empty() {
                        result.stdout.trim().to_string()
                    } else {
                        String::new()
                    };
                    let detail_lower = detail.to_lowercase();
                    if detail_lower.contains("starting up")
                        || detail_lower.contains("refused")
                        || detail_lower.contains("not reachable")
                    {
                        thread::sleep(Duration::from_secs(2));
                        continue;
                    }
                    return Err(MigrationError(detail.to_string()));
                }
                Err(exc) => {
                    last_error = Some(exc.to_string());
                    thread::sleep(Duration::from_secs(2));
                    continue;
                }
            }
        }
        if let Some(e) = last_error {
            Err(MigrationError(e))
        } else {
            Err(MigrationError(
                "PostgreSQL n'est pas devenu joignable pour les migrations".to_string(),
            ))
        }
    }
}

/// Escape a string as a SQL literal.
pub fn sql_literal(value: &str) -> String {
    format!("'{}'", value.replace("'", "''"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sql_literal_escapes_quotes() {
        assert_eq!(sql_literal("O'Brien"), "'O''Brien'");
        assert_eq!(sql_literal("simple"), "'simple'");
    }
}
