//! Platform module: ports, update flow.
//! Port de `src/n8n_launcher/platform/`.

pub mod ports;
pub mod update_flow;
pub mod updater;

pub use ports::{is_port_available, suggest_port};
