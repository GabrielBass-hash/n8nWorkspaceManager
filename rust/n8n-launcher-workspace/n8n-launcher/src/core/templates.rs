//! Loader for the template files that generate the remote and CI artefacts.
//! Port de `src/n8n_launcher/core/templates.py`.

use std::path::PathBuf;

/// Return the text of template *name* inside the embedded templates directory.
///
/// Templates are embedded at compile time via `include_str!` so a missing
/// template fails at build time rather than at the first publish.
pub fn read_template(package: &str, name: &str) -> Result<String, String> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("templates")
        .join(package)
        .join(name);
    std::fs::read_to_string(&path)
        .map_err(|e| format!("Template not found {}: {e}", path.display()))
}
