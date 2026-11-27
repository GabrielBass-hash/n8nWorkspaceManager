//! Secret redaction before persistence.
//! Port de `src/n8n_launcher/monitoring/redaction.py`.

use lazy_static::lazy_static;
use regex::{Captures, Regex};
use serde_json::Value;
use std::collections::BTreeMap;

lazy_static! {
    /// Correspond à un en-tête `Authorization` suivi de sa valeur.
    static ref AUTHORIZATION_RE: Regex =
        Regex::new(r#"(?i)(?P<prefix>["']?\bauthorization\b["']?\s*[:=]\s*)(?P<value>[^,\r\n]+)"#)
            .expect("AUTHORIZATION_RE is a valid regex");
    /// Correspond à une assignation `clé = valeur` où la clé est un nom de secret connu.
    static ref ASSIGNMENT_RE: Regex =
        Regex::new(r#"(?i)(?P<prefix>["']?\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|cookie|passphrase)\b["']?\s*[:=]\s*)(?:["'](?P<quoted>.*?)["']|(?P<value>[^\s,;}\]\r\n]+))"#)
            .expect("ASSIGNMENT_RE is a valid regex");
    /// Correspond à un token Bearer.
    static ref BEARER_RE: Regex = Regex::new(r#"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"#)
        .expect("BEARER_RE is a valid regex");
    /// Correspond à un mot de passe dans une URL.
    static ref URL_PASSWORD_RE: Regex =
        Regex::new(r#"(?i)(?P<prefix>[a-z][\w+.-]*://[^/\s:@]+:)[^@\s]+(?P<suffix>@)"#)
            .expect("URL_PASSWORD_RE is a valid regex");
    /// Correspond à des tokens GitHub / OpenAI.
    static ref TOKEN_RE: Regex =
        Regex::new(r#"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{8,})\b"#)
            .expect("TOKEN_RE is a valid regex");
    /// Normalise une clé pour la comparaison insensible à la casse.
    static ref NORMALIZE_KEY_RE: Regex = Regex::new(r#"[^a-z0-9]"#).expect("NORMALIZE_KEY_RE is a valid regex");
}

const SENSITIVE_KEY_PARTS: &[&str] = &[
    "password", "passwd", "pwd", "secret", "token", "apikey", "accesskey",
    "privatekey", "clientsecret", "authorization", "credential", "cookie",
    "passphrase",
];

/// Masque les valeurs qui ressemblent à des secrets avant persistance.
pub fn redact(value: &str) -> String {
    redact_text(value, "***REDACTED***")
}

/// Retourne une copie récursivement caviardée, JSON-friendly, de *value*.
///
/// Les clés de mappage qui identifient des identifiants, mots de passe, tokens
/// ou matériel de session sont remplacées en tant que telles. Les chaînes sont
/// également scannées pour les formes communes d'assignation, bearer-token,
/// GitHub-token et clé OpenAI afin qu'un secret ne puisse échapper via un
/// message de log ou un texte d'exception.
pub fn redact_secrets(value: &Value, replacement: &str) -> Value {
    match value {
        Value::Object(map) => {
            let mut redacted = BTreeMap::new();
            for (key, item) in map {
                let key_text = redact_text(key, replacement);
                if is_sensitive_key(key) {
                    redacted.insert(key_text, Value::String(replacement.to_string()));
                } else {
                    redacted.insert(key_text, redact_secrets(item, replacement));
                }
            }
            Value::Object(redacted.into_iter().collect())
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| redact_secrets(item, replacement)).collect())
        }
        Value::String(s) => Value::String(redact_text(s, replacement)),
        Value::Number(n) => Value::Number(n.clone()),
        Value::Bool(b) => Value::Bool(*b),
        Value::Null => Value::Null,
    }
}

/// Redige les formes de secrètes communes d'une chaîne sans modifier le texte ordinaire.
fn redact_text(value: &str, replacement: &str) -> String {
    let mut value = value.to_string();
    value = AUTHORIZATION_RE.replace_all(&value, |caps: &Captures| {
        format!("{}{}", &caps["prefix"], replacement)
    }).into_owned();
    value = ASSIGNMENT_RE.replace_all(&value, |caps: &Captures| {
        let prefix = &caps["prefix"];
        if let Some(_quoted) = caps.name("quoted") {
            format!("{}{}", prefix, replacement)
        } else {
            format!("{}{}", prefix, replacement)
        }
    }).into_owned();
    value = BEARER_RE.replace_all(&value, format!("Bearer {replacement}")).into_owned();
    value = URL_PASSWORD_RE.replace_all(&value, |caps: &Captures| {
        format!("{}{}{}", &caps["prefix"], replacement, &caps["suffix"])
    }).into_owned();
    TOKEN_RE.replace_all(&value, replacement).into_owned()
}

/// Retourne si une clé de mappage identifie une valeur qui ne doit jamais être conservée.
fn is_sensitive_key(key: &str) -> bool {
    let lowered = key.to_lowercase();
    let normalized = NORMALIZE_KEY_RE.replace_all(&lowered, "");
    SENSITIVE_KEY_PARTS.iter().any(|part| normalized.contains(part))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_redact_simple() {
        assert_eq!(redact("hello world"), "hello world");
        assert_eq!(redact("my_token"), "my_token");
        assert_eq!(redact("my_password"), "my_password");
    }

    #[test]
    fn test_redact_authorization() {
        let result = redact_text(r#"Authorization: Bearer abc123"#, "***REDACTED***");
        assert!(result.contains("***REDACTED***"));
        assert!(!result.contains("abc123"));
    }

    #[test]
    fn test_redact_bearer() {
        let result = redact_text("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "***REDACTED***");
        assert_eq!(result, "Bearer ***REDACTED***");
    }

    #[test]
    fn test_redact_url_password() {
        let result = redact_text("https://user:secret@example.com/path", "***REDACTED***");
        assert!(!result.contains("secret"));
        assert!(result.contains("https://user:***REDACTED***@example.com/path"));
    }

    #[test]
    fn test_redact_github_token() {
        let result = redact_text("ghp_abcdefghijklmnopqrstuvwxyz1234567890", "***REDACTED***");
        assert_eq!(result, "***REDACTED***");
    }

    #[test]
    fn test_redact_assignment_in_plain_text() {
        let result = redact_text("login password=hunter2 ok", "***REDACTED***");
        assert!(!result.contains("hunter2"));
        assert!(result.contains("login password=***REDACTED*** ok"));
    }

    #[test]
    fn test_redact_secrets_object() {
        let input = serde_json::json!({"name": "alice", "password": "s3cret", "token": "ghp_xyz"});
        let result = redact_secrets(&input, "***REDACTED***");
        assert_eq!(result["name"], "alice");
        assert_eq!(result["password"], "***REDACTED***");
        assert_eq!(result["token"], "***REDACTED***");
    }

    #[test]
    fn test_redact_secrets_nested() {
        let input = serde_json::json!({"db": {"host": "localhost", "password": "hidden"}});
        let result = redact_secrets(&input, "***REDACTED***");
        assert_eq!(result["db"]["host"], "localhost");
        assert_eq!(result["db"]["password"], "***REDACTED***");
    }

    #[test]
    fn test_is_sensitive_key() {
        assert!(is_sensitive_key("password"));
        assert!(is_sensitive_key("api_key"));
        assert!(is_sensitive_key("clientSecret"));
        assert!(!is_sensitive_key("name"));
        assert!(!is_sensitive_key("host"));
    }

    #[test]
    fn test_redact_preserves_ordinary_text() {
        let text = "The quick brown fox jumps over the lazy dog";
        assert_eq!(redact_text(text, "***REDACTED***"), text);
    }

    #[test]
    fn test_redact_custom_replacement() {
        let result = redact_text("token=abc", "***");
        assert_eq!(result, "token=***");
    }
}
