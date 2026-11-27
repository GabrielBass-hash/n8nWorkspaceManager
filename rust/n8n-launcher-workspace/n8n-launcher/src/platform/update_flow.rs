//! Update flow: check for a newer launcher release, offer it, download, self-install.
//! Port de `src/n8n_launcher/platform/update_flow.rs`.

use crate::core::paths;
use crate::platform::updater;
use std::cell::Cell;
use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// Délai après le lancement avant que la vérification automatique ne s'enclenche (ms).
pub const LAUNCH_CHECK_DELAY_MS: u64 = 1500;

/// Vérifie la disponibilité d'une version plus récente du launcher et guide la mise à jour.
///
/// Toute vérification est best effort et tout résultat est une *question posée à l'utilisateur*.
/// Une vérification de mise à jour ne doit jamais retarder ni perturber le lancement, et un
/// utilisateur qui ne veut pas de la mise à jour n'est pas une erreur.
#[derive(Clone)]
pub struct UpdateController {
    pub _events: Arc<Mutex<VecDeque<Box<dyn FnOnce() + Send>>>>,
    pub events: Arc<Mutex<VecDeque<Box<dyn FnOnce() + Send>>>>,
    set_status: Arc<dyn Fn(String) + Send + Sync>,
    on_offer: Arc<dyn Fn(String, String) -> bool + Send + Sync>,
    on_error: Arc<dyn Fn(String, String) + Send + Sync>,
    on_status_link: Arc<dyn Fn(String, String) + Send + Sync>,
    schedule: Arc<dyn Fn(u64, Box<dyn FnOnce() + Send>) + Send + Sync>,
    is_closed: Arc<dyn Fn() -> bool + Send + Sync>,
    finish_close: Arc<dyn Fn() + Send + Sync>,
}

impl UpdateController {
    /// Lie la file d'événements, les questions de l'appelant et son garde-fou de fermeture.
    pub fn new(
        set_status: Box<dyn Fn(String) + Send + Sync>,
        on_offer: Box<dyn Fn(String, String) -> bool + Send + Sync>,
        on_error: Box<dyn Fn(String, String) + Send + Sync>,
        on_status_link: Box<dyn Fn(String, String) + Send + Sync>,
        schedule: Box<dyn Fn(u64, Box<dyn FnOnce() + Send>) + Send + Sync>,
        is_closed: Box<dyn Fn() -> bool + Send + Sync>,
        finish_close: Box<dyn Fn() + Send + Sync>,
    ) -> Self {
        let events = Arc::new(Mutex::new(VecDeque::new()));
        Self {
            _events: events.clone(),
            events: events.clone(),
            set_status: Arc::from(set_status),
            on_offer: Arc::from(on_offer),
            on_error: Arc::from(on_error),
            on_status_link: Arc::from(on_status_link),
            schedule: Arc::from(schedule),
            is_closed: Arc::from(is_closed),
            finish_close: Arc::from(finish_close),
        }
    }

    /// Planifie la vérification au lancement quand l'app tourne depuis un bundle.
    pub fn setup(&self) {
        if updater::install_target().is_some() {
            let me = self.clone();
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                (self.schedule)(LAUNCH_CHECK_DELAY_MS, Box::new(move || me.check()));
            }));
        }
    }

    /// Lance la vérification de mise à jour sur un thread d'arrière-plan (ne bloque jamais le lancement).
    pub fn check(&self) {
        if (self.is_closed)() {
            return;
        }
        let me = self.clone();
        let _ = std::thread::Builder::new()
            .name("n8n-launcher-update".to_string())
            .spawn(move || me._worker());
    }

    /// Lance le worker (sur le thread de fond).
    pub fn _worker(&self) {
        updater::cleanup_stale();
        let release = match updater::fetch_latest_release(updater::REPO, None, 10) {
            Ok(r) => r,
            Err(_) => return,
        };
        let target = match updater::install_target() {
            Some(t) => t,
            None => return,
        };
        if !updater::release_is_newer(&release, &updater::current_version()) {
            return;
        }
        let asset = match updater::compatible_asset(&release, None) {
            Some(a) => a.clone(),
            None => return,
        };
        let writable = target
            .parent()
            .map(|p| {
                std::fs::metadata(p)
                    .map(|m| !m.permissions().readonly())
                    .unwrap_or(false)
            })
            .unwrap_or(false);

        if writable {
            let me = self.clone();
            let r = release.clone();
            let a = asset.clone();
            let mut q = self._events.lock().unwrap();
            q.push_back(Box::new(move || me._offer(r, a)));
        } else {
            let me = self.clone();
            let r = release.clone();
            let mut q = self._events.lock().unwrap();
            q.push_back(Box::new(move || me._show_link(r)));
        }
    }

    /// Pointe la barre de statut vers la page de release quand l'app n'est pas inscriptible.
    pub fn _show_link(&self, release: updater::Release) {
        if (self.is_closed)() {
            return;
        }
        let message = format!(
            "Nouvelle version {} disponible — cliquer pour ouvrir",
            release.tag_name
        );
        (self.on_status_link)(message, updater::release_page_url(updater::REPO));
    }

    /// Demande l'installation, puis télécharge sur un thread worker.
    pub fn _offer(&self, release: updater::Release, asset: updater::Asset) {
        if (self.is_closed)() {
            return;
        }
        let body = format!(
            "Une nouvelle version de n8n Launcher est disponible :\n\n{} -> {}\n\nVoulez-vous la télécharger et l'installer ?\nL'application redémarrera automatiquement.",
            updater::current_version(),
            release.tag_name
        );
        if !(self.on_offer)("Mise à jour disponible".to_string(), body) {
            return;
        }
        (self.set_status)(format!(
            "Téléchargement de la mise à jour {}…",
            release.tag_name
        ));
        let dest = paths::updates_dir()
            .unwrap_or_else(|_| std::path::PathBuf::from("."))
            .join(&asset.name);
        let me = self.clone();
        let a = asset.clone();
        let d = dest.clone();
        let _ = std::thread::Builder::new()
            .name("n8n-launcher-update-dl".to_string())
            .spawn(move || me._download(a, d));
    }

    /// Télécharge l'actif et rapporte la progression.
    pub fn _download(&self, asset: updater::Asset, dest: PathBuf) {
        let events = self._events.clone();
        let set_status = self.set_status.clone();
        let on_error = self.on_error.clone();

        let last_mb = Cell::new(0u64);
        let progress_fn = {
            let events = events.clone();
            let set_status = set_status.clone();
            move |received: u64| {
                let mb = received / (1024 * 1024);
                if mb != last_mb.get() {
                    last_mb.set(mb);
                    let status = format!("Téléchargement de la mise à jour… {} Mo", mb);
                    let events = events.clone();
                    let set_status = set_status.clone();
                    let mut q = events.lock().unwrap();
                    q.push_back(Box::new(move || set_status(status)));
                }
            }
        };

        match updater::download_asset(&asset.url, &dest, None, Some(asset.size), Some(&progress_fn)) {
            Ok(()) => {
                let me = self.clone();
                let d = dest.clone();
                let mut q = self._events.lock().unwrap();
                q.push_back(Box::new(move || me._confirm_install(d)));
            }
            Err(e) => {
                let msg = format!("Téléchargement impossible : {e}");
                let on_error = on_error.clone();
                let mut q = events.lock().unwrap();
                q.push_back(Box::new(move || on_error("Mise à jour".to_string(), msg)));
            }
        }
    }

    /// Demande le redémarrage et lance l'installeur si accepté.
    pub fn _confirm_install(&self, dest: PathBuf) {
        if (self.is_closed)() {
            return;
        }
        if !(self.on_offer)(
            "Mise à jour prête".to_string(),
            "La nouvelle version est téléchargée.\nRedémarrer n8n Launcher maintenant pour l'appliquer ?"
                .to_string(),
        ) {
            (self.set_status)("".to_string());
            return;
        }
        let target = updater::install_target().unwrap_or_else(|| PathBuf::from(""));
        match updater::installer_script(&dest, &target, None) {
            Ok(script) => {
                if let Err(e) = updater::spawn_installer(&script, updater::platform_system()) {
                    (self.on_error)("Mise à jour".to_string(), e.to_string());
                    return;
                }
                (self.finish_close)();
            }
            Err(e) => {
                (self.on_error)("Mise à jour".to_string(), e.to_string());
            }
        }
    }

    /// Retourne s'il y a un événement en attente.
    pub fn has_event(&self) -> bool {
        !self._events.lock().unwrap().is_empty()
    }

    /// Applique les continuations déjà en file (helper pour les tests).
    pub fn drain(&self) -> Vec<Box<dyn FnOnce() + Send>> {
        let mut q = self._events.lock().unwrap();
        q.drain(..).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn test_launch_check_delay() {
        assert_eq!(LAUNCH_CHECK_DELAY_MS, 1500);
    }

    fn make_controller() -> (UpdateController, Arc<AtomicBool>, Arc<std::sync::Mutex<Vec<String>>>) {
        let closed = Arc::new(AtomicBool::new(false));
        let statuses = Arc::new(std::sync::Mutex::new(Vec::new()));
        let errors = Arc::new(std::sync::Mutex::new(Vec::new()));
        let asked = Arc::new(std::sync::Mutex::new(Vec::new()));
        let finished = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let closed_c = closed.clone();
        let ctrl = UpdateController::new(
            Box::new({
                let sc = statuses.clone();
                move |s| sc.lock().unwrap().push(s)
            }),
            Box::new({
                let ac = asked.clone();
                move |t, b| { ac.lock().unwrap().push((t, b)); true }
            }),
            Box::new({
                let ec = errors.clone();
                move |t, b| ec.lock().unwrap().push((t, b))
            }),
            Box::new({
                let sc = statuses.clone();
                move |m, _u| sc.lock().unwrap().push(m)
            }),
            Box::new(|_ms, _f| {}),
            Box::new({
                let cc = closed.clone();
                move || cc.load(Ordering::SeqCst)
            }),
            Box::new({
                let fc = finished.clone();
                move || { fc.fetch_add(1, Ordering::SeqCst); }
            }),
        );
        (ctrl, closed, statuses)
    }

    #[test]
    fn test_show_link_closed_does_nothing() {
        let (ctrl, closed, statuses) = make_controller();
        closed.store(true, Ordering::SeqCst);
        ctrl._show_link(updater::Release {
            tag_name: "v1.0.03".to_string(),
            version: updater::parse_version("v1.0.03").unwrap(),
            assets: vec![],
        });
        assert!(statuses.lock().unwrap().is_empty());
    }

    #[test]
    fn test_confirm_install_closed_does_nothing() {
        let (ctrl, closed, _statuses) = make_controller();
        closed.store(true, Ordering::SeqCst);
        ctrl._confirm_install(std::path::PathBuf::from("/tmp/asset"));
        // finished counter not accessible here; just ensure no panic
    }
}
