//! Remote deploy script generation and path helpers.
//! Port de `src/n8n_launcher/remote/deploy.rs`.

use crate::core::models::ServerConfig;
use serde_json::Value;
use std::collections::BTreeSet;

pub const GENERATED_MARKER: &str = "n8n-launcher : généré — ne pas modifier à la main.";
pub const REMOTE_STATUS_CAPABILITY: &str = "__N8N_LAUNCHER_STATUS_V1__";

pub const TERMINAL_EXECUTION_STATUSES: &[&str] = &["canceled", "crashed", "error", "success"];
pub const PENDING_EXECUTION_STATUSES: &[&str] = &["new", "running", "waiting", "unknown"];

// n8n scope list shared with local bootstrap; duplicated as strings here.
pub const REQUIRED_WORKFLOW_SCOPES: &[&str] = &[
    "workflow:read",
    "workflow:write",
    "workflow:execute",
    "credential:read",
    "credential:write",
    "credential:list",
];

const TEMPLATE_HOOK: &str = include_str!("templates/hook.txt");
const TEMPLATE_DEPLOY: &str = include_str!("templates/deploy.txt");

/// Wrap *value* in single quotes for embedding into bash.
fn sh_sq(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

/// Return the server-side base directory for *cfg*.
pub fn resolve_base(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    let mut base = cfg.base_dir.trim().to_string();
    if base.starts_with("~/") {
        base = base[2..].to_string();
    }
    base = base.trim_end_matches('/').to_string();
    if !base.is_empty() {
        return base;
    }
    if let Some(id) = workspace_id {
        if !id.is_empty() {
            return format!("n8n-launcher/{}", id);
        }
    }
    String::new()
}

pub fn bare_dir(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}.git", resolve_base(cfg, workspace_id))
}

pub fn checkout_dir(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}/workflow", resolve_base(cfg, workspace_id))
}

pub fn log_path(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}/server.log", resolve_base(cfg, workspace_id))
}

pub fn history_path(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}/deploy-history.jsonl", resolve_base(cfg, workspace_id))
}

pub fn deploy_script_path(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}/deploy.py", resolve_base(cfg, workspace_id))
}

pub fn marker_path(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}/last-deploy.json", resolve_base(cfg, workspace_id))
}

pub fn server_remote_url(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    format!("{}@{}:{}", cfg.user, cfg.host, bare_dir(cfg, workspace_id))
}

/// Return the post-receive hook script for *cfg*.
pub fn render_hook(cfg: &ServerConfig, workspace_id: Option<&str>) -> String {
    let mut content = TEMPLATE_HOOK.to_string();
    content = content.replace("__MARKER__", GENERATED_MARKER);
    content = content.replace("__BASE__", &sh_sq(&resolve_base(cfg, workspace_id)));
    content = content.replace("__BARE__", &sh_sq(&bare_dir(cfg, workspace_id)));
    content = content.replace("__WORKFLOW__", &sh_sq(&checkout_dir(cfg, workspace_id)));
    content = content.replace("__LOG__", &sh_sq(&log_path(cfg, workspace_id)));
    let project = if let Some(id) = workspace_id {
        if !id.is_empty() {
            format!("n8n-ws-{}", id)
        } else {
            String::new()
        }
    } else {
        String::new()
    };
    content = content.replace("__PROJECT__", &sh_sq(&project));
    content = content.replace("__N8N_PORT__", &cfg.n8n_port.to_string());
    content
}

/// Return the self-contained server deploy script.
pub fn render_deploy_script() -> String {
    let mut content = TEMPLATE_DEPLOY.to_string();
    content = content.replace("__MARKER__", GENERATED_MARKER);
    content = content.replace("__TERMINAL_STATUSES__", &status_set_literal(TERMINAL_EXECUTION_STATUSES));
    content = content.replace("__PENDING_STATUSES__", &status_set_literal(PENDING_EXECUTION_STATUSES));
    content = content.replace("__API_KEY_SCOPES__", &list_literal(REQUIRED_WORKFLOW_SCOPES));
    content
}

fn status_set_literal(statuses: &[&str]) -> String {
    let set: BTreeSet<&str> = statuses.iter().copied().collect();
    let items: Vec<String> = set.iter().map(|s| format!("{:?}", s)).collect();
    format!("{{{}}}", items.join(", "))
}

fn list_literal(values: &[&str]) -> String {
    let items: Vec<String> = values.iter().map(|v| format!("{:?}", v)).collect();
    format!("[{}]", items.join(", "))
}

/// Assemble the secrets.json upload payload.
pub fn build_secrets_document(
    owner_email: &str,
    owner_password: &str,
    credentials: &[Value],
) -> Value {
    serde_json::json!({
        "owner_email": owner_email,
        "owner_password": owner_password,
        "credentials": credentials,
    })
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::models::ServerConfig;

    fn cfg() -> ServerConfig {
        ServerConfig {
            enabled: true,
            host: "example.com".to_string(),
            ssh_port: 22,
            user: "deploy".to_string(),
            key_path: None,
            base_dir: "n8n-launcher/ws-1".to_string(),
            n8n_port: 5678,
        }
    }

    #[test]
    fn test_resolve_base() {
        let c = cfg();
        assert_eq!(resolve_base(&c, Some("ws-1")), "n8n-launcher/ws-1");
    }

    #[test]
    fn test_resolve_base_tilde() {
        let mut c = cfg();
        c.base_dir = "~/foo".to_string();
        assert_eq!(resolve_base(&c, None), "foo");
    }

    #[test]
    fn test_resolve_base_empty_with_id() {
        let mut c = cfg();
        c.base_dir = "".to_string();
        assert_eq!(resolve_base(&c, Some("ws-2")), "n8n-launcher/ws-2");
    }

    #[test]
    fn test_paths() {
        let c = cfg();
        assert_eq!(bare_dir(&c, Some("ws-1")), "n8n-launcher/ws-1.git");
        assert_eq!(checkout_dir(&c, Some("ws-1")), "n8n-launcher/ws-1/workflow");
        assert_eq!(marker_path(&c, Some("ws-1")), "n8n-launcher/ws-1/last-deploy.json");
    }

    #[test]
    fn test_render_hook_contains_base() {
        let c = cfg();
        let hook = render_hook(&c, Some("ws-1"));
        assert!(hook.contains("n8n-launcher/ws-1"));
        assert!(hook.contains(GENERATED_MARKER));
    }

    #[test]
    fn test_render_deploy_script_nonempty() {
        let s = render_deploy_script();
        assert!(!s.is_empty());
        assert!(s.contains(REMOTE_STATUS_CAPABILITY));
    }

    #[test]
    fn test_build_secrets_document() {
        let doc = build_secrets_document("a@b.com", "pw", &[]);
        assert_eq!(doc.get("owner_email").unwrap(), "a@b.com");
    }

    #[test]
    fn test_status_sets() {
        assert!(TERMINAL_EXECUTION_STATUSES.contains(&"success"));
        assert!(PENDING_EXECUTION_STATUSES.contains(&"running"));
    }
}
