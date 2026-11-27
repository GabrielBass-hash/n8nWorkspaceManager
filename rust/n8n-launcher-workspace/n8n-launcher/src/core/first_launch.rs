//! First-launch configuration: the wizard's rules, without its dialogs.
//! Port de `src/n8n_launcher/core/first_launch.py`.

use crate::core::config::ConfigStore;
use crate::core::models::AppConfig;
use crate::docker::manager::DockerManager;
use thiserror::Error;

/// Raised when first-launch setup cannot complete.
#[derive(Debug, Error)]
pub enum SetupWizardError {
    #[error("Docker is not ready: {0}")]
    DockerNotReady(String),
    #[error("Invalid inputs: {0}")]
    InvalidInputs(String),
}

/// Enforce n8n's own password policy (8-64 chars, one number, one uppercase).
pub fn validate_password(password: &str) -> Result<(), SetupWizardError> {
    if password.is_empty() || !(8..=64).contains(&password.len()) {
        return Err(SetupWizardError::InvalidInputs(
            "An owner password of 8 to 64 characters is required".to_string(),
        ));
    }
    if !password.chars().any(|c| c.is_ascii_digit()) {
        return Err(SetupWizardError::InvalidInputs(
            "An owner password must contain at least one number".to_string(),
        ));
    }
    if !password.chars().any(|c| c.is_ascii_uppercase()) {
        return Err(SetupWizardError::InvalidInputs(
            "An owner password must contain at least one uppercase letter".to_string(),
        ));
    }
    Ok(())
}

/// Validate the first-launch inputs and return a new `AppConfig`.
pub fn build_initial_config(email: &str, password: &str, work_dir: &std::path::Path) -> Result<AppConfig, SetupWizardError> {
    if email.trim().is_empty() || !email.contains('@') {
        return Err(SetupWizardError::InvalidInputs(
            "A valid owner email is required".to_string(),
        ));
    }
    validate_password(password)?;
    if work_dir.as_os_str().is_empty() {
        return Err(SetupWizardError::InvalidInputs(
            "A work directory is required".to_string(),
        ));
    }
    std::fs::create_dir_all(work_dir).map_err(|e| {
        SetupWizardError::InvalidInputs(format!("Cannot create work directory: {e}"))
    })?;
    Ok(AppConfig::new(
        email.trim().to_string(),
        password.to_string(),
        work_dir.to_path_buf(),
    ))
}

/// Check Docker, build and save the initial config, and return it.
pub fn run_first_launch(
    store: &ConfigStore,
    docker: &DockerManager,
    email: &str,
    password: &str,
    work_dir: &std::path::Path,
) -> Result<AppConfig, SetupWizardError> {
    let status = docker.check_available();
    if !status.available {
        return Err(SetupWizardError::DockerNotReady(status.message));
    }
    let config = build_initial_config(email, password, work_dir)?;
    store.save(&config).map_err(|e| {
        SetupWizardError::InvalidInputs(format!("Cannot save configuration: {e}"))
    })?;
    Ok(config)
}
