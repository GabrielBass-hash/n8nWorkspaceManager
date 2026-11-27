//! GitHub REST API client.
//! Port de `src/n8n_launcher/github/api.py`.

use crate::github::GitHubError;
use reqwest::blocking::{Client, Response};
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, ACCEPT, USER_AGENT};
use serde_json::Value;
use std::time::Duration;

impl From<reqwest::Error> for GitHubError {
    fn from(e: reqwest::Error) -> Self {
        GitHubError {
            status_code: e.status().map(|s| s.as_u16()),
            message: format!("requête GitHub impossible : {e}"),
        }
    }
}

/// Réduit un chemin ``.github/workflows/x.yml`` à son nom de fichier bare.
pub fn workflow_name(workflow_file: &str) -> String {
    workflow_file.rsplitn(2, '/').next().unwrap_or(workflow_file).to_string()
}

/// Construit le chemin d'URL ``owner/repo`` attendu par GitHub.
/// Le slash entre owner et repo reste littéral ; seuls les deux segments sont
/// encodés pour les caractères spéciaux.
pub fn repo_url_path(repo_path: &str) -> String {
    let (owner, repo) = repo_path.split_once('/').unwrap_or((repo_path, ""));
    format!("{}/{}", url_encode(owner), url_encode(repo))
}

/// Encodage URL d'un segment de chemin sans encoder le slash.
fn url_encode(s: &str) -> String {
    urlencoding::encode(s).to_string()
}

/// Client authentifié par token pour l'API REST GitHub.
pub struct GitHubClient {
    base_url: String,
    token: String,
    _timeout: Duration,
    client: Client,
}

impl GitHubClient {
    /// Crée un client pour *token* avec le *timeout* donné.
    pub fn new(token: &str, timeout_secs: u64) -> Result<Self, GitHubError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .build()
            .map_err(|e| GitHubError {
                status_code: None,
                message: format!("impossible de créer le client HTTP : {e}"),
            })?;
        Ok(Self {
            base_url: "https://api.github.com".to_string(),
            token: token.to_string(),
            _timeout: Duration::from_secs(timeout_secs),
            client,
        })
    }

    /// En-têtes envoyés avec chaque requête (auth + version API).
    fn headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(ACCEPT, HeaderValue::from_static("application/vnd.github+json"));
        headers.insert(
            reqwest::header::HeaderName::from_static("x-github-api-version"),
            HeaderValue::from_static("2022-11-28"),
        );
        headers.insert(USER_AGENT, HeaderValue::from_static("n8n-launcher"));
        headers.insert(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {}", self.token)).unwrap(),
        );
        headers
    }

    /// Requête JSON retournant un objet (dict), avec un corps JSON optionnel.
    fn request_json(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<Value, GitHubError> {
        let url = format!("{}{}", self.base_url, path);
        let mut req = self
            .client
            .request(method.parse::<reqwest::Method>().unwrap(), &url)
            .headers(self.headers());
        if let Some(b) = body {
            req = req.json(b);
        }
        let response: Response = req.send().map_err(|e| GitHubError::from(e))?;

        let status = response.status();
        if !status.is_success() {
            return Err(GitHubError {
                status_code: Some(status.as_u16()),
                message: error_message(&response),
            });
        }

        response
            .json::<Value>()
            .map_err(|e| GitHubError {
                status_code: Some(status.as_u16()),
                message: format!("GitHub a renvoyé un JSON illisible : {e}"),
            })
    }

    /// Requête retournant une liste JSON (top-level array).
    fn request_json_list(&self, method: &str, path: &str) -> Result<Value, GitHubError> {
        let value = self.request_json(method, path, None)?;
        if !value.is_array() {
            return Err(GitHubError {
                status_code: None,
                message: "GitHub a renvoyé une réponse inattendue".to_string(),
            });
        }
        Ok(value)
    }

    /// Requête retournant le texte brut (raw body).
    fn request_text(&self, method: &str, path: &str) -> Result<String, GitHubError> {
        let url = format!("{}{}", self.base_url, path);
        let response: Response = self
            .client
            .request(method.parse::<reqwest::Method>().unwrap(), &url)
            .headers(self.headers())
            .send()
            .map_err(|e| GitHubError::from(e))?;

        let status = response.status();
        if !status.is_success() {
            return Err(GitHubError {
                status_code: Some(status.as_u16()),
                message: error_message(&response),
            });
        }

        response.text().map_err(|e| GitHubError::from(e))
    }

    /// Requête dont le succès ne transporte pas de body (dispatch, patch).
    fn _request_action(&self, method: &str, path: &str) -> Result<(), GitHubError> {
        let url = format!("{}{}", self.base_url, path);
        let response: Response = self
            .client
            .request(method.parse::<reqwest::Method>().unwrap(), &url)
            .headers(self.headers())
            .send()
            .map_err(|e| GitHubError::from(e))?;

        let status = response.status();
        if !(status.is_success() || status.as_u16() == 202 || status.as_u16() == 204) {
            return Err(GitHubError {
                status_code: Some(status.as_u16()),
                message: error_message(&response),
            });
        }
        Ok(())
    }
}

/// Retourne l'identifiant du compte authentifié (valide le token).
pub fn github_owner(client: &GitHubClient) -> Result<String, GitHubError> {
    let payload = client.request_json("GET", "/user", None)?;
    let login = payload.get("login").and_then(|v| v.as_str());
    match login {
        Some(login) if !login.is_empty() => Ok(login.to_string()),
        _ => Err(GitHubError {
            status_code: None,
            message: "GitHub n'a pas renvoyé l'identifiant du compte".to_string(),
        }),
    }
}

/// Liste les dépôts visibles pour le compte authentifié.
pub fn list_user_repos(client: &GitHubClient) -> Result<Vec<Value>, GitHubError> {
    let payload = client.request_json_list(
        "GET",
        "/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member",
    )?;
    Ok(payload
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_object().cloned().map(Value::Object))
                .collect()
        })
        .unwrap_or_default())
}

/// Crée un dépôt et retourne son URL HTTPS propre (sans token).
pub fn create_repo(
    client: &GitHubClient,
    name: &str,
    private: bool,
    description: Option<&str>,
    owner: Option<&str>,
) -> Result<String, GitHubError> {
    let account = owner
        .map(|o| o.to_string())
        .or_else(|| github_owner(client).ok())
        .unwrap_or_default();

    let path = match owner {
        Some(o) => format!("/orgs/{}/repos", repo_url_path(o)),
        None => "/user/repos".to_string(),
    };

    let mut body = serde_json::json!({"name": name, "private": private});
    if let Some(desc) = description {
        body.as_object_mut().unwrap().insert("description".to_string(), Value::String(desc.to_string()));
    }
    let _: Value = client.request_json("POST", &path, Some(&body))?;
    Ok(format!("https://github.com/{account}/{name}.git"))
}

/// Liste les branches d'un dépôt.
pub fn list_repo_branches(
    client: &GitHubClient,
    repo_path: &str,
) -> Result<Vec<String>, GitHubError> {
    let payload = client.request_json_list(
        "GET",
        &format!("/repos/{}/branches?per_page=100", repo_url_path(repo_path)),
    )?;
    let mut names: Vec<String> = payload
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| {
                    v.as_object()
                        .and_then(|o| o.get("name"))
                        .and_then(|n| n.as_str())
                        .filter(|n| !n.is_empty())
                        .map(|n| n.to_string())
                })
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    names.dedup();
    Ok(names)
}

/// Retourne les runs récents d'un workflow (du plus récent au plus ancien).
pub fn list_workflow_runs(
    client: &GitHubClient,
    repo_path: &str,
    workflow_file: &str,
) -> Result<Vec<Value>, GitHubError> {
    let workflow_id = urlencoding::encode(&workflow_name(workflow_file)).to_string();
    let payload = client.request_json(
        "GET",
        &format!(
            "/repos/{}/actions/workflows/{}/runs?per_page=20",
            repo_url_path(repo_path),
            workflow_id
        ),
        None,
    )?;
    let runs = payload.get("workflow_runs").and_then(|v| v.as_array());
    match runs {
        Some(arr) => Ok(arr.clone()),
        None => Err(GitHubError {
            status_code: None,
            message: "GitHub n'a pas renvoyé la liste des runs".to_string(),
        }),
    }
}

/// Retourne les jobs d'un run de workflow.
pub fn list_run_jobs(
    client: &GitHubClient,
    repo_path: &str,
    run_id: &str,
) -> Result<Vec<Value>, GitHubError> {
    let payload = client.request_json(
        "GET",
        &format!(
            "/repos/{}/actions/runs/{}/jobs?per_page=100",
            repo_url_path(repo_path),
            run_id
        ),
        None,
    )?;
    let jobs = payload.get("jobs").and_then(|v| v.as_array());
    match jobs {
        Some(arr) => Ok(arr.clone()),
        None => Err(GitHubError {
            status_code: None,
            message: "GitHub n'a pas renvoyé la liste des jobs".to_string(),
        }),
    }
}

/// Télécharge le log brut (text/plain) d'un job terminé.
pub fn fetch_job_logs(
    client: &GitHubClient,
    repo_path: &str,
    job_id: &str,
) -> Result<String, GitHubError> {
    client.request_text(
        "GET",
        &format!(
            "/repos/{}/actions/jobs/{}/logs",
            repo_url_path(repo_path),
            job_id
        ),
    )
}

/// Déclenche un run ``workflow_dispatch`` de *workflow_file* sur *ref*.
pub fn dispatch_workflow(
    client: &GitHubClient,
    repo_path: &str,
    workflow_file: &str,
    ref_: &str,
    inputs: Option<&Value>,
) -> Result<(), GitHubError> {
    let workflow_id = urlencoding::encode(&workflow_name(workflow_file)).to_string();
    let mut payload = serde_json::json!({"ref": ref_});
    if let Some(inputs) = inputs {
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("inputs".to_string(), inputs.clone());
        }
    }
    let _: Value = client.request_json(
        "POST",
        &format!(
            "/repos/{}/actions/workflows/{}/dispatches",
            repo_url_path(repo_path),
            workflow_id
        ),
        Some(&payload),
    )?;
    Ok(())
}

/// Pointe la branche par défaut d'un dépôt vers *branch*.
pub fn set_default_branch(
    client: &GitHubClient,
    repo_path: &str,
    branch: &str,
) -> Result<(), GitHubError> {
    let payload = serde_json::json!({"default_branch": branch});
    let _: Value = client.request_json(
        "PATCH",
        &format!("/repos/{}", repo_url_path(repo_path)),
        Some(&payload),
    )?;
    Ok(())
}

/// Construit un message lisible à partir d'une réponse GitHub non-2xx.
fn error_message(response: &Response) -> String {
    let base = format!("GitHub a refusé la requête (HTTP {})", response.status().as_u16());
    base
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_workflow_name() {
        assert_eq!(workflow_name(".github/workflows/n8n-ci.yml"), "n8n-ci.yml");
        assert_eq!(workflow_name("n8n-ci.yml"), "n8n-ci.yml");
    }

    #[test]
    fn test_repo_url_path() {
        assert_eq!(repo_url_path("owner/repo"), "owner/repo");
        assert_eq!(repo_url_path("my-org/my.repo"), "my-org/my.repo");
    }
}
