//! Workflow API key scopes.
//! Port de `src/n8n_launcher/n8n/scopes.py`.
///
/// Ce contrat est partagé avec le script ``deploy.py`` généré pour le déploiement
/// distant : le même ordre et les mêmes valeurs doivent être utilisés des deux
/// côtés, sinon n8n rejette la création de la clé API.

pub const REQUIRED_WORKFLOW_SCOPES: &[&str] = &[
    "workflow:list",
    "workflow:read",
    "workflow:create",
    "workflow:update",
    "workflow:delete",
    "workflow:activate",
    "credential:list",
    "credential:read",
    "credential:create",
    "credential:update",
    "credential:delete",
];
