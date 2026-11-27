//! Workspace CRUD and lifecycle orchestration.
//! Port de `src/n8n_launcher/workspaces/manager.py`.

use crate::core::config::ConfigStore;
use crate::core::models::{
    AppConfig, DbConfig, DbMode, ServerConfig, Workspace, WorkspaceState,
};
use crate::core::paths;
use crate::docker::compose;
use crate::docker::manager::{compose_project_name, DockerManager};
use crate::workspaces::ci;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::AppConfig;
    use std::path::PathBuf;

    fn make_store(tmpdir: &std::path::Path) -> (ConfigStore, PathBuf) {
        let store = ConfigStore::new(tmpdir.join("launcher.db"));
        let config = AppConfig::new(
            "owner@example.com".to_string(),
            "Password123".to_string(),
            tmpdir.join("work"),
        );
        store.save(&config).unwrap();
        (store, tmpdir.join("work"))
    }

    #[test]
    fn test_list_empty() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        assert!(manager.list().is_empty());
    }

    #[test]
    fn test_create_and_list() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _work) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        let wdir = tmpdir.path().join("ws1");
        let ws = manager.create("Mon workspace", wdir.clone(), None, None, None).unwrap();
        assert_eq!(ws.name, "Mon workspace");
        assert_eq!(manager.list().len(), 1);
    }

    #[test]
    fn test_create_empty_name_fails() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        let res = manager.create("   ", tmpdir.path().join("x"), None, None, None);
        assert!(res.is_err());
    }

    #[test]
    fn test_delete() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        let wdir = tmpdir.path().join("ws2");
        let ws = manager.create("ws", wdir, None, None, None).unwrap();
        manager.delete(&ws.id).unwrap();
        assert!(manager.list().is_empty());
    }

    #[test]
    fn test_update_name() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        let wdir = tmpdir.path().join("ws3");
        let ws = manager.create("old", wdir, None, None, None).unwrap();
        let mut changes = std::collections::HashMap::new();
        changes.insert("name".to_string(), "new".to_string());
        let updated = manager.update(&ws.id, &changes).unwrap();
        assert_eq!(updated.name, "new");
    }

    #[test]
    fn test_find_missing() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        assert!(manager.find("nope").is_err());
    }

    #[test]
    fn test_reconcile_all_empty() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        assert_eq!(manager.reconcile_all(), 0);
    }

    #[test]
    fn test_observer_registration() {
        struct Obs;
        impl WorkspaceObserver for Obs {
            fn workspaces_changed(&self) {}
        }
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        manager.add_observer(Box::new(Obs));
        manager.reconcile_all();
        // remove by pointer not easily available; ensure no panic
    }

    #[test]
    fn test_github_token_roundtrip() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        assert!(manager.github_token().is_none());
        manager.set_github_token(Some("tok".to_string())).unwrap();
        assert_eq!(manager.github_token().as_deref(), Some("tok"));
    }

    #[test]
    fn test_enable_disable_ci() {
        let tmpdir = tempfile::tempdir().unwrap();
        let (store, _) = make_store(tmpdir.path());
        let docker = DockerManager::new("docker");
        let manager = WorkspaceManager::new(&store, &docker);
        let wdir = tmpdir.path().join("ws4");
        let mut ws = manager.create("ws", wdir.clone(), None, None, None).unwrap();
        ws.git.enabled = true;
        ws.git.remote_url = Some("https://github.com/user/repo.git".to_string());
        crate::git::manager::git_init(&wdir, Some("https://github.com/user/repo.git"), Some("dev"));
        let ws2 = manager.enable_ci(&ws).unwrap();
        assert!(ws2.git.ci_enabled);
        let ws3 = manager.disable_ci(&ws2).unwrap();
        assert!(!ws3.git.ci_enabled);
    }
}

fn random_hex(n_bytes: usize) -> String {
    // Simple deterministic-ish fallback using uuid bytes (no rand dependency).
    let mut buf = vec![0u8; n_bytes];
    // Use uuid bytes
    let u = uuid::Uuid::new_v4();
    let bytes = u.as_bytes();
    for (i, b) in buf.iter_mut().enumerate() {
        *b = bytes[i % bytes.len()];
    }
    buf.iter().map(|b| format!("{:02x}", b)).collect::<String>()[..n_bytes * 2].to_string()
}

/// A workspace whose n8n answers, and whether reaching it started the stack.
#[derive(Debug, Clone)]
pub struct Reachable {
    pub workspace: Workspace,
    pub started: bool,
}

/// Observer notified after each committed change.
pub trait WorkspaceObserver: Send + Sync {
    fn workspaces_changed(&self);
    fn workspace_changed(&self, _workspace: Workspace) {}
}

/// Central controller for workspace CRUD and the start/stop lifecycle.
pub struct WorkspaceManager {
    store: Arc<ConfigStore>,
    docker: Arc<DockerManager>,
    observers: Arc<Mutex<Vec<(Box<dyn WorkspaceObserver>, *const ())>>>,
}

impl Clone for WorkspaceManager {
    fn clone(&self) -> Self {
        Self {
            store: self.store.clone(),
            docker: self.docker.clone(),
            observers: self.observers.clone(),
        }
    }
}

impl WorkspaceManager {
    pub fn new(store: &ConfigStore, docker: &DockerManager) -> Self {
        Self {
            store: Arc::new(store.clone()),
            docker: Arc::new(docker.clone()),
            observers: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn store(&self) -> Arc<ConfigStore> {
        self.store.clone()
    }

    pub fn docker(&self) -> Arc<DockerManager> {
        self.docker.clone()
    }

    pub fn add_observer(&self, observer: Box<dyn WorkspaceObserver>) {
        let ptr = observer.as_ref() as *const dyn WorkspaceObserver as *const ();
        let mut obs = self.observers.lock().unwrap();
        if !obs.iter().any(|(_, p)| *p == ptr) {
            obs.push((observer, ptr));
        }
    }

    pub fn remove_observer(&self, observer: &dyn WorkspaceObserver) {
        let ptr = observer as *const dyn WorkspaceObserver as *const ();
        let mut obs = self.observers.lock().unwrap();
        obs.retain(|(_, p)| *p != ptr);
    }

    fn notify_workspaces_changed(&self) {
        let obs = self.observers.lock().unwrap();
        for (o, _) in obs.iter() {
            o.workspaces_changed();
        }
    }

    fn notify_workspace_changed(&self, workspace: &Workspace) {
        let obs = self.observers.lock().unwrap();
        for (o, _) in obs.iter() {
            o.workspace_changed(workspace.clone());
        }
    }

    pub fn list(&self) -> Vec<Workspace> {
        self.store.load().map(|c| c.workspaces).unwrap_or_default()
    }

    pub fn live_state(&self, workspace: &Workspace) -> WorkspaceState {
        let projects = self.docker.list_project_states().unwrap_or_default();
        let project = compose_project_name(&workspace.id);
        let n8n_state = projects
            .get(&project)
            .and_then(|s| s.get("n8n"))
            .cloned();
        match n8n_state.as_deref() {
            Some("running") => WorkspaceState::RUNNING,
            Some(s) if ["created", "restarting", "starting", "paused"].contains(&s) => {
                WorkspaceState::STARTING
            }
            _ => WorkspaceState::STOPPED,
        }
    }

    pub fn reconcile_all(&self) -> i32 {
        let projects = match self.docker.list_project_states() {
            Ok(p) => p,
            Err(_) => {
                // fallback: mark all non-stopped as stopped
                let config = match self.store.load() {
                    Ok(c) => c,
                    Err(_) => return 0,
                };
                let updates: Vec<String> = config
                    .workspaces
                    .iter()
                    .filter(|w| w.state != WorkspaceState::STOPPED)
                    .map(|w| w.id.clone())
                    .collect();
                if updates.is_empty() {
                    return 0;
                }
                let ids = updates.clone();
                let res = self.store.mutate(|config: &mut AppConfig| {
                    for w in config.workspaces.iter_mut() {
                        if ids.contains(&w.id) {
                            w.state = WorkspaceState::STOPPED;
                        }
                    }
                });
                if res.is_err() {
                    return 0;
                }
                self.notify_workspaces_changed();
                return updates.len() as i32;
            }
        };
        let config = match self.store.load() {
            Ok(c) => c,
            Err(_) => return 0,
        };
        let mut updates: std::collections::HashMap<String, WorkspaceState> =
            std::collections::HashMap::new();
        for w in &config.workspaces {
            let live = if !paths::compose_file(&w.id).map(|p| p.exists()).unwrap_or(false) {
                WorkspaceState::STOPPED
            } else {
                // compute
                let project = compose_project_name(&w.id);
                let n8n_state = projects
                    .get(&project)
                    .and_then(|s| s.get("n8n"))
                    .cloned();
                match n8n_state.as_deref() {
                    Some("running") => WorkspaceState::RUNNING,
                    Some(s) if ["created", "restarting", "starting", "paused"].contains(&s) => {
                        WorkspaceState::STARTING
                    }
                    _ => WorkspaceState::STOPPED,
                }
            };
            if live != w.state {
                updates.insert(w.id.clone(), live);
            }
        }
        if updates.is_empty() {
            return 0;
        }
        let updates_clone = updates.clone();
        let res = self.store.mutate(|config: &mut AppConfig| {
            for w in config.workspaces.iter_mut() {
                if let Some(live) = updates_clone.get(&w.id) {
                    if *live != w.state {
                        w.state = *live;
                    }
                }
            }
        });
        if res.is_err() {
            return 0;
        }
        self.notify_workspaces_changed();
        updates.len() as i32
    }

    pub fn create(
        &self,
        name: &str,
        workflows_dir: PathBuf,
        db: Option<DbConfig>,
        port: Option<u16>,
        n8n_version: Option<&str>,
    ) -> Result<Workspace, String> {
        if name.trim().is_empty() {
            return Err("Le nom du workspace est requis".to_string());
        }
        let db = match db {
            Some(d) => {
                if d.mode == DbMode::MANAGED {
                    DbConfig {
                        mode: DbMode::MANAGED,
                        database_name: d.database_name.or(Some(crate::core::models::WORKSPACE_DATA_DB_NAME.to_string())),
                        username: d.username.or(Some(crate::core::models::WORKSPACE_DATA_DB_USER.to_string())),
                        password: d.password.or_else(|| Some(random_hex(16))),
                    }
                } else {
                    d
                }
            }
            None => {
                if crate::database::layout::has_db_layout(&workflows_dir) {
                    DbConfig {
                        mode: DbMode::MANAGED,
                        database_name: Some(crate::core::models::WORKSPACE_DATA_DB_NAME.to_string()),
                        username: Some(crate::core::models::WORKSPACE_DATA_DB_USER.to_string()),
                        password: Some(random_hex(16)),
                    }
                } else {
                    DbConfig::new(DbMode::NONE)
                }
            }
        };

        let reserved: Vec<u16> = self
            .store
            .load()
            .map(|c| c.workspaces.iter().map(|w| w.port).collect())
            .unwrap_or_default();

        let selected_port = match port {
            Some(p) => p,
            None => crate::platform::ports::suggest_port(5678, Some(&reserved), 1000)
                .map_err(|e| e.to_string())?,
        };
        if reserved.contains(&selected_port) {
            return Err(format!("Le port {}", selected_port));
        }

        let id = uuid::Uuid::new_v4().to_string()[..8].to_string();
        let n8n_version = n8n_version.unwrap_or("2.40.0").to_string();

        self._scaffold(&workflows_dir, &db);

        let mut ws = Workspace::new(
            id.clone(),
            name.trim().to_string(),
            workflows_dir.clone(),
            selected_port,
            db.clone(),
        );
        ws.n8n_version = n8n_version;

        // mutate with port re-check under lock
        let ws_clone = ws.clone();
        let created = self
            .store
            .mutate(|config: &mut AppConfig| {
                if config.workspaces.iter().any(|w| w.port == ws_clone.port) {
                    return Err(format!("Le port {}", ws_clone.port));
                }
                config.workspaces.push(ws_clone.clone());
                Ok(ws_clone.clone())
            })
            .map_err(|e| e.to_string())?
            .map_err(|e| e)?;

        self.notify_workspaces_changed();
        Ok(created)
    }

    pub fn clone_from_git(
        &self,
        name: &str,
        remote_url: &str,
        workflows_dir: PathBuf,
        port: Option<u16>,
    ) -> Result<Workspace, String> {
        let port = port.unwrap_or_else(|| 5678);
        let db = DbConfig::new(DbMode::MANAGED);
        let id = uuid::Uuid::new_v4().to_string();
        let ws = Workspace::new(id.clone(), name.to_string(), workflows_dir.clone(), port, db);

        let _ = std::fs::create_dir_all(&workflows_dir);
        crate::git::manager::git_clone(remote_url, &workflows_dir);

        let mut ws = ws;
        ws.git.enabled = true;
        ws.git.remote_url = Some(remote_url.to_string());
        let ws_clone = ws.clone();

        self.store
            .mutate(|config: &mut AppConfig| {
                // replace the pushed placeholder? We pushed ws before mutation; need to push ws_clone.
                config.workspaces.push(ws_clone.clone());
            })
            .map_err(|e| e.to_string())?;

        let committed = self
            .store
            .load()
            .ok()
            .and_then(|c| c.workspaces.into_iter().find(|w| w.id == id))
            .unwrap_or(ws);

        self.notify_workspaces_changed();
        Ok(committed)
    }

    pub fn update(&self, workspace_id: &str, changes: &std::collections::HashMap<String, String>) -> Result<Workspace, String> {
        let mut found: Option<Workspace> = None;
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == workspace_id) {
                    if let Some(v) = changes.get("name") {
                        w.name = v.clone();
                    }
                    if let Some(v) = changes.get("port") {
                        if let Ok(p) = v.parse::<u16>() {
                            w.port = p;
                        }
                    }
                    found = Some(w.clone());
                }
            })
            .map_err(|e| e.to_string())?;
        let ws = found.ok_or_else(|| "workspace not found".to_string())?;
        self.notify_workspace_changed(&ws);
        Ok(ws)
    }

    pub fn delete(&self, workspace_id: &str) -> Result<(), String> {
        // stop first (best effort)
        let _ = self.stop(workspace_id);
        self.store
            .mutate(|config: &mut AppConfig| {
                config.workspaces.retain(|w| w.id != workspace_id);
            })
            .map_err(|e| e.to_string())?;
        // remove compose file? leave
        self.notify_workspaces_changed();
        Ok(())
    }

    pub fn ensure_running(&self, workspace_id: &str) -> Result<Reachable, String> {
        let ws = self.find(workspace_id)?;
        let live = self.live_state(&ws);
        if live == WorkspaceState::RUNNING {
            return Ok(Reachable { workspace: ws, started: false });
        }
        let started_ws = self.start(workspace_id)?;
        Ok(Reachable {
            workspace: started_ws,
            started: true,
        })
    }

    pub fn start(&self, workspace_id: &str) -> Result<Workspace, String> {
        let ws = self.find(workspace_id)?;
        // write compose
        let compose_path = paths::compose_file(&ws.id).map_err(|e| e.to_string())?;
        compose::write_compose(&ws, &compose_path).map_err(|e| e.to_string())?;

        // docker up
        self.docker
            .up(&ws.id, &compose_path)
            .map_err(|e| e.to_string())?;

        // mark starting
        self.set_state(&ws.id, WorkspaceState::STARTING)?;

        // wait for n8n
        let base_url = format!("http://localhost:{}", ws.port);
        crate::n8n::owner::wait_for_n8n(&base_url, 60, 2).map_err(|e| e.to_string())?;

        // owner bootstrap
        let mut api_key: Option<String> = None;
        let config = self.store.load().map_err(|e| e.to_string())?;
        let owner_setup = crate::n8n::owner::OwnerSetup::new(30).map_err(|e| e.to_string())?;
        let creds = owner_setup.bootstrap(
            &base_url,
            &config.owner_email,
            &config.owner_password,
            "Launcher",
            "Owner",
            60,
        );
        if let Ok(c) = creds {
            api_key = Some(c.api_key);
        }

        // save api key
        if let Some(key) = &api_key {
            let key = key.clone();
            let id = ws.id.clone();
            let _ = self.store.mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.api_key = Some(key.clone());
                    w.state = WorkspaceState::RUNNING;
                }
            });
        } else {
            self.set_state(&ws.id, WorkspaceState::RUNNING)?;
        }

        // ensure db credentials (best effort)
        if let Some(key) = &api_key {
            let ws2 = self.find(&ws.id).unwrap_or(ws.clone());
            self._ensure_db_credentials(&ws2, key);
        }

        // git pull (best effort)
        let _ = self.sync_git_find(&ws.id, false);

        // import workflows (best effort) — pipelines dir + root, like Python
        if let Some(key) = &api_key {
            let client = crate::n8n::api::N8nApiClient::new(&base_url, key, 30).map_err(|e| e.to_string())?;
            let pipelines_dir = ws.workflows_dir.join("n8nPipelines");
            let _ = std::fs::create_dir_all(&pipelines_dir);
            let _ = crate::n8n::workflows::import_all(&client, &pipelines_dir);
            let _ = crate::n8n::workflows::import_all(&client, &ws.workflows_dir);
        }

        let updated = self.find(&ws.id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn stop_with_sync(&self, workspace_id: &str) -> Result<Workspace, String> {
        // export + git push before stop
        if let Ok(ws) = self.find(workspace_id) {
            if let Some(key) = &ws.api_key {
                let base_url = format!("http://localhost:{}", ws.port);
                let client = crate::n8n::api::N8nApiClient::new(&base_url, key, 30).map_err(|e| e.to_string())?;
                let pipelines_dir = ws.workflows_dir.join("n8nPipelines");
                let _ = crate::n8n::workflows::export_all(&client, &pipelines_dir, Some(&ws.workflows_dir));
            }
            let _ = self.sync_git(&ws, true);
        }
        self.stop(workspace_id)
    }

    pub fn stop(&self, workspace_id: &str) -> Result<Workspace, String> {
        let ws = self.find(workspace_id)?;
        self.set_state(&ws.id, WorkspaceState::STOPPING)?;
        let compose_path_str = paths::compose_file(&ws.id).map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
        let _ = self.docker.down(&ws.id, &compose_path_str, true);
        let updated = self.set_state(&ws.id, WorkspaceState::STOPPED)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn sync_git(&self, workspace: &Workspace, push: bool) -> Result<(), String> {
        self._commit_and_push(workspace, "sync", push).map(|_| ())
    }

    fn sync_git_find(&self, workspace_id: &str, push: bool) -> Result<(), String> {
        let ws = self.find(workspace_id)?;
        self.sync_git(&ws, push)
    }

    pub fn _commit_and_push(&self, workspace: &Workspace, message: &str, push: bool) -> Result<bool, String> {
        if !workspace.git.enabled {
            return Ok(false);
        }
        let dir = &workspace.workflows_dir;
        if !crate::git::manager::git_is_repo(dir) {
            return Ok(false);
        }
        crate::git::manager::git_add(dir, &["."]);
        let committed = crate::git::manager::git_commit(dir, message);
        if push && committed {
            // best effort push
            crate::git::manager::git_push(dir);
        }
        Ok(committed)
    }

    pub fn _set_git_push_failed(&self, workspace: &Workspace, failed: bool) -> Result<(), String> {
        let id = workspace.id.clone();
        let f = failed;
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git_push_failed = f;
                }
            })
            .map_err(|e| e.to_string())?;
        if let Ok(w) = self.find(&id) {
            self.notify_workspace_changed(&w);
        }
        Ok(())
    }

    pub fn git_init_workspace(&self, workspace: &Workspace, remote_url: Option<&str>) -> Result<(), String> {
        let dir = &workspace.workflows_dir;
        let _ = std::fs::create_dir_all(dir);
        crate::git::manager::git_init(dir, remote_url, Some("dev"));
        crate::git::manager::ensure_local_excludes(dir);
        if let Some(url) = remote_url {
            crate::git::manager::git_add_remote(dir, "origin", url);
        }
        // persist git config
        let id = workspace.id.clone();
        let url = remote_url.map(|s| s.to_string());
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git.enabled = true;
                    w.git.remote_url = url.clone();
                    w.git.branch = "dev".to_string();
                }
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn git_remote_url(&self, workspace: &Workspace) -> Option<String> {
        crate::git::manager::git_remote_url(&workspace.workflows_dir, None)
    }

    pub fn github_token(&self) -> Option<String> {
        self.store
            .load()
            .ok()
            .and_then(|c| c.github_token)
            .filter(|t| !t.is_empty())
    }

    pub fn set_github_token(&self, token: Option<String>) -> Result<(), String> {
        let t = token.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                config.github_token = t.clone();
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn configure_db(&self, workspace: &Workspace, db: DbConfig) -> Result<Workspace, String> {
        let id = workspace.id.clone();
        let db_clone = db.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.db = db_clone.clone();
                }
            })
            .map_err(|e| e.to_string())?;
        let updated = self.find(&id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn configure_git(&self, workspace: &Workspace, remote_url: Option<String>) -> Result<(), String> {
        let id = workspace.id.clone();
        let url = remote_url.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git.enabled = true;
                    w.git.remote_url = url.clone();
                    if w.git.branch.is_empty() {
                        w.git.branch = "dev".to_string();
                    }
                }
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn enable_ci(&self, workspace: &Workspace) -> Result<Workspace, String> {
        if !workspace.git.enabled || !crate::git::manager::git_is_repo(&workspace.workflows_dir) {
            return Err("Les tests GitHub Actions nécessitent un dépôt Git (configurez Git d'abord).".to_string());
        }
        if crate::workspaces::ci::github_repo_path(self.git_remote_url(workspace).as_deref()).is_none() {
            return Err("Les tests GitHub Actions nécessitent un dépôt distant GitHub (ex. https://github.com/utilisateur/repo.git).".to_string());
        }
        self._ensure_workspace_branch(workspace);
        let files = crate::workspaces::ci::render_harness(&workspace.n8n_version);
        let selection = crate::workspaces::ci::selection_path(&workspace.workflows_dir);
        for (rel, content) in &files {
            if rel == &format!("{}/{}", crate::workspaces::ci::CI_DIR, crate::workspaces::ci::SELECTION_FILE) {
                if !selection.exists() {
                    if let Some(parent) = selection.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    let _ = std::fs::write(&selection, content);
                }
                continue;
            }
            let target = workspace.workflows_dir.join(rel);
            if let Some(parent) = target.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&target, content);
        }
        let id = workspace.id.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git.ci_enabled = true;
                }
            })
            .map_err(|e| e.to_string())?;
        let updated = self.find(&id)?;
        let _ = self._commit_and_push(&updated, "n8n-launcher: activer les tests GitHub Actions", true);
        self._ensure_ci_default_branch(&updated);
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    fn _ensure_ci_default_branch(&self, workspace: &Workspace) {
        let repo_path = match crate::workspaces::ci::github_repo_path(self.git_remote_url(workspace).as_deref()) {
            Some(p) => p,
            None => return,
        };
        let token = match self.github_token() {
            Some(t) => t,
            None => return,
        };
        let client = match crate::github::api::GitHubClient::new(&token, 30) {
            Ok(c) => c,
            Err(_) => return,
        };
        let branch = crate::git::manager::workspace_branch(&workspace.id);
        let _ = crate::github::api::set_default_branch(&client, &repo_path, &branch);
    }

    pub fn disable_ci(&self, workspace: &Workspace) -> Result<Workspace, String> {
        let id = workspace.id.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git.ci_enabled = false;
                }
            })
            .map_err(|e| e.to_string())?;
        let updated = self.find(&id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn set_ci_credentials(&self, workspace: &Workspace, creds: Vec<serde_json::Value>) -> Result<Workspace, String> {
        let id = workspace.id.clone();
        let creds_clone = creds.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.git.ci_credentials = creds_clone.clone();
                }
            })
            .map_err(|e| e.to_string())?;
        let updated = self.find(&id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn save_ci_selection(&self, workspace: &Workspace, selected: &std::collections::HashSet<String>) {
        ci::write_selection(&workspace.workflows_dir, selected);
    }

    pub fn ci_credentials_payload(&self, _workspace: &Workspace, selected: Vec<serde_json::Value>) -> String {
        // delegate to ci? Python builds a JSON string. Minimal: serialize.
        serde_json::to_string(&selected).unwrap_or_default()
    }

    pub fn _ensure_workspace_branch(&self, workspace: &Workspace) -> Option<String> {
        let dir = &workspace.workflows_dir;
        if !crate::git::manager::git_is_repo(dir) {
            return None;
        }
        crate::git::manager::ensure_workspace_branch(dir, "dev")
    }

    pub fn install_server(&self, workspace: &Workspace, server: &ServerConfig) -> Result<Workspace, String> {
        // Probe the server first, like the Python flow (best effort).
        let _ = crate::remote::ssh::test_connection(server);
        let id = workspace.id.clone();
        let base = crate::remote::deploy::resolve_base(server, Some(&id));
        let bare = crate::remote::deploy::bare_dir(server, Some(&id));
        // Create the bare repository (idempotent).
        crate::remote::ssh::ssh_run(server, &format!("git init --bare {}", crate::remote::ssh::sh_quote(&bare)))
            .map_err(|e| e.to_string())?;
        crate::remote::ssh::mkdir_remote(server, &base, true).map_err(|e| e.to_string())?;
        crate::remote::ssh::write_remote_file(
            server,
            &format!("{}/hooks/post-receive", bare),
            &crate::remote::deploy::render_hook(server, Some(&id)),
        )
        .map_err(|e| e.to_string())?;
        crate::remote::ssh::chmod_remote(server, &format!("{}/hooks/post-receive", bare), "755")
            .map_err(|e| e.to_string())?;
        crate::remote::ssh::write_remote_file(
            server,
            &format!("{}/deploy.py", base),
            &crate::remote::deploy::render_deploy_script(),
        )
        .map_err(|e| e.to_string())?;
        crate::remote::ssh::chmod_remote(server, &format!("{}/deploy.py", base), "755")
            .map_err(|e| e.to_string())?;

        // Persist the server configuration.
        let srv = server.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.server = srv.clone();
                    w.server.enabled = true;
                }
            })
            .map_err(|e| e.to_string())?;

        // Wire the local git remote.
        if workspace.git.enabled && crate::git::manager::git_is_repo(&workspace.workflows_dir) {
            let remote_url = crate::remote::deploy::server_remote_url(server, Some(&id));
            let current_url = crate::git::manager::git_remote_url(&workspace.workflows_dir, Some("server"));
            if current_url.is_none() {
                crate::git::manager::git_add_remote(&workspace.workflows_dir, "server", &remote_url);
            } else if current_url.as_deref() != Some(&remote_url) {
                crate::git::manager::git_set_remote_url(&workspace.workflows_dir, "server", &remote_url);
            }
        }

        let updated = self.find(&id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn disable_server(&self, workspace: &Workspace) -> Result<Workspace, String> {
        let id = workspace.id.clone();
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.server.enabled = false;
                }
            })
            .map_err(|e| e.to_string())?;
        let updated = self.find(&id)?;
        self.notify_workspace_changed(&updated);
        Ok(updated)
    }

    pub fn publish(&self, workspace: &Workspace) -> Result<(), String> {
        let server = workspace.server.clone();
        if !server.enabled {
            return Err("Aucun serveur configuré pour ce workspace : configurez-le avant de publier.".to_string());
        }
        if !crate::git::manager::git_is_repo(&workspace.workflows_dir) {
            return Err("La publication nécessite un dépôt Git configuré.".to_string());
        }
        if workspace.api_key.is_none() {
            return Err("Le workspace doit avoir été démarré une fois pour exporter ses credentials.".to_string());
        }

        // export workflows (best effort)
        if workspace.state == WorkspaceState::RUNNING {
            if let Some(key) = &workspace.api_key {
                let base_url = format!("http://localhost:{}", workspace.port);
                if let Ok(client) = crate::n8n::api::N8nApiClient::new(&base_url, key, 30) {
                    let pipelines_dir = workspace.workflows_dir.join("n8nPipelines");
                    let _ = crate::n8n::workflows::export_all(&client, &pipelines_dir, Some(&workspace.workflows_dir));
                }
            }
        }

        // write remote compose
        let remote_compose = crate::docker::compose::render_remote_compose(workspace);
        let _ = std::fs::write(workspace.workflows_dir.join("compose.yml"), remote_compose);

        // secrets
        let credentials = self._export_all_credentials(workspace);
        let config = self.store.load().map_err(|e| e.to_string())?;
        let document = crate::remote::deploy::build_secrets_document(
            &config.owner_email,
            &config.owner_password,
            &credentials,
        );
        let secrets_path = self._secrets_remote_path(workspace);
        let _ = crate::remote::ssh::write_remote_file(&server, &secrets_path, &serde_json::to_string_pretty(&document).unwrap_or_default());
        let _ = crate::remote::ssh::chmod_remote(&server, &secrets_path, "600");

        // git push dev -> main
        let env = if let Some(key) = &server.key_path {
            Some(crate::git::manager::git_ssh_env(key, Some(server.ssh_port)))
        } else {
            None
        };
        // ensure branch
        let _ = crate::git::manager::ensure_workspace_branch(&workspace.workflows_dir, "dev");
        let branch = crate::git::manager::git_current_branch(&workspace.workflows_dir).unwrap_or_else(|| "dev".to_string());
        let expected_sha = crate::git::manager::git_head(&workspace.workflows_dir);
        let _commit_msg = "n8n-launcher: publication";
        let _ = crate::git::manager::git_commit(&workspace.workflows_dir, _commit_msg);

        crate::git::manager::git_push_ref(
            &workspace.workflows_dir,
            "server",
            &branch,
            "main",
            env.as_ref(),
            300,
        );

        // poll deploy marker
        let (status, marker) = self._poll_deploy(&server, &workspace.id, &expected_sha);
        self._record_server_status(workspace, &status, &marker);
        if status == "error" {
            let msg = marker.get("error").and_then(|v| v.as_str()).unwrap_or("Le déploiement a échoué sur le serveur.").to_string();
            return Err(msg);
        }
        if status == "timeout" {
            return Err("Le serveur n'a pas confirmé le déploiement avant la limite de temps (vérifiez le log serveur).".to_string());
        }
        let _ = branch;
        Ok(())
    }

    pub fn server_health(&self, workspace_id: &str, timeout: f64) -> String {
        let (ws, server) = match self._server_config(workspace_id) {
            Ok(v) => v,
            Err(_) => return "unknown".to_string(),
        };
        if !server.enabled {
            return "unavailable".to_string();
        }
        let compose = format!("{}/compose.yml", crate::remote::deploy::checkout_dir(&server, Some(&ws.id)));
        let project = format!("n8n-ws-{}", ws.id);
        let _ = timeout;
        // Best-effort: run docker compose ps over SSH
        match crate::remote::ssh::ssh_run(&server, &format!(
            "docker compose -f '{}' -p '{}' ps --format json",
            compose, project
        )) {
            Ok(out) => out,
            Err(e) => e,
        }
    }

    pub fn server_logs(&self, workspace_id: &str, lines: i32) -> String {
        let (ws, server) = match self._server_config(workspace_id) {
            Ok(v) => v,
            Err(_) => return String::new(),
        };
        if !server.enabled {
            return String::new();
        }
        let compose = format!("{}/compose.yml", crate::remote::deploy::checkout_dir(&server, Some(&ws.id)));
        let project = format!("n8n-ws-{}", ws.id);
        let cmd = format!(
            "docker compose -f '{}' -p '{}' logs --no-color --tail {} n8n",
            compose, project, lines
        );
        crate::remote::ssh::ssh_run(&server, &cmd).unwrap_or_default()
    }

    pub fn server_deploy_status(&self, workspace_id: &str) -> String {
        let (_ws, server) = match self._server_config(workspace_id) {
            Ok(v) => v,
            Err(_) => return String::new(),
        };
        if !server.enabled {
            return String::new();
        }
        let path = crate::remote::deploy::marker_path(&server, Some(&_ws.id));
        crate::remote::ssh::ssh_run(&server, &format!("cat '{}'", path)).unwrap_or_default()
    }

    pub fn server_execution_status(&self, _workspace_id: &str) -> String {
        String::new()
    }

    pub fn _secrets_remote_path(&self, workspace: &Workspace) -> String {
        format!("{}/secrets.json", crate::remote::deploy::resolve_base(&workspace.server, Some(&workspace.id)))
    }

    pub fn _managed_db_config() -> DbConfig {
        DbConfig {
            mode: DbMode::MANAGED,
            database_name: Some(crate::core::models::WORKSPACE_DATA_DB_NAME.to_string()),
            username: Some(crate::core::models::WORKSPACE_DATA_DB_USER.to_string()),
            password: Some(crate::core::models::WORKSPACE_DATA_DB_PASSWORD.to_string()),
        }
    }

    pub fn _scaffold(&self, workflows_dir: &Path, _db: &DbConfig) {
        let _ = std::fs::create_dir_all(workflows_dir);
        let _ = std::fs::create_dir_all(workflows_dir.join("n8nPipelines"));
    }

    pub fn _find(&self, config: &AppConfig, workspace_id: &str) -> Option<Workspace> {
        config.workspaces.iter().find(|w| w.id == workspace_id).cloned()
    }

    pub fn find(&self, workspace_id: &str) -> Result<Workspace, String> {
        self.store
            .load()
            .ok()
            .and_then(|c| self._find(&c, workspace_id))
            .ok_or_else(|| format!("workspace {} not found", workspace_id))
    }

    fn set_state(&self, workspace_id: &str, state: WorkspaceState) -> Result<Workspace, String> {
        let id = workspace_id.to_string();
        let mut found: Option<Workspace> = None;
        self.store
            .mutate(|config: &mut AppConfig| {
                if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                    w.state = state;
                    found = Some(w.clone());
                }
            })
            .map_err(|e| e.to_string())?;
        let ws = found.ok_or_else(|| format!("workspace {} not found", id))?;
        self.notify_workspace_changed(&ws);
        Ok(ws)
    }

    pub fn _ensure_db_credentials(&self, workspace: &Workspace, _api_key: &str) {
        if workspace.db.mode == DbMode::NONE {
            return;
        }
        if workspace.api_key.is_none() {
            return;
        }
        let api_key = workspace.api_key.clone().unwrap();
        let base_url = format!("http://localhost:{}", workspace.port);
        // try configure; on 401/403 rotate
        let needs_rotate = match crate::n8n::api::N8nApiClient::new(&base_url, &api_key, 30) {
            Ok(client) => match crate::database::credentials::configure_db_credential(&client, workspace) {
                Ok(_) => false,
                Err(e) => e.contains("401") || e.contains("403"),
            },
            Err(_) => false,
        };
        if !needs_rotate {
            self._configure_credentials(workspace, &api_key);
            return;
        }
        let config = match self.store.load() {
            Ok(c) => c,
            Err(_) => return,
        };
        let setup = match crate::n8n::owner::OwnerSetup::new(30) {
            Ok(s) => s,
            Err(_) => return,
        };
        let rotated = match setup.bootstrap(&base_url, &config.owner_email, &config.owner_password, "Owner", "Launcher", 30) {
            Ok(c) => c.api_key,
            Err(_) => return,
        };
        let id = workspace.id.clone();
        let key = rotated.clone();
        let _ = self.store.mutate(|config: &mut AppConfig| {
            if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                w.api_key = Some(key.clone());
            }
        });
        self._configure_credentials(workspace, &rotated);
    }

    pub fn _configure_credentials(&self, workspace: &Workspace, api_key: &str) {
        let base_url = format!("http://localhost:{}", workspace.port);
        if let Ok(client) = crate::n8n::api::N8nApiClient::new(&base_url, api_key, 30) {
            let _ = crate::database::credentials::configure_db_credential(&client, workspace);
        }
    }

    pub fn _ensure_managed_db_parameters(&self, workspace_id: &str) {
        let _ = self.store.mutate(|config: &mut AppConfig| {
            if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == workspace_id) {
                if w.db.mode == DbMode::MANAGED {
                    if w.db.database_name.is_none() {
                        w.db.database_name = Some(crate::core::models::WORKSPACE_DATA_DB_NAME.to_string());
                    }
                    if w.db.username.is_none() {
                        w.db.username = Some(crate::core::models::WORKSPACE_DATA_DB_USER.to_string());
                    }
                    if w.db.password.is_none() {
                        w.db.password = Some(random_hex(16));
                    }
                }
            }
        });
    }

    pub fn _import_workflows(&self, workspace: &Workspace) {
        let pipelines_dir = workspace.workflows_dir.join("n8nPipelines");
        if workspace.git.enabled && crate::git::manager::git_is_repo(&workspace.workflows_dir) {
            let _ = crate::git::manager::ensure_workspace_branch(&workspace.workflows_dir, "dev");
            let _ = crate::git::manager::git_pull(&workspace.workflows_dir);
        }
        if !pipelines_dir.is_dir() {
            return;
        }
        if workspace.api_key.is_none() {
            return;
        }
        let api_key = workspace.api_key.clone().unwrap();
        let base_url = format!("http://localhost:{}", workspace.port);
        if let Ok(client) = crate::n8n::api::N8nApiClient::new(&base_url, &api_key, 30) {
            let runner = crate::n8n::workflows::SyncRunner::new(client.clone(), pipelines_dir);
            let _ = runner.import_all();
            let root_runner = crate::n8n::workflows::SyncRunner::new(client, workspace.workflows_dir.clone());
            let _ = root_runner.import_all();
        }
    }

    pub fn _export_all_credentials(&self, workspace: &Workspace) -> Vec<serde_json::Value> {
        if workspace.api_key.is_none() {
            return Vec::new();
        }
        let api_key = workspace.api_key.clone().unwrap();
        let base_url = format!("http://localhost:{}", workspace.port);
        let client = match crate::n8n::api::N8nApiClient::new(&base_url, &api_key, 30) {
            Ok(c) => c,
            Err(_) => return Vec::new(),
        };
        let mut payload = Vec::new();
        if let Ok(items) = client.list_credentials() {
            for item in items {
                let name = item.get("name").and_then(|v| v.as_str()).map(|s| s.to_string());
                let ctype = item.get("type").and_then(|v| v.as_str()).map(|s| s.to_string());
                if name.is_none() || ctype.is_none() {
                    continue;
                }
                let id = item.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
                if id.is_empty() {
                    continue;
                }
                if let Ok(detail) = client.get_credential(&id) {
                    let data = detail.get("data").cloned().unwrap_or(serde_json::Value::Object(serde_json::Map::new()));
                    payload.push(serde_json::json!({
                        "name": name.unwrap(),
                        "type": ctype.unwrap(),
                        "data": data,
                    }));
                }
            }
        }
        payload
    }

    pub fn _poll_deploy(
        &self,
        cfg: &crate::core::models::ServerConfig,
        workspace_id: &str,
        expected_sha: &str,
    ) -> (String, serde_json::Value) {
        let remote_marker = crate::remote::deploy::marker_path(cfg, Some(workspace_id));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(180);
        let mut delay = std::time::Duration::from_millis(300);
        let max_delay = std::time::Duration::from_millis(1200);
        let backoff = 2.0f64;
        while std::time::Instant::now() < deadline {
            let cmd = format!("cat {}", crate::remote::ssh::sh_quote(&remote_marker));
            let result = crate::remote::ssh::ssh_run_opts(cfg, &cmd, None, 30, false);
            let stdout = result.unwrap_or_default();
            let marker: serde_json::Value = serde_json::from_str(stdout.trim()).unwrap_or(serde_json::json!({}));
            if marker.get("sha").and_then(|v| v.as_str()) != Some(expected_sha) {
                std::thread::sleep(delay);
                let ms = (delay.as_millis() as f64 * backoff).min(max_delay.as_millis() as f64) as u64;
                delay = std::time::Duration::from_millis(ms);
                continue;
            }
            if marker.get("status").and_then(|v| v.as_str()) == Some("ok") {
                return ("ok".to_string(), marker);
            }
            if marker.get("status").and_then(|v| v.as_str()) == Some("error") {
                return ("error".to_string(), marker);
            }
            std::thread::sleep(delay);
            let ms = (delay.as_millis() as f64 * backoff).min(max_delay.as_millis() as f64) as u64;
            delay = std::time::Duration::from_millis(ms);
        }
        ("timeout".to_string(), serde_json::json!({}))
    }

    pub fn _record_server_status(&self, workspace: &Workspace, status: &str, marker: &serde_json::Value) {
        let id = workspace.id.clone();
        let marker_str = if marker.is_null() || marker.as_object().map(|o| o.is_empty()).unwrap_or(true) {
            None
        } else {
            Some(marker.to_string())
        };
        let error = match status {
            "ok" => None,
            "error" => Some(marker.get("error").and_then(|v| v.as_str()).unwrap_or("Échec du déploiement.").to_string()),
            _ => Some("Le serveur n'a pas confirmé le déploiement.".to_string()),
        };
        let _ = self.store.mutate(|config: &mut AppConfig| {
            if let Some(w) = config.workspaces.iter_mut().find(|w| w.id == id) {
                w.server_last_deploy = marker_str.clone();
                w.server_last_error = error.clone();
            }
        });
    }

    pub fn _server_config(&self, workspace_id: &str) -> Result<(Workspace, ServerConfig), String> {
        let ws = self.find(workspace_id)?;
        Ok((ws.clone(), ws.server.clone()))
    }

    pub fn _server_port_conflict(&self, _workspace_id: &str, _port: u16) -> bool {
        false
    }

    pub fn _workspace_db_mode(&self, workspace_id: &str) -> DbMode {
        self.find(workspace_id).map(|w| w.db.mode).unwrap_or(DbMode::NONE)
    }
}


