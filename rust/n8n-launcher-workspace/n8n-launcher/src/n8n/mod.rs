//! n8n module: API client, owner bootstrap, workflows.
//! Port de `src/n8n_launcher/n8n/`.

pub mod api;
pub mod owner;
pub mod scopes;
pub mod exports;
pub mod workflows;

pub use scopes::REQUIRED_WORKFLOW_SCOPES;
