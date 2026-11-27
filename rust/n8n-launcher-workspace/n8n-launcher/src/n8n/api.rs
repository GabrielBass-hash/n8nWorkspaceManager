//! n8n public API HTTP client.
//! Port de `src/n8n_launcher/n8n/api.rs`.

use reqwest::blocking::Client;
use serde_json::Value;
use std::time::Duration;

/// Erreur levée quand n8n retourne une réponse API non satisfaite.
#[derive(Debug)]
pub struct N8nApiError {
    pub status_code: Option<u16>,
    pub message: String,
}

impl std::fmt::Display for N8nApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "N8nApiError({:?}): {}", self.status_code, self.message)
    }
}

impl std::error::Error for N8nApiError {}

impl From<reqwest::Error> for N8nApiError {
    fn from(e: reqwest::Error) -> Self {
        N8nApiError {
            status_code: e.status().map(|s| s.as_u16()),
            message: format!("requête n8n impossible : {e}"),
        }
    }
}

/// Client HTTP authentifié pour l'API publique REST de n8n.
///
/// Toute requête porte l'en-tête ``X-N8N-API-KEY`` ; les erreurs sont enveloppées
/// dans :class:`N8nApiError` avec le code HTTP préservé pour la gestion 401/403.
#[derive(Clone)]
pub struct N8nApiClient {
    base_url: String,
    api_key: String,
    _timeout: Duration,
    client: Client,
}

impl N8nApiClient {
    /// Crée un client pour *base_url* et *api_key* avec le *timeout* donné.
    pub fn new(base_url: &str, api_key: &str, timeout_secs: u64) -> Result<Self, N8nApiError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| N8nApiError {
                status_code: None,
                message: format!("impossible de créer le client HTTP : {e}"),
            })?;
        Ok(Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            _timeout: Duration::from_secs(timeout_secs),
            client,
        })
    }

    /// Retourne tous les workflows visibles pour la clé API.
    pub fn list_workflows(&self) -> Result<Vec<Value>, N8nApiError> {
        let payload: Value = self._request("GET", "/workflows", None, None, false)?;
        Ok(payload
            .get("data")
            .and_then(|v| v.as_array())
            .map(|arr| arr.clone())
            .unwrap_or_default())
    }

    /// Retourne l'export complet d'un workflow par son id.
    pub fn get_workflow(&self, workflow_id: &str) -> Result<Value, N8nApiError> {
        self._request("GET", &format!("/workflows/{workflow_id}"), None, None, false)
    }

    /// Crée un workflow à partir d'une charge d'export.
    pub fn create_workflow(&self, workflow: &Value) -> Result<Value, N8nApiError> {
        self._request("POST", "/workflows", Some(workflow), None, false)
    }

    /// Remplace un workflow existant par son id.
    pub fn update_workflow(&self, workflow_id: &str, workflow: &Value) -> Result<Value, N8nApiError> {
        self._request(
            "PUT",
            &format!("/workflows/{workflow_id}"),
            Some(workflow),
            None,
            false,
        )
    }

    /// Supprime un workflow par son id.
    pub fn delete_workflow(&self, workflow_id: &str) -> Result<(), N8nApiError> {
        let _: Value = self._request("DELETE", &format!("/workflows/{workflow_id}"), None, None, false)?;
        Ok(())
    }

    /// Active ou désactive les déclencheurs d'un workflow.
    pub fn activate_workflow(&self, workflow_id: &str, active: bool) -> Result<Value, N8nApiError> {
        let action = if active { "activate" } else { "deactivate" };
        self._request("POST", &format!("/workflows/{workflow_id}/{action}"), None, None, false)
    }

    /// Retourne tous les credentials ; une liste manquante est traitée comme vide.
    pub fn list_credentials(&self) -> Result<Vec<Value>, N8nApiError> {
        let payload: Value = self._request("GET", "/credentials", None, None, true)?;
        Ok(payload
            .get("data")
            .and_then(|v| v.as_array())
            .map(|arr| arr.clone())
            .unwrap_or_default())
    }

    /// Retourne un seul credential, y compris ses données ``data`` secrètes.
    pub fn get_credential(&self, credential_id: &str) -> Result<Value, N8nApiError> {
        self._request("GET", &format!("/credentials/{credential_id}"), None, None, false)
    }

    /// Crée un nouveau credential.
    pub fn create_credential(&self, credential: &Value) -> Result<Value, N8nApiError> {
        self._request("POST", "/credentials", Some(credential), None, false)
    }

    /// Retourne un credential existant par son nom ou en crée un Postgres.
    pub fn ensure_postgres_credential(
        &self,
        name: &str,
        host: &str,
        port: u16,
        database: &str,
        user: &str,
        password: &str,
    ) -> Result<Value, N8nApiError> {
        for credential in self.list_credentials()? {
            if credential.get("name").and_then(|v| v.as_str()) == Some(name) {
                return Ok(credential);
            }
        }
        self.create_credential(&serde_json::json!({
            "name": name,
            "type": "postgres",
            "data": {
                "host": host,
                "port": port,
                "database": database,
                "user": user,
                "password": password,
                "ssl": "disable",
            },
        }))
    }

    /// Effectue une requête authentifiée et décode la réponse JSON.
    ///
    /// ``ignore_not_found=true`` transforme une réponse 404 en ``{}`` vide au
    /// lieu de lever une erreur, ce sur quoi plusieurs appelants comptent.
    fn _request(
        &self,
        method: &str,
        path: &str,
        json: Option<&Value>,
        params: Option<&Value>,
        ignore_not_found: bool,
    ) -> Result<Value, N8nApiError> {
        let url = format!("{}{}", self.base_url, path);
        let mut req = self
            .client
            .request(method.parse::<reqwest::Method>().unwrap(), &url)
            .header("X-N8N-API-KEY", &self.api_key)
            .header("Accept", "application/json");
        if let Some(j) = json {
            req = req.json(j);
        }
        if let Some(p) = params {
            req = req.query(&p);
        }
        let response = req.send().map_err(|e| N8nApiError::from(e))?;

        let status = response.status();
        if !status.is_success() {
            if ignore_not_found && status.as_u16() == 404 {
                return Ok(serde_json::json!({}));
            }
            let text = response.text().unwrap_or_default();
            let detail = &text[..500.min(text.len())];
            return Err(N8nApiError {
                status_code: Some(status.as_u16()),
                message: format!("n8n API a retourné {}: {}", status.as_u16(), detail),
            });
        }
        if status.as_u16() == 204 {
            return Ok(serde_json::json!({}));
        }
        response.json::<Value>().map_err(|e| N8nApiError {
            status_code: Some(status.as_u16()),
            message: format!("n8n API a retourné un JSON invalide : {e}"),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_display() {
        let e = N8nApiError {
            status_code: Some(401),
            message: "unauthorized".to_string(),
        };
        assert!(e.to_string().contains("401"));
        assert!(e.to_string().contains("unauthorized"));
    }
}
