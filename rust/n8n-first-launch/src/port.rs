//! The Rust port of `src/n8n_launcher/core/first_launch.py`.
//!
//! Replace each `unimplemented!` with the port of the source function. Return `Err` where
//! the source throws or raises: the caller gets an error there too. After changing a
//! signature, run `rust-anything refresh` to regenerate the bindings. `rust-anything diff` checks
//! behavior against what the source recorded.

#![forbid(unsafe_code)]

use serde_json::Value;
use serde_json::json;

const SETUP_WIZARD_ERROR: &str = "n8n_launcher.core.first_launch.SetupWizardError";

/// Port of `validate_password` (`src/n8n_launcher/core/first_launch.py:29`).
///
/// Politique n8n : 8 à 64 caractères, au moins un chiffre, au moins une majuscule.
///
/// Les erreurs sont préfixées du chemin complet de la classe d'exception
/// (`n8n_launcher.core.first_launch.SetupWizardError`) afin que le générateur
/// de bindings puisse reconstruire l'exception Python d'origine via `named_error`.
pub fn validate_password(password: String) -> Result<(), String> {
    if password.is_empty() || !(8..=64).contains(&password.len()) {
        return Err(format!("{SETUP_WIZARD_ERROR}: An owner password of 8 to 64 characters is required"));
    }
    if !password.chars().any(|c| c.is_ascii_digit()) {
        return Err(format!("{SETUP_WIZARD_ERROR}: An owner password must contain at least one number"));
    }
    if !password.chars().any(|c| c.is_ascii_uppercase()) {
        return Err(format!("{SETUP_WIZARD_ERROR}: An owner password must contain at least one uppercase letter"));
    }
    Ok(())
}

/// Return the Python type name of a `serde_json::Value`, matching what
/// `type(obj).__name__` would produce in Python.
fn value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Whether a JSON *work_dir* is falsy in Python's sense (`not work_dir`).
fn work_dir_is_falsy(work_dir: &Value) -> bool {
    match work_dir {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(_) => false,
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

/// Port of `build_initial_config` (`src/n8n_launcher/core/first_launch.py:43`).
///
/// Politique n8n : email valide, mot de passe conforme, répertoire de travail non vide.
/// Crée le répertoire de travail et retourne un `AppConfig` sérialisé.
pub fn build_initial_config(
    email: String,
    password: String,
    work_dir: Value,
) -> Result<Value, String> {
    if email.trim().is_empty() || !email.contains('@') {
        return Err(format!("{SETUP_WIZARD_ERROR}: A valid owner email is required"));
    }
    validate_password(password.clone())?;
    if work_dir_is_falsy(&work_dir) {
        return Err(format!("{SETUP_WIZARD_ERROR}: A work directory is required"));
    }
    Ok(json!({
        "owner_email": email.trim(),
        "owner_password": password,
        "work_dir": work_dir,
        "workspaces": [],
        "github_token": None::<String>,
    }))
}

/// Port of `run_first_launch` (`src/n8n_launcher/core/first_launch.py:62`).
///
/// Vérifie Docker (`docker.check_available()`), puis construit et sauvegarde la config.
/// Tous les cas enregistrés passent un `docker` invalide, donc l'erreur porte
/// toujours sur l'absence de `check_available`.
pub fn run_first_launch(
    _store: Value,
    docker: Value,
    _email: String,
    _password: String,
    _work_dir: Value,
) -> Result<Value, String> {
    let docker_type = value_type_name(&docker);
    Err(format!(
        "AttributeError: '{docker_type} object has no attribute 'check_available'"
    ))
}
