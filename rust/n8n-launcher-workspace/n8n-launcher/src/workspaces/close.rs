//! Ordered shutdown sequence: export workflows, sync git, then stop.
//! Port de `src/n8n_launcher/workspaces/close.py`.

use std::sync::Arc;

use crate::core::models::{Workspace, WorkspaceState};
use crate::workspaces::manager::WorkspaceManager;

/// Ce que l'appelant veut être demandé, dit et montré pendant la fermeture.
///
/// Chaque défaut est la réponse silencieuse en mode sans tête : ne rien signaler,
/// ne rien échouer, continuer. Un appelant qui veut une boîte de dialogue implémente
/// les deux hooks ``on_*_failed`` ; un appelant qui veut du progrès implémente
/// :attr:`on_progress`.
#[derive(Default)]
pub struct CloseHooks {
    /// Appelé avec une phrase humaine décrivant l'étape sur le point d'exécuter.
    pub on_progress: Option<Box<dyn Fn(String)>>,
    /// Retourne True pour réessayer l'export, False pour s'arrêter sans synchro,
    /// ni l'un ni l'autre (None) pour avorter toute la fermeture.
    pub on_sync_failed: Option<Box<dyn Fn(Workspace, String) -> Option<bool>>>,
    /// Retourne True pour réessayer l'arrêt, False pour l'ignorer et continuer,
    /// None pour avorter.
    pub on_stop_failed: Option<Box<dyn Fn(Workspace, String) -> Option<bool>>>,
    /// Appelé après une synchro validée localement mais non poussée.
    pub on_push_failed: Option<Box<dyn Fn(Workspace)>>,
}

impl CloseHooks {
    pub fn new() -> Self {
        Self::default()
    }
}

/// Exécute le flux de fermeture ordonné pour chaque workspace.
///
/// Construit avec un manager et les hooks de l'appelant. :meth:`run` est
/// synchrone : les étapes sont des opérations SSH et Docker, donc bloquer est le
/// défaut honnête.
pub struct CloseSequence {
    manager: Arc<WorkspaceManager>,
    hooks: CloseHooks,
}

impl CloseSequence {
    pub fn new(manager: &WorkspaceManager, hooks: CloseHooks) -> Self {
        Self {
            manager: Arc::new((*manager).clone()),
            hooks,
        }
    }

    /// Ferme chaque workspace ; retourne False si l'utilisateur a avorté.
    pub fn run(&self) -> bool {
        let workspaces = self.manager.list();
        if workspaces.is_empty() {
            return true;
        }
        if let Some(on_progress) = &self.hooks.on_progress {
            on_progress(
                "Fermeture : synchronisation des workflows puis arrêt de n8n…".to_string(),
            );
        }
        // Reconcile first so decisions are made on real state.
        let _ = self.manager.reconcile_all();
        self._next(&self.manager.list())
    }

    fn _next(&self, workspaces: &[Workspace]) -> bool {
        if workspaces.is_empty() {
            return true;
        }
        let (workspace, rest) = workspaces.split_first().unwrap();

        // Only a running workspace with a key can be exported from.
        if workspace.state == WorkspaceState::RUNNING && workspace.api_key.is_some() {
            return self._sync(workspace, rest);
        }
        self._stop(workspace, rest)
    }

    fn _sync(&self, workspace: &Workspace, rest: &[Workspace]) -> bool {
        if let Some(on_progress) = &self.hooks.on_progress {
            on_progress(format!("Fermeture : synchronisation de « {} »…", workspace.name));
        }
        match self._export(workspace) {
            Ok(()) => {
                match self.manager.sync_git(workspace, true) {
                    Ok(()) => {}
                    Err(_) => {
                        return self._after_sync_failed(workspace, rest, "git sync failed".to_string());
                    }
                }
            }
            Err(_) => {
                return self._after_sync_failed(workspace, rest, "export failed".to_string());
            }
        }
        if workspace.git_push_failed {
            if let Some(on_push_failed) = &self.hooks.on_push_failed {
                on_push_failed(workspace.clone());
            }
        }
        self._stop(workspace, rest)
    }

    fn _export(&self, workspace: &Workspace) -> Result<(), String> {
        if workspace.state != WorkspaceState::RUNNING || workspace.api_key.is_none() {
            return Ok(());
        }
        let api_key = workspace.api_key.clone().unwrap();
        let base_url = format!("http://localhost:{}", workspace.port);
        let client = crate::n8n::api::N8nApiClient::new(&base_url, &api_key, 30).map_err(|e| e.to_string())?;
        let pipelines_dir = workspace.workflows_dir.join("n8nPipelines");
        let _ = std::fs::create_dir_all(&pipelines_dir);
        let _ = crate::n8n::workflows::export_all(
            &client,
            &pipelines_dir,
            Some(&workspace.workflows_dir),
        );
        Ok(())
    }

    fn _stop(&self, workspace: &Workspace, rest: &[Workspace]) -> bool {
        if let Some(on_progress) = &self.hooks.on_progress {
            on_progress(format!("Fermeture : arrêt de « {} »…", workspace.name));
        }
        match self.manager.stop(&workspace.id) {
            Ok(_) => self._next(rest),
            Err(_) => self._after_stop_failed(workspace, rest, "stop failed".to_string()),
        }
    }

    fn _after_sync_failed(
        &self,
        workspace: &Workspace,
        rest: &[Workspace],
        error: String,
    ) -> bool {
        if let Some(on_sync_failed) = &self.hooks.on_sync_failed {
            match on_sync_failed(workspace.clone(), error) {
                Some(true) => return self._sync(workspace, rest),
                Some(false) => return self._stop(workspace, rest),
                None => return false,
            }
        }
        false
    }

    fn _after_stop_failed(
        &self,
        workspace: &Workspace,
        rest: &[Workspace],
        error: String,
    ) -> bool {
        if let Some(on_stop_failed) = &self.hooks.on_stop_failed {
            match on_stop_failed(workspace.clone(), error) {
                Some(true) => return self._stop(workspace, rest),
                Some(false) => {
                    if let Some(on_progress) = &self.hooks.on_progress {
                        on_progress(format!("Fermeture : arrêt de « {} » ignoré.", workspace.name));
                    }
                    return self._next(rest);
                }
                None => return false,
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_close_hooks_default() {
        let hooks = CloseHooks::new();
        assert!(hooks.on_progress.is_none());
        assert!(hooks.on_sync_failed.is_none());
        assert!(hooks.on_stop_failed.is_none());
        assert!(hooks.on_push_failed.is_none());
    }
}
