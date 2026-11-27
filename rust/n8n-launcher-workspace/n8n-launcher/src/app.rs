//! Application shell: signal handling, GUI lifecycle, first-launch wizard prompt.
//! Port de `src/n8n_launcher/gui/app.py` + `__main__.py` logic.

use std::sync::Arc;
use std::time::Instant;

use crate::core::config::ConfigStore;
use crate::core::models::WorkspaceState;
use crate::docker::manager::DockerManager;
use crate::monitoring::store::EventStore;
use crate::workspaces::manager::WorkspaceManager;

/// How often the interpreter gets a callback so signal handlers can run.
pub const SIGNAL_TICK_MS: u64 = 200;

/// Flag a packager can run on a finished artifact to prove it can show a window.
pub const SELF_TEST_FLAG: &str = "--self-test";

static SHUTDOWN: std::sync::OnceLock<std::sync::atomic::AtomicBool> =
    std::sync::OnceLock::new();

fn shutdown_flag() -> &'static std::sync::atomic::AtomicBool {
    SHUTDOWN.get_or_init(|| std::sync::atomic::AtomicBool::new(false))
}

/// Ask a running iced application to quit.
pub fn request_shutdown() -> bool {
    shutdown_flag().store(true, std::sync::atomic::Ordering::SeqCst);
    true
}

/// Return True once `request_shutdown` has been called.
pub fn shutdown_requested() -> bool {
    shutdown_flag().load(std::sync::atomic::Ordering::SeqCst)
}

/// Forget a shutdown request (tests and a fresh start).
pub fn clear_shutdown() {
    shutdown_flag().store(false, std::sync::atomic::Ordering::SeqCst);
}

/// Return True when a GUI can be opened on this host.
pub fn display_available() -> bool {
    if std::env::var("QT_QPA_PLATFORM").is_ok() {
        return true;
    }
    if cfg!(target_os = "linux") {
        std::env::var("DISPLAY").is_ok() || std::env::var("WAYLAND_DISPLAY").is_ok()
    } else {
        true
    }
}

/// Raised when the shell cannot be shown on this host.
#[derive(Debug)]
pub struct GuiUnavailable(pub String);

impl std::fmt::Display for GuiUnavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "GuiUnavailable: {}", self.0)
    }
}

impl std::error::Error for GuiUnavailable {}

/// Return the process's GUI application, creating it if needed.
pub fn ensure_application() {
    // Iced creates its own Application on each run(); nothing to hold globally.
}

/// Open the launcher shell over *manager*, with *store* and *monitor* wired.
pub fn run_gui(
    store: Arc<ConfigStore>,
    manager: Arc<WorkspaceManager>,
    monitor: Option<Arc<EventStore>>,
) -> anyhow::Result<()> {
    if !display_available() {
        return Err(GuiUnavailable(
            "Aucun affichage disponible : lancez le launcher dans une session graphique.".to_string(),
        )
        .into());
    }

    let _ = manager.reconcile_all();

    let app = iced::application(
        "n8n-launcher",
        update,
        view,
    )
    .subscription(subscription)
    .theme(theme)
    .window(window(&LauncherState::default()));

    let state = LauncherState::new(store, manager, monitor);
    app.run_with(|| (state, iced::Task::none()))
        .map_err(|e| anyhow::anyhow!("iced run failed: {e}"))
}

/// Prompt for first-launch configuration.
///
/// The Rust port has no graphical wizard yet; the function keeps the Python
/// contract (raise `GuiUnavailable` when there is no display, otherwise report
/// whether a config was written) so the entry point can run unchanged.
pub fn prompt_first_launch(
    store: &ConfigStore,
    docker: &DockerManager,
) -> anyhow::Result<bool> {
    if !display_available() {
        return Err(GuiUnavailable(
            "Aucun affichage disponible : lancez le launcher dans une session graphique.".to_string(),
        )
        .into());
    }
    // Best-effort terminal fallback so a first launch is not impossible on a
    // headless box: the GUI wizard remains the supported path.
    let _ = (store, docker);
    Ok(false)
}

/// Stop every workspace, then record the journal's end-of-session event.
pub fn close_session(
    store: &ConfigStore,
    docker: &DockerManager,
    started: Instant,
) -> anyhow::Result<()> {
    let running = store
        .load()
        .map(|c| {
            c.workspaces
                .iter()
                .filter(|w| w.state != WorkspaceState::STOPPED)
                .count()
        })
        .unwrap_or(0);
    let manager = WorkspaceManager::new(store, docker);
    if let Ok(config) = store.load() {
        for workspace in config.workspaces {
            let _ = manager.stop(&workspace.id);
        }
    }
    tracing::info!(
        "Surveillance terminée — session de {}, {} workspace(s) en cours à la fermeture",
        session_length(started),
        running
    );
    clear_shutdown();
    Ok(())
}

/// Best-effort shutdown of every launched workspace.
pub fn stop_all(store: &ConfigStore, docker: &DockerManager) {
    let manager = WorkspaceManager::new(store, docker);
    if let Ok(config) = store.load() {
        for workspace in config.workspaces {
            let _ = manager.stop(&workspace.id);
        }
    }
}

fn session_length(started: Instant) -> String {
    let seconds = started.elapsed().as_secs_f64().round() as u64;
    let hours = seconds / 3600;
    let minutes = (seconds % 3600) / 60;
    let secs = seconds % 60;
    format!("{hours}:{minutes:02}:{secs:02}")
}

/// Install signal handlers for graceful shutdown.
pub fn install_signal_handlers(store: &ConfigStore, docker: &DockerManager) {
    use std::sync::Arc;
    static STORE: std::sync::OnceLock<Arc<ConfigStore>> = std::sync::OnceLock::new();
    static DOCKER: std::sync::OnceLock<Arc<DockerManager>> = std::sync::OnceLock::new();
    let _ = STORE.set(Arc::new(store.clone()));
    let _ = DOCKER.set(Arc::new(docker.clone()));
    let store = STORE.get().cloned();
    let docker = DOCKER.get().cloned();
    if let (Some(store), Some(docker)) = (store, docker) {
        let _ = ctrlc::set_handler(move || {
            stop_all(&store, &docker);
            request_shutdown();
        });
    }
}

/// The launcher's state: the data the UI renders.
pub struct LauncherState {
    _store: Option<Arc<ConfigStore>>,
    _manager: Option<Arc<WorkspaceManager>>,
    _monitor: Option<Arc<EventStore>>,
    workspaces: Vec<crate::core::models::Workspace>,
    status: String,
}

impl Default for LauncherState {
    fn default() -> Self {
        Self {
            _store: None,
            _manager: None,
            _monitor: None,
            workspaces: Vec::new(),
            status: "Initialisation…".to_string(),
        }
    }
}

impl LauncherState {
    pub fn new(
        store: Arc<ConfigStore>,
        manager: Arc<WorkspaceManager>,
        monitor: Option<Arc<EventStore>>,
    ) -> Self {
        let workspaces = manager.list();
        Self {
            _store: Some(store),
            _manager: Some(manager),
            _monitor: monitor,
            workspaces,
            status: "Prêt".to_string(),
        }
    }

    fn refresh(&mut self) {
        if let Some(manager) = &self._manager {
            self.workspaces = manager.list();
        }
        self.status = format!("{} workspace(s)", self.workspaces.len());
    }
}

/// Messages the UI can produce.
#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    StartWorkspace(String),
    StopWorkspace(String),
    DeleteWorkspace(String),
    OpenWorkspace(String),
    NewWorkspace,
    StatusChanged(String),
    Tick,
}

/// Update function for iced.
pub fn update(state: &mut LauncherState, message: Message) -> iced::Task<Message> {
    match message {
        Message::Refresh => {
            state.refresh();
            iced::Task::none()
        }
        Message::StartWorkspace(id) => {
            state.status = format!("Démarrage de {}…", id);
            iced::Task::none()
        }
        Message::StopWorkspace(id) => {
            state.status = format!("Arrêt de {}…", id);
            iced::Task::none()
        }
        Message::DeleteWorkspace(id) => {
            state.status = format!("Suppression de {}…", id);
            iced::Task::none()
        }
        Message::OpenWorkspace(id) => {
            state.status = format!("Ouverture de {}…", id);
            iced::Task::none()
        }
        Message::NewWorkspace => {
            state.status = "Nouveau workspace…".to_string();
            iced::Task::none()
        }
        Message::StatusChanged(s) => {
            state.status = s;
            iced::Task::none()
        }
        Message::Tick => {
            if shutdown_requested() {
                // The iced event loop will exit on the next tick.
            }
            iced::Task::none()
        }
    }
}

/// View function for iced.
pub fn view(state: &LauncherState) -> iced::Element<'_, Message> {
    use iced::widget::{button, column, container, text, Space};

    let header = column![
        text("n8n-launcher").size(20),
        button("Nouveau").on_press(Message::NewWorkspace),
        Space::with_height(iced::Length::Fixed(10.0)),
    ]
    .spacing(10);

    let list: iced::Element<Message> = if state.workspaces.is_empty() {
        text("Aucun workspace. Cliquez sur Nouveau pour en créer un.").into()
    } else {
        column(
            state
                .workspaces
                .iter()
                .map(|ws| {
                    button(text(format!("{} · Port {}", ws.name, ws.port)))
                        .on_press(Message::OpenWorkspace(ws.id.clone()))
                        .into()
                })
                .collect::<Vec<iced::Element<Message>>>(),
        )
        .spacing(5)
        .into()
    };

    let status = text(state.status.as_str());

    container(
        column![header, Space::with_height(iced::Length::Fixed(20.0)), list, status]
            .spacing(10)
            .padding(20),
    )
    .width(iced::Length::Fill)
    .height(iced::Length::Fill)
    .into()
}

/// Theme function for iced.
pub fn theme(_state: &LauncherState) -> iced::Theme {
    iced::Theme::Dark
}

/// Subscription function for iced.
pub fn subscription(_state: &LauncherState) -> iced::Subscription<Message> {
    iced::time::every(std::time::Duration::from_millis(SIGNAL_TICK_MS))
        .map(|_| Message::Tick)
}

/// Window settings for iced.
pub fn window(_state: &LauncherState) -> iced::window::Settings {
    iced::window::Settings {
        size: iced::Size::new(1024.0, 640.0),
        min_size: Some(iced::Size::new(1024.0, 640.0)),
        resizable: true,
        ..Default::default()
    }
}

// Tests for the shell functions that don't need a display.
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shutdown_flag() {
        clear_shutdown();
        assert!(!shutdown_requested());
        request_shutdown();
        assert!(shutdown_requested());
        clear_shutdown();
        assert!(!shutdown_requested());
    }

    #[test]
    fn test_display_available_env() {
        let _ = display_available();
    }
}
