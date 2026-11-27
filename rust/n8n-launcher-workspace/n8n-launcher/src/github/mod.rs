//! GitHub module: REST client and auth.
//! Port de `src/n8n_launcher/github/`.

pub mod api;
pub mod auth;

#[derive(Debug)]
pub struct GitHubError {
    pub status_code: Option<u16>,
    pub message: String,
}

impl std::fmt::Display for GitHubError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "GitHubError({:?}): {}", self.status_code, self.message)
    }
}

impl std::error::Error for GitHubError {}
