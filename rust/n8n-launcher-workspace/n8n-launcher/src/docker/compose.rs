//! Render an isolated Docker Compose project for one workspace.
//! Port de `src/n8n_launcher/docker/compose.py`.

use crate::core::models::{DbMode, DEFAULT_POSTGRES_IMAGE, N8N_METADATA_DB_NAME, N8N_METADATA_DB_PASSWORD, N8N_METADATA_DB_USER, Workspace};
use std::path::{Path, PathBuf};

/// Return the isolated `docker compose` project name for a workspace.
pub fn compose_project_name(workspace: &Workspace) -> String {
    format!("n8n-ws-{}", workspace.id)
}

/// Return the complete Docker Compose YAML for the given workspace.
pub fn render_compose(workspace: &Workspace) -> String {
    let workflows_mount = _compose_path(&workspace.workflows_dir);
    _render(workspace, &workflows_mount, workspace.port, false, None)
}

/// Return the Docker Compose YAML deployed to a remote production server.
///
/// The workflow checkout lives next to the Compose file on the server, so the
/// volume path is relative (`./:/workflows`) and the n8n port binds loopback
/// only: the launcher reaches the instance through SSH, never over the LAN.
/// The project is pinned by a top-level `name:` (`n8n-ws-<id>`) so the
/// server-side `post-receive` hook and `deploy.py` reach the right,
/// isolated project regardless of the checkout directory (`workflow`).
pub fn render_remote_compose(workspace: &Workspace) -> String {
    let name = compose_project_name(workspace);
    _render(workspace, "./", workspace.server.n8n_port, true, Some(&name))
}

/// Render and write the Compose YAML, creating intermediate directories.
pub fn write_compose(workspace: &Workspace, output: &Path) -> std::io::Result<PathBuf> {
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let content = render_compose(workspace);
    std::fs::write(output, content)?;
    Ok(output.to_path_buf())
}

fn _render(
    workspace: &Workspace,
    workflows_mount: &str,
    n8n_port: u16,
    loopback: bool,
    name: Option<&str>,
) -> String {
    let n8n_image = format!("n8nio/n8n:{}", workspace.n8n_version);
    let (db_environment, db_service, db_dependency) = _database_parts(workspace);

    let mut data_volumes = format!("  n8ndata-{}:\n", workspace.id);
    if workspace.db.mode == DbMode::MANAGED {
        data_volumes.push_str(&format!("  pgdata-{}:\n", workspace.id));
    }

    let bind_host = if loopback { "127.0.0.1:" } else { "" };
    let project_line = match name {
        Some(n) => format!("name: {n}\n"),
        None => String::new(),
    };

    let service_block = format!(
        r#"  n8n:
    image: {n8n_image}
    restart: unless-stopped
    ports:
      - "{bind_host}{n8n_port}:5678"
    environment:
{db_environment}      N8N_PORT: "5678"
      N8N_PROTOCOL: http
      N8N_SECURE_COOKIE: "false"
    volumes:
      - n8ndata-{workspace_id}:/home/node/.n8n
      - "{workflows_mount}:/workflows"
{db_dependency}    healthcheck:
      test: ["CMD-SHELL", "wget --spider -q http://127.0.0.1:5678/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 20
"#,
        n8n_image = n8n_image,
        bind_host = bind_host,
        n8n_port = n8n_port,
        db_environment = db_environment,
        db_dependency = db_dependency,
        workspace_id = workspace.id,
        workflows_mount = workflows_mount
    );

    format!(
        "{project_line}services:\n{db_service}{service_block}volumes:\n{data_volumes}"
    )
}

fn _database_parts(workspace: &Workspace) -> (String, String, String) {
    if workspace.db.mode != DbMode::MANAGED {
        return (String::new(), String::new(), String::new());
    }

    let environment = format!(
        r#"      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: postgres
      DB_POSTGRESDB_PORT: "5432"
      DB_POSTGRESDB_DATABASE: {}
      DB_POSTGRESDB_USER: {}
      DB_POSTGRESDB_PASSWORD: {}
"#,
        N8N_METADATA_DB_NAME, N8N_METADATA_DB_USER, N8N_METADATA_DB_PASSWORD
    );

    let postgres_image = workspace
        .postgres_image
        .as_deref()
        .unwrap_or(DEFAULT_POSTGRES_IMAGE);

    let command_block = if workspace.postgres_preload_timescaledb {
        r#"    command: ["postgres", "-c", "shared_preload_libraries=timescaledb"]
"#
    } else {
        ""
    };

    let service = format!(
        r#"  postgres:
    image: {postgres_image}
    restart: unless-stopped
{command_block}    environment:
      POSTGRES_DB: {metadata_db_name}
      POSTGRES_USER: {metadata_db_user}
      POSTGRES_PASSWORD: {metadata_db_password}
    volumes:
      - pgdata-{workspace_id}:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {metadata_db_user} -d {metadata_db_name}"]
      interval: 5s
      timeout: 3s
      retries: 20
"#,
        postgres_image = postgres_image,
        command_block = command_block,
        metadata_db_name = N8N_METADATA_DB_NAME,
        metadata_db_user = N8N_METADATA_DB_USER,
        metadata_db_password = N8N_METADATA_DB_PASSWORD,
        workspace_id = workspace.id
    );

    let dependency =
        "    depends_on:\n      postgres:\n        condition: service_healthy\n".to_string();

    (environment, service, dependency)
}

fn _compose_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/").replace('"', "\\\"").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::{DbConfig, ServerConfig, WorkspaceState};

    fn test_workspace() -> Workspace {
        Workspace::new(
            "ws-abc123".to_string(),
            "Test WS".to_string(),
            std::path::PathBuf::from("/home/user/workflows"),
            5678,
            DbConfig::new(DbMode::NONE),
        )
    }

    #[test]
    fn test_compose_project_name() {
        let ws = test_workspace();
        assert_eq!(compose_project_name(&ws), "n8n-ws-ws-abc123");
    }

    #[test]
    fn test_render_compose_none_mode() {
        let ws = test_workspace();
        let yaml = render_compose(&ws);
        assert!(yaml.contains("n8n-ws-ws-abc123") || !yaml.contains("name:"));
        assert!(yaml.contains("n8nio/n8n:2.40.0"));
        assert!(yaml.contains("- \"5678:5678\""));
        assert!(yaml.contains("n8ndata-ws-abc123:/home/node/.n8n"));
        assert!(!yaml.contains("postgres:"));
        assert!(!yaml.contains("pgdata-"));
    }

    #[test]
    fn test_render_compose_managed_mode() {
        let mut ws = test_workspace();
        ws.db = DbConfig::new(DbMode::MANAGED);
        let yaml = render_compose(&ws);
        assert!(yaml.contains("postgres:"));
        assert!(yaml.contains("pgdata-ws-abc123:"));
        assert!(yaml.contains("DB_TYPE: postgresdb"));
        assert!(yaml.contains("depends_on:"));
    }

    #[test]
    fn test_render_remote_compose_has_name() {
        let mut ws = test_workspace();
        ws.server = ServerConfig {
            enabled: true,
            host: "example.com".to_string(),
            ssh_port: 22,
            user: "root".to_string(),
            key_path: None,
            base_dir: "n8n-launcher/ws-abc123".to_string(),
            n8n_port: 5678,
        };
        let yaml = render_remote_compose(&ws);
        assert!(yaml.contains("name: n8n-ws-ws-abc123"));
        assert!(yaml.contains("- \"127.0.0.1:5678:5678\""));
        assert!(yaml.contains("\"./:/workflows\""));
    }

    #[test]
    fn test_compose_path_escaping() {
        let path = std::path::Path::new(r#"C:\Users\name\project"#);
        let escaped = _compose_path(path);
        assert!(!escaped.contains('\\'));
        assert!(!escaped.contains('"'));
    }

    #[test]
    fn test_managed_mode_has_postgres_healthcheck() {
        let mut ws = test_workspace();
        ws.db = DbConfig::new(DbMode::MANAGED);
        let yaml = render_compose(&ws);
        assert!(yaml.contains("pg_isready"));
    }

    #[test]
    fn test_timescaledb_command_block() {
        let mut ws = test_workspace();
        ws.db = DbConfig::new(DbMode::MANAGED);
        ws.postgres_preload_timescaledb = true;
        let yaml = render_compose(&ws);
        assert!(yaml.contains("shared_preload_libraries=timescaledb"));
    }

    #[test]
    fn test_workspace_defaults_used() {
        let ws = test_workspace();
        assert_eq!(ws.n8n_version, "2.40.0");
        assert!(ws.git.enabled == false);
        assert_eq!(ws.state, WorkspaceState::STOPPED);
    }
}
