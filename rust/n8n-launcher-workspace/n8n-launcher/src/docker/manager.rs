//! Docker manager: thin wrapper around `docker compose`.
//! Port de `src/n8n_launcher/docker/manager.py`.

use crate::core::models::Workspace;
use std::collections::HashMap;
use std::path::Path;
use std::process::Command;

pub fn compose_project_name(workspace_id: &str) -> String {
    format!("n8n-ws-{}", workspace_id)
}

pub fn parse_compose_status(raw: &str) -> HashMap<String, String> {
    let mut services = HashMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(row) = serde_json::from_str::<serde_json::Value>(line) {
            if let (Some(service), Some(state)) = (
                row.get("Service").and_then(|v| v.as_str()),
                row.get("State").and_then(|v| v.as_str()),
            ) {
                services.insert(service.to_string(), state.to_string());
            }
        }
    }
    services
}

pub fn parse_container_labels(raw: &serde_json::Value) -> HashMap<String, String> {
    if let Some(obj) = raw.as_object() {
        return obj
            .iter()
            .map(|(k, v)| (k.clone(), v.to_string().trim_matches('"').to_string()))
            .collect();
    }
    if let Some(text) = raw.as_str() {
        let text = text.trim();
        if text.is_empty() {
            return HashMap::new();
        }
        if text.starts_with('{') {
            if let Ok(decoded) = serde_json::from_str::<serde_json::Value>(text) {
                if let Some(obj) = decoded.as_object() {
                    return obj
                        .iter()
                        .map(|(k, v)| (k.clone(), v.to_string().trim_matches('"').to_string()))
                        .collect();
                }
            }
        }
        let mut labels = HashMap::new();
        for pair in text.split(',') {
            if let Some((key, value)) = pair.split_once('=') {
                let key = key.trim();
                if !key.is_empty() {
                    labels.insert(key.to_string(), value.trim().to_string());
                }
            }
        }
        return labels;
    }
    HashMap::new()
}

pub fn parse_container_states(raw: &str) -> HashMap<String, HashMap<String, String>> {
    let mut projects: HashMap<String, HashMap<String, String>> = HashMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(row) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        let labels = match row.get("Labels") {
            Some(v) => parse_container_labels(v),
            None => HashMap::new(),
        };
        let project = match labels.get("com.docker.compose.project") {
            Some(p) => p.clone(),
            None => continue,
        };
        let service = match labels.get("com.docker.compose.service") {
            Some(s) => s.clone(),
            None => continue,
        };
        let state = row
            .get("State")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        projects
            .entry(project)
            .or_default()
            .insert(service, state);
    }
    projects
}

pub struct ComposeStatus {
    pub raw_output: String,
    pub returncode: i32,
}

pub struct DockerStatus {
    pub available: bool,
    pub message: String,
}

pub struct ExecResult {
    pub returncode: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug)]
pub struct DockerError(pub String);

impl std::fmt::Display for DockerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "DockerError: {}", self.0)
    }
}

impl std::error::Error for DockerError {}

#[derive(Clone, Debug)]
pub struct DockerManager {
    pub command: String,
}

impl DockerManager {
    pub fn new(command: &str) -> Self {
        Self { command: command.to_string() }
    }

    pub fn check_available(&self) -> DockerStatus {
        match Command::new(&self.command).arg("--version").output() {
            Ok(output) if output.status.success() => {
                let message = String::from_utf8_lossy(&output.stdout).to_string();
                DockerStatus { available: true, message }
            }
            _ => DockerStatus {
                available: false,
                message: "Docker daemon not available".to_string(),
            },
        }
    }

    pub fn status(&self, _workspace_id: &str, compose_path: &Path) -> std::io::Result<ComposeStatus> {
        let project = ""; // caller passes project via compose_path dir; simplified
        let _ = project;
        let output = Command::new(&self.command)
            .args(["compose", "-f"])
            .arg(compose_path)
            .args(["ps", "--format", "json"])
            .output()?;
        Ok(ComposeStatus {
            raw_output: String::from_utf8_lossy(&output.stdout).to_string(),
            returncode: output.status.code().unwrap_or(-1),
        })
    }

    /// Valide un fichier Compose en le résolvant (sans démarrer de conteneur).
    pub fn validate_compose(&self, compose_path: &Path) -> std::io::Result<()> {
        let output = Command::new(&self.command)
            .args(["compose", "-f"])
            .arg(compose_path)
            .args(["config", "--quiet"])
            .output()?;
        if !output.status.success() {
            return Err(std::io::Error::other(String::from_utf8_lossy(&output.stderr).to_string()));
        }
        Ok(())
    }

    pub fn up(&self, workspace_id: &str, compose_path: &Path) -> std::io::Result<()> {
        let project = compose_project_name(workspace_id);
        let output = Command::new(&self.command)
            .args(["compose", "-p", &project, "-f"])
            .arg(compose_path)
            .arg("up")
            .arg("-d")
            .output()?;
        if !output.status.success() {
            return Err(std::io::Error::other(String::from_utf8_lossy(&output.stderr).to_string()));
        }
        Ok(())
    }

    pub fn down(&self, workspace_id: &str, compose_path: &str, _remove_orphans: bool) -> std::io::Result<()> {
        let project = compose_project_name(workspace_id);
        let output = Command::new(&self.command)
            .args(["compose", "-p", &project, "-f", compose_path, "down"])
            .output()?;
        if !output.status.success() {
            return Err(std::io::Error::other(String::from_utf8_lossy(&output.stderr).to_string()));
        }
        Ok(())
    }

    pub fn list_project_states(&self) -> std::io::Result<HashMap<String, HashMap<String, String>>> {
        let output = Command::new(&self.command)
            .args(["ps", "--format", "json"])
            .output()?;
        if !output.status.success() {
            return Ok(HashMap::new());
        }
        let raw = String::from_utf8_lossy(&output.stdout).to_string();
        // docker ps --format json emits one object per line
        Ok(parse_container_states(&raw))
    }

    pub fn exec_psql(
        &self,
        workspace_id: &str,
        compose_path: &Path,
        database: &str,
        user: &str,
        _password: &str,
        stdin: &str,
    ) -> std::io::Result<ExecResult> {
        let project = compose_project_name(workspace_id);
        let mut child = Command::new(&self.command)
            .args(["compose", "-p", &project, "-f"])
            .arg(compose_path)
            .args(["exec", "-T", "db", "psql", "-U", user, "-d", database, "-v", "ON_ERROR_STOP=1"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()?;
        if let Some(mut stdin_handle) = child.stdin.take() {
            use std::io::Write;
            let _ = stdin_handle.write_all(stdin.as_bytes());
        }
        let output = child.wait_with_output()?;
        Ok(ExecResult {
            returncode: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        })
    }
}

pub fn resolve_docker_command() -> String {
    #[cfg(target_os = "macos")]
    {
        let dirs = [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/Applications/Docker.app/Contents/Resources/bin",
        ];
        for d in dirs {
            let candidate = format!("{}/docker", d);
            if std::path::Path::new(&candidate).is_file() {
                return candidate;
            }
        }
    }
    if let Ok(path) = std::env::var("PATH") {
        for dir in path.split(':') {
            let candidate = format!("{}/docker", dir);
            if std::path::Path::new(&candidate).is_file() {
                return candidate;
            }
        }
    }
    "docker".to_string()
}

// silence unused import warning for Workspace
#[allow(dead_code)]
fn _use_workspace(_w: &Workspace) {}
