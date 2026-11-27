//! Database module: credentials, layout, migrations.
//! Port de `src/n8n_launcher/database/`.

pub mod credentials;
pub mod layout;
pub mod migrations;

pub fn has_db_layout(_workflows_dir: &std::path::Path) -> bool {
    false
}
