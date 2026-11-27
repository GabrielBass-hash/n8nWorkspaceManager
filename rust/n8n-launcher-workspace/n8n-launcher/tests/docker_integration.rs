//! Integration tests against a real Docker daemon.
//!
//! These tests only run when a Docker daemon answers on the default socket
//! (`/var/run/docker.sock`). The suite auto-skips otherwise so a laptop without
//! Docker never fails CI for a missing daemon, and a CI runner without the
//! socket skips instead of erroring.

use n8n_launcher::core::models::{DbConfig, DbMode, Workspace};
use n8n_launcher::docker::compose;
use n8n_launcher::docker::manager::DockerManager;
use tempfile::tempdir;

fn docker_available() -> bool {
    DockerManager::new("docker").check_available().available
}

fn render_and_validate(dir: &std::path::Path) -> bool {
    let ws = Workspace::new(
        "int-test-1".to_string(),
        "integration".to_string(),
        dir.join("workflows"),
        5678,
        DbConfig::new(DbMode::NONE),
    );
    let compose_path = dir.join("compose.yml");
    compose::write_compose(&ws, &compose_path).expect("write compose");
    DockerManager::new("docker")
        .validate_compose(&compose_path)
        .expect("docker compose config should validate a rendered compose file");
    true
}

#[test]
fn test_docker_daemon_reachable() {
    if !docker_available() {
        eprintln!("docker daemon not reachable: skipping integration test");
        return;
    }
}

#[test]
fn test_rendered_compose_validates() {
    if !docker_available() {
        eprintln!("docker daemon not reachable: skipping compose validation");
        return;
    }
    let dir = tempfile::tempdir().unwrap();
    assert!(render_and_validate(dir.path()));
}

#[test]
fn test_lifecycle_up_down() {
    if cfg!(target_os = "windows") {
        eprintln!("lifecycle test requires a Linux Docker daemon: skipping");
        return;
    }
    if !docker_available() {
        eprintln!("docker daemon not reachable: skipping integration test");
        return;
    }
    let dir = tempdir().expect("tempdir");
    let workflows = dir.path().join("workflows");
    std::fs::create_dir_all(&workflows).expect("workflows dir");
    let ws = n8n_launcher::core::models::Workspace::new(
        "rust-lifecycle".to_string(),
        "rust-lifecycle".to_string(),
        workflows,
        15678,
        n8n_launcher::core::models::DbConfig::new(n8n_launcher::core::models::DbMode::NONE),
    );
    let compose_path = dir.path().join("compose.yml");
    n8n_launcher::docker::compose::write_compose(&ws, &compose_path).expect("write compose");
    let docker = n8n_launcher::docker::manager::DockerManager::new("docker");
    docker.validate_compose(&compose_path).expect("validate");
    docker.up(&ws.id, &compose_path).expect("up");
    let states = docker.list_project_states().unwrap_or_default();
    assert!(states.contains_key(&format!("n8n-ws-{}", ws.id)));
    docker.down(&ws.id, compose_path.to_str().unwrap(), true).expect("down");
}
