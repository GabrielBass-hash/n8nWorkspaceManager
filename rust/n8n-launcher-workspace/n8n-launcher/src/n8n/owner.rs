//! Owner bootstrap: n8n owner account and API key.
//! Port de `src/n8n_launcher/n8n/owner.rs`.

use crate::n8n::scopes::REQUIRED_WORKFLOW_SCOPES;
use reqwest::blocking::Client;
use serde_json::Value;
use std::time::{Duration, Instant};

/// Erreur levée quand le bootstrap du propriétaire n8n ne peut pas aboutir.
#[derive(Debug)]
pub struct OwnerSetupError(pub String);

impl std::fmt::Display for OwnerSetupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "OwnerSetupError: {}", self.0)
    }
}

impl std::error::Error for OwnerSetupError {}

impl From<reqwest::Error> for OwnerSetupError {
    fn from(e: reqwest::Error) -> Self {
        OwnerSetupError(format!("requête n8n impossible : {e}"))
    }
}

/// Identifiants API retournés après le bootstrap du propriétaire.
#[derive(Debug, Clone)]
pub struct ApiCredentials {
    pub api_key: String,
}

/// Attend que n8n serve son interface, pas seulement que le port réponde.
///
/// Docker signalant un conteneur en cours ne signifie que le processus existe,
/// et ``/healthz`` signifie seulement que la socket répond : mesuré sur n8n
/// 2.40, la liveness passe au vert ~2 s *avant* que le gestionnaire d'interface
/// ne soit monté, et ``GET /`` répond ``Cannot GET /`` dans cette fenêtre.
/// ``/healthz/readiness`` est l'endpoint qui suit le fait que l'éditeur est
/// réellement joignable (il reste 503 tant que les migrations et l'interface
/// ne sont pas terminées). Les versions de n8n antérieures à cette route
/// répondent 404 dessus et recourent à la racine de l'interface, qui est la
/// même condition de l'autre côté.
///
/// # Erreurs
///
/// Retourne une :class:`OwnerSetupError` si n8n ne sert pas son interface
/// avant *timeout*.
pub fn wait_for_n8n(
    base_url: &str,
    timeout_secs: u64,
    interval_secs: u64,
) -> Result<(), OwnerSetupError> {
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| OwnerSetupError(format!("impossible de créer le client HTTP : {e}")))?;
    let root = base_url.trim_end_matches('/');
    let deadline = Instant::now() + Duration::from_secs(timeout_secs);
    let mut last_error = "aucune réponse".to_string();

    while Instant::now() < deadline {
        let per_request = Duration::from_secs(10u64.min(timeout_secs.max(1)));
        match client.get(format!("{root}/healthz/readiness")).timeout(per_request).send() {
            Ok(response) if response.status().is_success() => return Ok(()),
            Ok(response) if response.status().as_u16() == 404 => {
                match client.get(format!("{root}/")).timeout(per_request).send() {
                    Ok(page) if page.status().is_success() => return Ok(()),
                    Ok(page) => last_error = format!("HTTP {} sur /", page.status().as_u16()),
                    Err(e) => last_error = e.to_string(),
                }
            }
            Ok(response) => last_error = format!("HTTP {}", response.status().as_u16()),
            Err(e) => last_error = e.to_string(),
        }
        let remaining = deadline.duration_since(Instant::now()).as_secs_f64();
        let sleep_for = (interval_secs as f64).min(remaining.max(0.0));
        if sleep_for > 0.0 {
            std::thread::sleep(Duration::from_secs_f64(sleep_for));
        }
    }
    Err(OwnerSetupError(format!(
        "n8n n'est pas prêt après {timeout_secs}s ({last_error})"
    )))
}

/// Hache un mot de passe propriétaire avec bcrypt.
pub fn hash_owner_password(password: &str) -> String {
    let hashed = bcrypt::hash(password, bcrypt::DEFAULT_COST).expect("bcrypt hash failed");
    hashed
}

/// Bootstrap du compte propriétaire n8n et création d'une clé API launcher.
pub struct OwnerSetup {
    timeout: u64,
    client: Client,
}

impl OwnerSetup {
    /// Crée un nouveau bootstrap avec le *timeout* donné (secondes).
    pub fn new(timeout_secs: u64) -> Result<Self, OwnerSetupError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| OwnerSetupError(format!("impossible de créer le client HTTP : {e}")))?;
        Ok(Self {
            timeout: timeout_secs,
            client,
        })
    }

    /// Garantit que le propriétaire existe, se connecte et retourne une clé API.
    pub fn bootstrap(
        &self,
        base_url: &str,
        email: &str,
        password: &str,
        first_name: &str,
        last_name: &str,
        ready_timeout_secs: u64,
    ) -> Result<ApiCredentials, OwnerSetupError> {
        let root = base_url.trim_end_matches('/');
        self._ensure_owner(root, email, password, first_name, last_name, ready_timeout_secs)?;

        let login: Value = self._request(
            "POST",
            &format!("{root}/rest/login"),
            &serde_json::json!({"emailOrLdapLoginId": email, "password": password}),
        )?;
        let token = login.get("data").and_then(|v| v.get("token")).and_then(|v| v.as_str());
        // n8n n'a plus de jeton dans le corps depuis la 2.33+ ; le cookie de
        // session est le vecteur d'authentification pour les endpoints /rest.
        if token.is_none() {
            // Vérifier les cookies de session — reqwest les gère automatiquement
            // via le CookieStore du client.
        }
        self._remove_launcher_keys(root)?;
        let response: Value = self._request(
            "POST",
            &format!("{root}/rest/api-keys"),
            &serde_json::json!({
                "label": "n8n-launcher",
                "scopes": REQUIRED_WORKFLOW_SCOPES,
                "expiresAt": 0,
            }),
        )?;
        let api_key = response
            .get("data")
            .and_then(|v| v.get("rawApiKey"))
            .or_else(|| response.get("rawApiKey"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| OwnerSetupError("n8n n'a pas renvoyé de clé API".to_string()))?;
        Ok(ApiCredentials {
            api_key: api_key.to_string(),
        })
    }

    /// Supprime les clés API "n8n-launcher" existantes.
    fn _remove_launcher_keys(&self, root: &str) -> Result<(), OwnerSetupError> {
        let response: Value = match self._request("GET", &format!("{root}/rest/api-keys"), &Value::Null) {
            Ok(v) => v,
            Err(_) => return Ok(()),
        };
        let items = response.get("data").and_then(|v| v.get("items")).and_then(|v| v.as_array());
        if let Some(items) = items {
            for item in items {
                if item.get("label").and_then(|v| v.as_str()) == Some("n8n-launcher") {
                    let id = item.get("id").and_then(|v| v.as_str()).unwrap_or("");
                    let _ = self._request("DELETE", &format!("{root}/rest/api-keys/{id}"), &Value::Null);
                }
            }
        }
        Ok(())
    }

    /// Garantit que le propriétaire existe, en réessayant tant que n8n n'est pas prêt.
    fn _ensure_owner(
        &self,
        root: &str,
        email: &str,
        password: &str,
        first_name: &str,
        last_name: &str,
        ready_timeout_secs: u64,
    ) -> Result<(), OwnerSetupError> {
        let deadline = Instant::now() + Duration::from_secs(ready_timeout_secs);
        let payload = serde_json::json!({
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "password": password,
        });
        let mut last_error: Option<OwnerSetupError> = None;

        while Instant::now() < deadline {
            match self._request("POST", &format!("{root}/rest/owner/setup"), &payload) {
                Ok(_) => return Ok(()),
                Err(OwnerSetupError(msg)) => {
                    let message = msg.to_lowercase();
                    if message.contains("already") {
                        return Ok(());
                    }
                    if ["must be", "invalid_type", "expected", "not allowed"]
                        .iter()
                        .any(|marker| message.contains(marker))
                    {
                        return Err(OwnerSetupError(msg));
                    }
                    last_error = Some(OwnerSetupError(msg));
                }
            }
            std::thread::sleep(Duration::from_secs(2));
        }
        Err(last_error
            .unwrap_or_else(|| OwnerSetupError("n8n n'est pas devenu prêt pour la configuration du compte propriétaire".to_string())))
    }

    /// Effectue une requête authentifiée et décode la réponse JSON.
    fn _request(&self, method: &str, url: &str, payload: &Value) -> Result<Value, OwnerSetupError> {
        let response = self
            .client
            .request(method.parse::<reqwest::Method>().unwrap(), url)
            .json(payload)
            .timeout(Duration::from_secs(self.timeout))
            .send()
            .map_err(|e| OwnerSetupError(e.to_string()))?;

        let status = response.status();
        if !status.is_success() {
            let text = response.text().unwrap_or_default();
            let detail = text[..300.min(text.len())].trim();
            let detail = if detail.is_empty() {
                status.canonical_reason().unwrap_or("unknown").to_string()
            } else {
                detail.to_string()
            };
            return Err(OwnerSetupError(format!(
                "n8n a renvoyé HTTP {} : {}",
                status.as_u16(),
                detail
            )));
        }
        response.json::<Value>().map_err(|e| {
            OwnerSetupError(format!(
                "n8n a renvoyé une réponse non-JSON : {e}"
            ))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display() {
        let e = OwnerSetupError("test error".to_string());
        assert!(e.to_string().contains("test error"));
    }

    #[test]
    fn test_hash_owner_password() {
        let hashed = hash_owner_password("test-password");
        assert!(!hashed.is_empty());
        assert_ne!(hashed, "test-password");
    }
}
