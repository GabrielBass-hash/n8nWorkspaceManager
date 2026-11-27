//! CI harness for a workspace's own repo.
//! Port de `src/n8n_launcher/workspaces/ci.py`.

use lazy_static::lazy_static;
use regex::Regex;
use serde_json::Value;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// Répertoire géré par le launcher dans le dépôt du workspace.
pub const CI_DIR: &str = ".n8n-tests";
/// Fichier de sélection des pipelines de tests.
pub const SELECTION_FILE: &str = "tests.json";
/// Workflow GitHub Actions généré.
pub const WORKFLOW_FILE: &str = ".github/workflows/n8n-ci.yml";
/// Script de validation statique généré.
pub const VALIDATE_FILE: &str = ".n8n-tests/validate.py";
/// Script runner de tests généré.
pub const RUNNER_FILE: &str = ".n8n-tests/runner.py";

/// Marqueur écrit en tête de chaque fichier généré.
pub const GENERATED_MARKER: &str = "n8n-launcher : généré — ne pas modifier à la main.";
/// Nom du secret GitHub contenant les credentials.
pub const SECRET_NAME: &str = "N8N_CI_CREDENTIALS";

/// Fichiers supprimés quand la CI est désactivée (tests.json est conservé).
pub const DISABLE_FILES: &[&str] = &[WORKFLOW_FILE, VALIDATE_FILE, RUNNER_FILE];

/// Types de déclencheurs manuels pouvant démarrer une exécution depuis l'éditeur.
pub const MANUAL_TRIGGER_TYPES: &[&str] = &[
    "n8n-nodes-base.manualTrigger",
    "@n8n/n8n-nodes-langchain.manualChatTrigger",
];
/// Types de déclencheurs planifiés.
pub const SCHEDULE_TRIGGER_TYPES: &[&str] = &["n8n-nodes-base.scheduleTrigger"];

/// Fichiers JSON racine qui ne sont manifestement pas des exports de workflow.
pub const ROOT_BLOCKLIST: &[&str] = &[
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "composer.json",
    "Pipfile",
    "Pipfile.lock",
    "deno.json",
    "deno.lock",
];

/// Jeton remplacé au rendu par le marqueur généré.
const _MARKER_TOKEN: &str = "__MARKER__";
/// Jeton remplacé au rendu par l'image n8n épinglée.
const _IMAGE_TOKEN: &str = "__N8N_IMAGE__";

lazy_static! {
    /// URL de dépôt GitHub (HTTPS).
    static ref _HTTPS_REPO: Regex =
        Regex::new(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
            .expect("_HTTPS_REPO is a valid regex");
    /// URL de dépôt GitHub (SSH scp-like).
    static ref _SSH_REPO: Regex =
        Regex::new(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?/?$")
            .expect("_SSH_REPO is a valid regex");
    /// URL de dépôt GitHub (SSH explicite).
    static ref _GIT_SSH_REPO: Regex =
        Regex::new(r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
            .expect("_GIT_SSH_REPO is a valid regex");
    /// Tag d'image Docker valide.
    static ref _IMAGE_TAG: Regex =
        Regex::new(r"^[A-Za-z0-9._-]+$").expect("_IMAGE_TAG is a valid regex");
}

/// Extrait ``owner/repo`` d'une URL de dépôt distant GitHub.
///
/// Retourne ``None`` pour les URLs vides, GitLab, chemins locaux, ou tout ce
/// qui n'est pas un dépôt GitHub.
pub fn github_repo_path(remote_url: Option<&str>) -> Option<String> {
    let remote_url = remote_url?;
    let candidate = remote_url.trim();
    for pattern in [&*_HTTPS_REPO, &*_SSH_REPO, &*_GIT_SSH_REPO] {
        if let Some(caps) = pattern.captures(candidate) {
            return Some(caps.get(1)?.as_str().to_string());
        }
    }
    None
}

/// Retourne les chemins relatifs POSIX de chaque export de workflow dans le workspace.
///
/// Miroir du comportement d'import du launcher : ``n8nPipelines/*.json`` plus
/// les fichiers ``*.json`` à la racine. Les fichiers manifest/lock sont exclus.
///
/// Les exports racines qui dupliquent un pipeline déjà trouvé sous
/// ``n8nPipelines/`` (même nom de base) sont supprimés — le miroir n'est que
/// pour la fraîcheur, les copies canoniques sont celles exportées vers le
/// dossier de pipelines.
pub fn collect_workflows(workflows_dir: &Path) -> Vec<String> {
    let mut found_paths: Vec<String> = Vec::new();
    let mut seen_basename: HashSet<String> = HashSet::new();

    let pipelines_dir = workflows_dir.join("n8nPipelines");
    if pipelines_dir.is_dir() {
        if let Ok(entries) = std::fs::read_dir(&pipelines_dir) {
            let mut paths: Vec<_> = entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.is_file() && p.extension().map_or(false, |ext| ext == "json"))
                .collect();
            paths.sort();
            for path in paths {
                if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                    let rel = format!("n8nPipelines/{name}");
                    found_paths.push(rel.clone());
                    seen_basename.insert(name.to_string());
                }
            }
        }
    }

    if let Ok(entries) = std::fs::read_dir(workflows_dir) {
        let mut paths: Vec<_> = entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_file() && p.extension().map_or(false, |ext| ext == "json"))
            .collect();
        paths.sort();
        for path in paths {
            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                if ROOT_BLOCKLIST.contains(&name) {
                    continue;
                }
                if seen_basename.contains(name) {
                    continue;
                }
                found_paths.push(name.to_string());
            }
        }
    }

    found_paths.sort();
    found_paths
}

/// Charge et vérifie un export de workflow ; ``None`` quand illisible.
pub fn load_export(workflows_dir: &Path, rel: &str) -> Option<Value> {
    let path = workflows_dir.join(rel);
    if !path.is_file() {
        return None;
    }
    let data = std::fs::read_to_string(&path).ok()?;
    let data: Value = serde_json::from_str(&data).ok()?;
    if !data.is_object() || !data.get("nodes").map_or(false, |n| n.is_array()) {
        return None;
    }
    Some(data)
}

/// Retourne le chemin du fichier de sélection ``tests.json`` du workspace.
pub fn selection_path(workflows_dir: &Path) -> PathBuf {
    workflows_dir.join(CI_DIR).join(SELECTION_FILE)
}

/// Retourne la sélection de pipelines persistée comme un ensemble de chemins relatifs.
pub fn read_selection(workflows_dir: &Path) -> HashSet<String> {
    let path = selection_path(workflows_dir);
    let data: Value = match std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
    {
        Some(d) => d,
        None => return HashSet::new(),
    };
    let selected = data.get("selected").and_then(|v| v.as_array());
    selected
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

/// Persiste la sélection de pipelines (triée, sortie stable).
pub fn write_selection(workflows_dir: &Path, selected: &HashSet<String>) {
    let path = selection_path(workflows_dir);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut sorted: Vec<&String> = selected.iter().collect();
    sorted.sort();
    let data = serde_json::json!({ "selected": sorted });
    let content = serde_json::to_string_pretty(&data).unwrap_or_default() + "\n";
    let _ = std::fs::write(&path, content);
}

/// Retourne True quand le type de node n8n est un déclencheur (le type se termine par Trigger).
fn _is_trigger_type(node_type: &str) -> bool {
    node_type.rsplit('.').next().map_or(false, |s| s.ends_with("Trigger"))
}

/// Retourne les noms d'affichage des credentials absents de *provided*.
///
/// Les nodes avec des données épinglées sont exclus : un node épinglé tourne
/// sur ses données épinglées et n'a pas besoin de ses credentials à l'exécution.
pub fn missing_credentials(
    export: &Value,
    provided: &HashSet<String>,
) -> Vec<String> {
    let pin_data = export.get("pinData").and_then(|v| v.as_object());
    let mut missing: HashSet<String> = HashSet::new();
    let nodes = export.get("nodes").and_then(|v| v.as_array());
    if let Some(nodes) = nodes {
        for node in nodes {
            if !node.is_object() {
                continue;
            }
            let node_name = node.get("name").and_then(|v| v.as_str()).unwrap_or("");
            if let Some(pd) = pin_data {
                if pd.contains_key(node_name) {
                    continue;
                }
            }
            let credentials = node.get("credentials").and_then(|v| v.as_object());
            if let Some(creds) = credentials {
                for (_key, credential) in creds {
                    if !credential.is_object() {
                        continue;
                    }
                    let name = credential
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .trim();
                    let ctype = credential
                        .get("type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .trim();
                    if name.is_empty() && ctype.is_empty() {
                        continue;
                    }
                    let key = format!("{ctype}/{name}");
                    if !provided.contains(&key) {
                        let label = if !name.is_empty() {
                            format!("{name} ({ctype})")
                        } else if !ctype.is_empty() {
                            format!("({ctype})")
                        } else {
                            continue;
                        };
                        missing.insert(label);
                    }
                }
            }
        }
    }
    let mut missing: Vec<String> = missing.into_iter().collect();
    missing.sort();
    missing
}

/// Retourne le déclencheur à démarrer, ou ``None`` pour les runs par nœud de destination.
fn default_start_trigger(export: &Value) -> Option<String> {
    let nodes = export.get("nodes").and_then(|v| v.as_array())?;
    let pin_data = export.get("pinData").and_then(|v| v.as_object());
    for node in nodes {
        if let Some(obj) = node.as_object() {
            if let Some(ntype) = obj.get("type").and_then(|v| v.as_str()) {
                if MANUAL_TRIGGER_TYPES.contains(&ntype) {
                    return obj.get("name").and_then(|v| v.as_str()).map(|s| s.to_string());
                }
            }
        }
    }
    for node in nodes {
        if let Some(obj) = node.as_object() {
            if let Some(ntype) = obj.get("type").and_then(|v| v.as_str()) {
                if _is_trigger_type(ntype) {
                    if let Some(name) = obj.get("name").and_then(|v| v.as_str()) {
                        if pin_data.map_or(false, |pd| pd.contains_key(name)) {
                            return Some(name.to_string());
                        }
                    }
                }
            }
        }
    }
    None
}

/// Construit l'ensemble des clés ``type/name`` depuis les métadonnées ``(name, type)`` enregistrées.
fn _credential_keys(credentials: &[Value]) -> HashSet<String> {
    let mut keys = HashSet::new();
    for item in credentials {
        if let Some(obj) = item.as_object() {
            let name = obj.get("name").and_then(|v| v.as_str()).unwrap_or("").trim();
            let ctype = obj.get("type").and_then(|v| v.as_str()).unwrap_or("").trim();
            if !name.is_empty() && !ctype.is_empty() {
                keys.insert(format!("{ctype}/{name}"));
            }
        }
    }
    keys
}

/// Retourne (éligible, raison) pour un pipeline.
///
/// Un pipeline est éligible pour la CI quand il a un déclencheur que n8n peut
/// démarrer sans entrée externe (manuel, planning, ou déclencheur webhook/chat
/// épinglé) *et* que tous les credentials des nodes non épinglés sont couverts
/// par *credentials*.
///
/// La raison est un texte français lisible par l'utilisateur expliquant le
/// refus le cas échéant.
pub fn workflow_eligibility(
    export: &Value,
    credentials: &[Value],
) -> (bool, String) {
    let nodes: Vec<Value> = export
        .get("nodes")
        .and_then(|v| v.as_array())
        .into_iter()
        .flat_map(|arr| arr.iter().cloned())
        .filter(|v| v.is_object())
        .collect();

    let triggers: Vec<&Value> = nodes
        .iter()
        .filter(|node| {
            node.get("type")
                .and_then(|v| v.as_str())
                .map_or(false, |t| _is_trigger_type(t))
        })
        .collect();

    if triggers.is_empty() {
        return (false, "aucun déclencheur".to_string());
    }

    let node_types: HashSet<&str> = triggers
        .iter()
        .filter_map(|node| node.get("type").and_then(|v| v.as_str()))
        .collect();

    let runnable = MANUAL_TRIGGER_TYPES.iter().any(|t| node_types.contains(*t))
        || SCHEDULE_TRIGGER_TYPES.iter().any(|t| node_types.contains(*t))
        || default_start_trigger(export).is_some();

    if !runnable {
        return (
            false,
            "déclencheur webhook/chat non épinglé (épinglez-le dans l'éditeur)".to_string(),
        );
    }

    let credential_keys = _credential_keys(credentials);
    let missing = missing_credentials(export, &credential_keys);
    if !missing.is_empty() {
        return (false, format!("credentials manquantes : {}", missing.join(", ")));
    }

    (true, String::new())
}

/// Valide une version n8n pour qu'elle ne puisse pas s'injecter dans un tag Docker.
fn _safe_image_tag(version: &str) -> String {
    let tag = version.trim();
    if _IMAGE_TAG.is_match(tag) {
        tag.to_string()
    } else {
        "2.40.0".to_string()
    }
}

/// Retourne chaque fichier généré sous la forme ``chemin relatif -> contenu``.
pub fn render_harness(n8n_version: &str) -> Vec<(String, String)> {
    let image = format!(
        "docker.n8n.io/n8nio/n8n:{}",
        _safe_image_tag(n8n_version)
    );
    vec![
        (WORKFLOW_FILE.to_string(), _workflow_content()),
        (VALIDATE_FILE.to_string(), _validate_content()),
        (
            RUNNER_FILE.to_string(),
            _runner_content(&image),
        ),
        (
            format!("{CI_DIR}/{SELECTION_FILE}"),
            _empty_selection(),
        ),
    ]
}

fn _empty_selection() -> String {
    "{\n  \"selected\": []\n}\n".to_string()
}

fn _workflow_content() -> String {
    TEMPLATE_WORKFLOW.replace(_MARKER_TOKEN, GENERATED_MARKER)
}

fn _validate_content() -> String {
    TEMPLATE_VALIDATE.replace(_MARKER_TOKEN, GENERATED_MARKER)
}

fn _runner_content(image: &str) -> String {
    TEMPLATE_RUNNER.replace(_MARKER_TOKEN, GENERATED_MARKER).replace(_IMAGE_TOKEN, image)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_github_repo_path_https() {
        assert_eq!(
            github_repo_path(Some("https://github.com/owner/repo.git")),
            Some("owner/repo".to_string())
        );
        assert_eq!(
            github_repo_path(Some("https://github.com/owner/repo")),
            Some("owner/repo".to_string())
        );
        assert_eq!(github_repo_path(Some("https://gitlab.com/owner/repo.git")), None);
        assert_eq!(github_repo_path(None), None);
        assert_eq!(github_repo_path(Some("")), None);
    }

    #[test]
    fn test_github_repo_path_ssh() {
        assert_eq!(
            github_repo_path(Some("git@github.com:owner/repo.git")),
            Some("owner/repo".to_string())
        );
        assert_eq!(
            github_repo_path(Some("ssh://git@github.com/owner/repo.git")),
            Some("owner/repo".to_string())
        );
    }

    #[test]
    fn test_github_repo_path_with_dots() {
        assert_eq!(
            github_repo_path(Some("https://github.com/my-org/my.repo.git")),
            Some("my-org/my.repo".to_string())
        );
    }

    #[test]
    fn test_collect_workflows_empty() {
        let dir = tempfile::tempdir().unwrap();
        let result = collect_workflows(dir.path());
        assert!(result.is_empty());
    }

    #[test]
    fn test_load_export_missing() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_export(dir.path(), "nonexistent.json").is_none());
    }

    #[test]
    fn test_workflow_eligibility_no_triggers() {
        let export = serde_json::json!({"nodes": []});
        let eligible = workflow_eligibility(&export, &[]);
        assert!(!eligible.0);
        assert_eq!(eligible.1, "aucun déclencheur");
    }

    #[test]
    fn test_workflow_eligibility_manual_trigger() {
        let export = serde_json::json!({
            "nodes": [
                {"name": "Start", "type": "n8n-nodes-base.manualTrigger"}
            ]
        });
        let eligible = workflow_eligibility(&export, &[]);
        assert!(eligible.0);
        assert!(eligible.1.is_empty());
    }

    #[test]
    fn test_workflow_eligibility_missing_credentials() {
        let export = serde_json::json!({
            "nodes": [
                {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
                {"name": "DB", "type": "n8n-nodes-base.postgres", "credentials": {"postgresCredential": {"name": "my-db", "type": "postgres"}}}
            ]
        });
        let eligible = workflow_eligibility(&export, &[]);
        assert!(!eligible.0);
        assert!(eligible.1.contains("credentials manquantes"));
    }

    #[test]
    fn test_workflow_eligibility_with_credentials() {
        let export = serde_json::json!({
            "nodes": [
                {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
                {"name": "DB", "type": "n8n-nodes-base.postgres", "credentials": {"postgresCredential": {"name": "my-db", "type": "postgres"}}}
            ]
        });
        let credentials = vec![serde_json::json!({"name": "my-db", "type": "postgres"})];
        let eligible = workflow_eligibility(&export, &credentials);
        assert!(eligible.0);
        assert!(eligible.1.is_empty());
    }

    #[test]
    fn test_render_harness() {
        let files = render_harness("2.40.0");
        let paths: Vec<&str> = files.iter().map(|(p, _)| p.as_str()).collect();
        let selection_path = format!("{CI_DIR}/{SELECTION_FILE}");
        assert!(paths.contains(&WORKFLOW_FILE));
        assert!(paths.contains(&VALIDATE_FILE));
        assert!(paths.contains(&RUNNER_FILE));
        assert!(paths.contains(&selection_path.as_str()));
        // Only the three template files contain the GENERATED_MARKER
        for (path, content) in &files {
            if path != &format!("{CI_DIR}/{SELECTION_FILE}") {
                assert!(content.contains(GENERATED_MARKER), "missing marker in {path}");
            }
        }
        // Runner should contain the pinned image
        let runner_entry = files.iter().find(|(p, _)| p == RUNNER_FILE).unwrap();
        let runner = runner_entry.1.as_str();
        assert!(runner.contains("docker.n8n.io/n8nio/n8n:2.40.0"));
    }

    #[test]
    fn test_render_harness_invalid_version() {
        let files = render_harness("invalid version!!");
        let runner_entry = files.iter().find(|(p, _)| p == RUNNER_FILE).unwrap();
        let runner = runner_entry.1.as_str();
        assert!(runner.contains("docker.n8n.io/n8nio/n8n:2.40.0"));
    }
}

const TEMPLATE_WORKFLOW: &str = r#"# __MARKER__
# GitHub Actions CI pour ce workspace n8n : exécute les pipelines sélectionnées
# dans un conteneur n8n isolé et jetable (voir .n8n-tests/runner.py).
name: n8n-launcher CI

on:
  push:
    branches: ["dev"]
  pull_request:
  workflow_dispatch:

concurrency:
  group: "ci-${{ github.ref }}"
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  validate:
    name: Valider les exports
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - name: Validation statique des exports
        run: python .n8n-tests/validate.py

  test:
    name: Exécuter les pipelines
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - name: Compter les pipelines sélectionnées
        id: selection
        run: |
          count=$(python -c "import json;print(len(json.load(open('.n8n-tests/tests.json', encoding='utf-8'))['selected']))" 2>/dev/null || echo 0)
          echo "count=$count" >> "$GITHUB_OUTPUT"
      - name: Lancer le runner de tests n8n
        # Une sélection vide (aucun test coché) ne lance rien : le job passe
        # « vert » sans démarrer de conteneur, au lieu d'exécuter l'ancienne
        # sélection encore présente sur GitHub.
        if: steps.selection.outputs.count != '0'
        env:
          N8N_IMAGE: ${{ vars.N8N_IMAGE }}
          N8N_CI_CREDENTIALS: ${{ secrets.N8N_CI_CREDENTIALS }}
        run: python .n8n-tests/runner.py
"#;

const TEMPLATE_VALIDATE: &str = r#"# __MARKER__
# Validation statique des exports de workflows et de la sélection de tests.
# Reste volontairement en bibliothèque standard, sans Docker.
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILE = ROOT / ".n8n-tests" / "tests.json"

# Fichiers de manifest manifestement non-workflow (miroir du launcher).
BLOCKLIST_ROOT = {
    "package.json", "package-lock.json", "tsconfig.json", "composer.json",
    "Pipfile", "Pipfile.lock", "deno.json", "deno.lock",
}


def collect_workflows():
    found = []
    pipeline_names = set()
    pipelines = ROOT / "n8nPipelines"
    if pipelines.is_dir():
        for path in sorted(pipelines.glob("*.json")):
            if path.is_file():
                found.append(f"n8nPipelines/{path.name}")
                pipeline_names.add(path.name)
    for path in sorted(ROOT.glob("*.json")):
        if (
            path.is_file()
            and path.name not in BLOCKLIST_ROOT
            and path.name not in pipeline_names
        ):
            found.append(path.name)
    return sorted(found)


def main():
    errors = []
    seen_names = set()
    exports = {}
    for rel in collect_workflows():
        path = ROOT / rel
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{rel} : fichier JSON illisible ({exc})")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            errors.append(f"{rel} : export invalide (\"nodes\" manquant)")
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            errors.append(f"{rel} : nom de workflow manquant")
        if name in seen_names:
            errors.append(f"{rel} : nom de workflow dupliqué (« {name} »)")
        seen_names.add(name)
        if not isinstance(data.get("connections"), dict):
            errors.append(f"{rel} : connexions invalides")
        for index, node in enumerate(data.get("nodes")):
            if not isinstance(node, dict) or not str(node.get("name") or "").strip() or not str(node.get("type") or "").strip():
                errors.append(f"{rel} : nœud n°{index} invalide (nom/type manquants)")
        exports[rel] = data
    try:
        selection = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        selection = {}
    selected = set()
    if isinstance(selection, dict):
        selected = {str(item) for item in selection.get("selected", []) if isinstance(item, str)}
    for rel in sorted(selected):
        if rel not in exports:
            errors.append(f"tests.json : « {rel} » ne correspond à aucun export connu")
    if not errors:
        print(f"OK : {len(exports)} export(s) valide(s), {len(selected)} pipeline(s) sélectionnée(s).")
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
"#;

const TEMPLATE_RUNNER: &str = r#"# __MARKER__
# Runner de tests : exécute les pipelines sélectionnées dans un conteneur n8n
# jetable. Bibliothèque standard uniquement, aucun paquet à installer.
# Usage : python .n8n-tests/runner.py
# Variables gérées par GitHub Actions : N8N_CI_CREDENTIALS (secret) et
# N8N_IMAGE (variable, optionnelle — la version pinnée par le launcher est
# utilisée par défaut).
from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SELECTION_FILE = ROOT / ".n8n-tests" / "tests.json"

# Image pinnée par le launcher (surritable via le variable N8N_IMAGE).
N8N_IMAGE = os.environ.get("N8N_IMAGE") or "__N8N_IMAGE__"
SECRET_NAME = "N8N_CI_CREDENTIALS"

OWNER_EMAIL = "ci@n8n-launcher.test"
OWNER_PASSWORD = "CiPassw0rd-n8n9"  # conforme à la politique de mot de passe n8n
API_KEY_LABEL = "n8n-ci"

# Délais (secondes) : démarrage du conteneur / exécution d'une pipeline.
START_TIMEOUT = 240.0
RUN_TIMEOUT = 300.0
POLL_INTERVAL = 2.0

MANUAL_TRIGGERS = {
    "n8n-nodes-base.manualTrigger",
    "@n8n/n8n-nodes-langchain.manualChatTrigger",
}
SCHEDULE_TRIGGERS = {"n8n-nodes-base.scheduleTrigger"}

BLOCKLIST_ROOT = {
    "package.json", "package-lock.json", "tsconfig.json", "composer.json",
    "Pipfile", "Pipfile.lock", "deno.json", "deno.lock",
}

# Champ acceptés par le schéma public de création de workflows (strict).
CREATE_WHITELIST = {
    "name", "nodes", "connections", "settings", "staticData", "pinData",
    "nodeGroups", "projectId", "parentFolderId",
}


def _verbose(message: str) -> None:
    print(f"[runner] {message}", flush=True)


def find_free_port() -> int:
    """Laisse l'OS choisir un port local libre pour publier n8n."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Http:
    """Client HTTP JSON minimaliste avec gestion des cookies (stdlib only)."""

    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Exécute la requête ; rend (code HTTP, corps JSON ou texte)."""
        url = self.base_url + path
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                code = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            code = exc.code
        except OSError as exc:
            return 0, str(exc)
        text = raw.strip()
        if not text:
            return code, None
        try:
            return code, json.loads(text)
        except ValueError:
            return code, text


def collect_workflows():
    found = []
    pipeline_names = set()
    pipelines = ROOT / "n8nPipelines"
    if pipelines.is_dir():
        for path in sorted(pipelines.glob("*.json")):
            if path.is_file():
                found.append(f"n8nPipelines/{path.name}")
                pipeline_names.add(path.name)
    for path in sorted(ROOT.glob("*.json")):
        if (
            path.is_file()
            and path.name not in BLOCKLIST_ROOT
            and path.name not in pipeline_names
        ):
            found.append(path.name)
    return sorted(found)


def read_selection():
    try:
        data = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {str(item) for item in data.get("selected", []) if isinstance(item, str)}


def wait_ready(http: Http) -> None:
    """Attend que n8n réponde en JSON (owner/setup sert de sonde)."""
    deadline = time.monotonic() + START_TIMEOUT
    payload = {
        "firstName": "CI",
        "lastName": "Runner",
        "email": OWNER_EMAIL,
        "password": OWNER_PASSWORD,
    }
    while True:
        try:
            code, body = http.request("POST", "/rest/owner/setup", payload)
        except Exception as exc:
            code, body = 0, str(exc)
        if not isinstance(body, str):
            print(f"[runner] n8n répond (HTTP {code}) — prêt", flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("n8n ne répond pas en JSON dans le délai imparti")
        time.sleep(2.0)


def login(http: Http) -> None:
    """Connecte le propriétaire ; l'auth des endpoints /rest passe par le
    cookie de session ``n8n-auth`` posé par la réponse (jamais par un jeton
    dans le corps de réponse sur n8n 2.33+). Le jeton éventuel du corps n'est
    accepté que comme signal de secours pour de futures versions."""
    code, body = http.request(
        "POST",
        "/rest/login",
        {"emailOrLdapLoginId": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    body_token = isinstance(body, dict) && bool((body.get("data") or {}).get("token"))
    has_session = any(cookie.name == "n8n-auth" for cookie in http.cookies)
    if code not in (200, 201) || !(body_token || has_session) {
        raise RuntimeError(f"connexion au propriétaire impossible (HTTP {code}) : {body}")
    }

def create_api_key(http: Http) -> str {
    code, body = http.request(
        "POST",
        "/rest/api-keys",
        {
            "label": API_KEY_LABEL,
            "scopes": [
                "workflow:list",
                "workflow:create",
                "workflow:read",
                "credential:list",
                "credential:read",
                "credential:create",
            ],
            "expiresAt": 0,
        },
    )
    if not isinstance(body, dict):
        raise RuntimeError(f"n8n n'a pas répondu en JSON pour la clé API (HTTP {code})")
    secret = body.get("data", {}).get("rawApiKey") || body.get("rawApiKey")
    if not secret {
        raise RuntimeError(f"n8n n'a pas renvoyé de clé API (HTTP {code}) : {body}")
    }
    return str(secret)
"#;
