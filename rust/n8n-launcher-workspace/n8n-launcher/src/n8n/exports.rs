//! Export/import planning for workflow JSON.
//! Port de `src/n8n_launcher/n8n/exports.rs`.

use lazy_static::lazy_static;
use regex::Regex;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// Le seul nom de dossier vers lequel le launcher écrit. Consommé par les
/// scripts générés via le jeton ``__CANONICAL_DIRNAME__``.
pub const CANONICAL_DIRNAME: &str = "n8nPipelines";

lazy_static! {
    pub static ref EXPORT_NAME_RE: Regex =
        Regex::new(r"^.+?-(\d+)\.json$").expect("EXPORT_NAME_RE is a valid regex");
}

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

/// Pourquoi un export n'a pas été poussé vers n8n.
pub const SKIP_KNOWN_ID: &str = "id déjà présent dans n8n (lu dans le nom du fichier)";
pub const SKIP_UNREADABLE: &str = "JSON illisible";
pub const SKIP_NOT_A_WORKFLOW: &str = "pas un export de workflow (pas de « nodes »)";
pub const SKIP_KNOWN_NAME: &str = "un workflow de ce nom existe déjà dans n8n";

/// Liste blanche des champs acceptés par le schéma public de création.
/// Le schéma POST valide avec ``additionalProperties: false`` et rejette tout
/// champ réservé au serveur. Les fichiers d'export en contiennent plusieurs
/// (``description``, ``active``, ``triggerCount``, ``shared``, ...).
pub const WORKFLOW_KEYS: &[&str] = &[
    "name",
    "nodes",
    "connections",
    "settings",
    "staticData",
    "pinData",
    "nodeGroups",
    "projectId",
    "parentFolderId",
];

/// Réduit un nom de workflow au jeu de caractères autorisé dans un nom de
/// fichier d'export.
fn _safe_name(value: &str) -> String {
    let text = value.trim();
    if text.is_empty() {
        return "workflow".to_string();
    }
    let safe: String = text
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if safe.is_empty() {
        "workflow".to_string()
    } else {
        safe
    }
}

/// Retourne le nom de fichier d'export du launcher pour un workflow.
pub fn export_filename(name: &str, workflow_id: &str) -> String {
    format!("{}-{}.json", _safe_name(name), workflow_id)
}

/// Retourne le chemin canonique vers lequel l'export d'un workflow est écrit.
pub fn export_path(root: &Path, name: &str, workflow_id: &str) -> PathBuf {
    root.join(CANONICAL_DIRNAME).join(export_filename(name, workflow_id))
}

/// Retourne l'id n8n porté par un nom de fichier d'export du launcher, sinon
/// ``None``. Un fichier nommé ``Meteo-42.json`` porte l'id ``42`` même si le
/// nom du workflow à l'intérieur a changé : cela permet à la réimportation
/// d'ignorer un doublon au lieu de le recréer sous un second nom.
pub fn parse_export_id(filename: &str) -> Option<String> {
    Path::new(filename)
        .file_name()
        .and_then(|n| n.to_str())
        .and_then(|name| EXPORT_NAME_RE.captures(name))
        .and_then(|caps| caps.get(1).map(|m| m.as_str().to_string()))
}

/// Lit le corps d'un fichier d'export, ou ``None`` s'il n'est pas un objet
/// JSON utilisable.
fn _read_body(path: &Path) -> Option<Value> {
    let data = std::fs::read_to_string(path).ok()?;
    let data: Value = serde_json::from_str(&data).ok()?;
    if data.is_object() {
        Some(data)
    } else {
        None
    }
}

/// Charge un export par son relpath ; ``None`` s'il est inutilisable.
pub fn load_export(root: &Path, relpath: &str) -> Option<Value> {
    let path = root.join(relpath);
    if !path.is_file() {
        return None;
    }
    let body = _read_body(&path)?;
    if body.get("nodes").is_some() {
        Some(body)
    } else {
        None
    }
}

/// Construit un corps de requête de création accepté par l'API publique.
/// Une liste blanche plutôt qu'une liste noire, car le schéma de création est
/// strict et les fichiers d'export contiennent de nombreux champs en lecture
/// seule. ``settings`` est requis par ce schéma, donc il vaut par défaut un
/// objet vide.
pub fn create_payload(workflow: &Value) -> Value {
    let mut payload = serde_json::Map::new();
    for key in WORKFLOW_KEYS {
        if let Some(v) = workflow.get(key) {
            payload.insert(key.to_string(), v.clone());
        }
    }
    if !payload.contains_key("settings") {
        payload.insert("settings".to_string(), Value::Object(serde_json::Map::new()));
    }
    Value::Object(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_export_filename() {
        assert_eq!(export_filename("Mon Workflow", "42"), "Mon_Workflow-42.json");
        assert_eq!(export_filename("workflow", ""), "workflow-.json");
    }

    #[test]
    fn test_parse_export_id() {
        assert_eq!(parse_export_id("Meteo-42.json"), Some("42".to_string()));
        assert_eq!(parse_export_id("custom.json"), None);
        assert_eq!(parse_export_id("n8nPipelines/Meteo-42.json"), Some("42".to_string()));
    }

    #[test]
    fn test_safe_name() {
        assert_eq!(_safe_name("Mon Workflow!"), "Mon_Workflow_");
        assert_eq!(_safe_name(""), "workflow");
        assert_eq!(_safe_name("   "), "workflow");
    }

    #[test]
    fn test_create_payload() {
        let workflow = serde_json::json!({
            "name": "Test",
            "nodes": [],
            "connections": {},
            "description": "should be dropped",
            "active": true,
        });
        let payload = create_payload(&workflow);
        assert_eq!(payload.get("name").and_then(|v| v.as_str()), Some("Test"));
        assert!(payload.get("nodes").is_some());
        assert!(payload.get("connections").is_some());
        assert!(payload.get("description").is_none());
        assert!(payload.get("active").is_none());
        assert!(payload.get("settings").is_some());
    }
}
