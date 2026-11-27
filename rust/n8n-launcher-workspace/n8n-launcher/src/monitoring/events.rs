//! Structured event.
//! Port de `src/n8n_launcher/monitoring/events.py`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: Option<i64>,
    pub name: String,
    pub level: String,
    pub message: String,
    pub timestamp: String,
    pub context: Option<serde_json::Value>,
    pub exception: Option<String>,
}

impl Event {
    pub fn new(name: String, level: String, message: String) -> Self {
        Self {
            id: None,
            name,
            level,
            message,
            timestamp: chrono::Utc::now().to_rfc3339(),
            context: None,
            exception: None,
        }
    }
}
