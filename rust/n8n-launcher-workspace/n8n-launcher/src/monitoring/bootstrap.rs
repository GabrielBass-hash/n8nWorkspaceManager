//! Logging bootstrap.
//! Port de `src/n8n_launcher/monitoring/bootstrap.py`.

use crate::core::paths::logs_dir;

pub fn bootstrap_logging() -> Result<crate::monitoring::store::EventStore, String> {
    let _log_dir = logs_dir()?;
    Ok(crate::monitoring::store::EventStore::new().map_err(|e| e.to_string())?)
}

pub fn capture_exceptions() {
    // No-op stub
}
