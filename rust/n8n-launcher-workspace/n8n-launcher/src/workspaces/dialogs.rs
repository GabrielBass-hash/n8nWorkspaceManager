//! What the forms collect, not how they looked.
//! Port de `src/n8n_launcher/workspaces/dialogs.rs`.

pub struct CreatePlan {
    pub name: String,
    pub managed: bool,
}

pub fn fresh_managed_db_config() -> crate::core::models::DbConfig {
    crate::core::models::DbConfig::new(crate::core::models::DbMode::MANAGED)
}
