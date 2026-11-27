//! SyncRunner: import/export workflows.
//! Port de `src/n8n_launcher/n8n/workflows.rs`.

use crate::n8n::api::N8nApiClient;
use crate::n8n::exports::{create_payload, export_filename, EXPORT_NAME_RE};
use serde_json::Value;
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;

/// Contrôle la façon dont la synchronisation traite les workflows distants et locaux.
#[derive(Debug, Clone, Copy)]
pub struct SyncPolicy {
    /// Quand vrai, on ne récupère que depuis le distant (pas d'export local → distant).
    pub pull_remote_only: bool,
    /// Quand vrai, on supprime les orphelins locaux pendant l'export.
    pub delete_orphans: bool,
}

impl Default for SyncPolicy {
    fn default() -> Self {
        Self {
            pull_remote_only: true,
            delete_orphans: false,
        }
    }
}

/// Comptes des workflows tirés, poussés et ignorés par une passe de synchronisation.
#[derive(Debug, Clone, Copy, Default)]
pub struct SyncReport {
    pub pulled: u32,
    pub pushed: u32,
    pub skipped: u32,
}

/// Synchronisation bidirectionnelle optionnelle et périodique avec un dossier de workspace.
pub struct SyncRunner {
    api: N8nApiClient,
    workflows_dir: PathBuf,
    _policy: SyncPolicy,
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}

impl SyncRunner {
    /// Crée un nouveau runner pour *api* et *workflows_dir*.
    pub fn new(api: N8nApiClient, workflows_dir: PathBuf) -> Self {
        Self {
            api,
            workflows_dir,
            _policy: SyncPolicy::default(),
            stop: Arc::new(AtomicBool::new(false)),
            thread: None,
        }
    }

    /// Crée un runner avec une politique explicite.
    pub fn with_policy(api: N8nApiClient, workflows_dir: PathBuf, policy: SyncPolicy) -> Self {
        Self {
            api,
            workflows_dir,
            _policy: policy,
            stop: Arc::new(AtomicBool::new(false)),
            thread: None,
        }
    }

    /// Démarre la boucle de synchro en arrière-plan (sans effet si déjà en cours).
    pub fn start(&mut self) {
        if self.is_running() {
            return;
        }
        self.stop.store(false, Ordering::SeqCst);
        let api = self.api.clone();
        let workflows_dir = self.workflows_dir.clone();
        let stop = self.stop.clone();
        let interval = std::time::Duration::from_secs(10);
        self.thread = Some(thread::spawn(move || {
            while !stop.load(Ordering::SeqCst) {
                let _ = sync_once_inner(&api, &workflows_dir);
                thread::sleep(interval);
            }
        }));
    }

    /// Signale l'arrêt du thread de synchro et attend sa fin.
    pub fn stop(&mut self) -> Result<(), String> {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(thread) = self.thread.take() {
            thread.join().map_err(|_| "erreur de jointure du thread de synchro".to_string())?;
        }
        Ok(())
    }

    /// Retourne True tant que le thread de synchro est vivant.
    pub fn is_running(&self) -> bool {
        self.thread
            .as_ref()
            .map_or(false, |t| !t.is_finished())
    }

    /// Tire chaque workflow distant qui n'est pas encore exporté localement.
    pub fn sync_once(&self) -> Result<SyncReport, String> {
        sync_once_inner(&self.api, &self.workflows_dir)
    }

    /// Rafraîchit chaque workflow depuis n8n et supprime les exports locaux obsolètes.
    pub fn export_all(&self, mirror: Option<&Path>) -> Result<SyncReport, String> {
        export_all(&self.api, &self.workflows_dir, mirror.map(|p| p.to_path_buf()).as_ref())
    }

    /// Crée dans n8n chaque workflow JSON présent dans le dossier du workspace.
    pub fn import_all(&self) -> Result<SyncReport, String> {
        import_all(&self.api, &self.workflows_dir)
    }
}

/// Tire chaque workflow distant qui n'est pas encore exporté localement.
fn sync_once_inner(api: &N8nApiClient, workflows_dir: &PathBuf) -> Result<SyncReport, String> {
    let _ = fs::create_dir_all(workflows_dir);
    let mut pulled = 0u32;
    let mut skipped = 0u32;
    let workflows = api.list_workflows().map_err(|e| e.to_string())?;
    for workflow in &workflows {
        let workflow_id = id_string(workflow.get("id"));
        if workflow_id.is_empty() {
            skipped += 1;
            continue;
        }
        let name = workflow
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&workflow_id)
            .to_string();
        let filename = export_filename(&name, &workflow_id);
        let target = workflows_dir.join(&filename);
        if target.exists() {
            skipped += 1;
            continue;
        }
        let detail = api.get_workflow(&workflow_id).map_err(|e| e.to_string())?;
        let body = to_sorted_string(&detail);
        let _ = fs::write(&target, body);
        pulled += 1;
    }
    Ok(SyncReport {
        pulled,
        pushed: 0,
        skipped,
    })
}

/// Rafraîchit chaque workflow depuis n8n et supprime les exports locaux obsolètes.
pub fn export_all(
    api: &N8nApiClient,
    workflows_dir: &PathBuf,
    mirror: Option<&PathBuf>,
) -> Result<SyncReport, String> {
    let _ = fs::create_dir_all(workflows_dir);
    let workflows = api.list_workflows().map_err(|e| e.to_string())?;
    let mut exported: HashSet<String> = HashSet::new();
    let mut live_mirror: Option<HashSet<String>> = if mirror.is_some() {
        Some(HashSet::new())
    } else {
        None
    };
    let mut pulled = 0u32;
    for workflow in &workflows {
        let workflow_id = id_string(workflow.get("id"));
        if workflow_id.is_empty() {
            continue;
        }
        let name = workflow
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(&workflow_id);
        let filename = export_filename(name, &workflow_id);
        let body_val = api.get_workflow(&workflow_id).map_err(|e| e.to_string())?;
        let body = to_sorted_string(&body_val);
        let _ = fs::write(workflows_dir.join(&filename), &body);
        exported.insert(filename.clone());
        if let Some(mirror_dir) = mirror {
            let _ = fs::create_dir_all(mirror_dir);
            let _ = fs::write(mirror_dir.join(&filename), &body);
            if let Some(live) = live_mirror.as_mut() {
                live.insert(filename.clone());
            }
        }
        pulled += 1;
    }
    // prune stale in workflows_dir
    if let Ok(entries) = fs::read_dir(workflows_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            let name = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string();
            if exported.contains(&name) {
                continue;
            }
            if !EXPORT_NAME_RE.is_match(&name) {
                continue;
            }
            let _ = fs::remove_file(&path);
        }
    }
    // clean mirror
    if let Some(mirror_dir) = mirror {
        if let Some(live) = &live_mirror {
            if let Ok(entries) = fs::read_dir(mirror_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if !path.is_file() {
                        continue;
                    }
                    let name = path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("")
                        .to_string();
                    if live.contains(&name) {
                        continue;
                    }
                    if !EXPORT_NAME_RE.is_match(&name) {
                        continue;
                    }
                    let _ = fs::remove_file(&path);
                }
            }
        }
    }
    Ok(SyncReport {
        pulled,
        pushed: 0,
        skipped: 0,
    })
}

/// Crée dans n8n chaque workflow JSON présent dans le dossier du workspace.
pub fn import_all(api: &N8nApiClient, workflows_dir: &PathBuf) -> Result<SyncReport, String> {
    let existing = api.list_workflows().map_err(|e| e.to_string())?;
    let mut existing_ids: HashSet<String> = HashSet::new();
    for item in &existing {
        let id = id_string(item.get("id"));
        if !id.is_empty() {
            existing_ids.insert(id);
        }
    }
    let mut existing_names: HashSet<String> = HashSet::new();
    for item in &existing {
        if let Some(name) = item.get("name").and_then(|v| v.as_str()) {
            existing_names.insert(name.to_string());
        }
    }

    let mut paths: Vec<PathBuf> = Vec::new();
    if let Ok(rd) = fs::read_dir(workflows_dir) {
        for entry in rd.flatten() {
            let path = entry.path();
            if path.is_file()
                && path.extension().and_then(|e| e.to_str()) == Some("json")
            {
                paths.push(path);
            }
        }
    }
    paths.sort();

    let mut pushed = 0u32;
    let mut skipped = 0u32;
    for path in paths {
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("");
        let export_id = _export_id(stem);
        if !export_id.is_empty() && existing_ids.contains(&export_id) {
            skipped += 1;
            continue;
        }
        let text = match fs::read_to_string(&path) {
            Ok(t) => t,
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        let workflow: Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        if !workflow.is_object() {
            skipped += 1;
            continue;
        }
        if workflow.get("nodes").is_none() {
            skipped += 1;
            continue;
        }
        let name = workflow
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if !name.is_empty() && existing_names.contains(&name) {
            skipped += 1;
            continue;
        }
        let payload = create_payload(&workflow);
        api.create_workflow(&payload).map_err(|e| e.to_string())?;
        pushed += 1;
    }
    Ok(SyncReport {
        pulled: 0,
        pushed,
        skipped,
    })
}

/// Extrait l'id n8n porté par le nom d'un fichier d'export.
fn _export_id(stem: &str) -> String {
    stem.rsplit('-').next().unwrap_or(stem).to_string()
}

fn id_string(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string().trim_matches('"').to_string(),
    }
}

/// Sérialise une valeur JSON de manière déterministe (clés triées).
fn to_sorted_string(value: &Value) -> String {
    // Use serde_json pretty with sorted keys by converting maps to BTreeMap via
    // a recursive canonicalization.
    let canonical = canonicalize(value);
    serde_json::to_string_pretty(&canonical).unwrap_or_default() + "\n"
}

fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for k in keys {
                if let Some(v) = map.get(k) {
                    out.insert(k.clone(), canonicalize(v));
                }
            }
            Value::Object(out)
        }
        Value::Array(arr) => Value::Array(arr.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sync_policy_default() {
        let p = SyncPolicy::default();
        assert!(p.pull_remote_only);
        assert!(!p.delete_orphans);
    }

    #[test]
    fn test_sync_report_default() {
        let r = SyncReport::default();
        assert_eq!(r.pulled, 0);
        assert_eq!(r.pushed, 0);
        assert_eq!(r.skipped, 0);
    }

    #[test]
    fn test_export_id() {
        assert_eq!(_export_id("Meteo-42"), "42");
        assert_eq!(_export_id("simple"), "simple");
    }
}
