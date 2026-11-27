//! Application shell: signal handling, GUI lifecycle, first-launch wizard.
//! Port de `src/n8n_launcher/gui/app.py` + `__main__.py` logic.
//!
//! La vue iced est dans `gui` ; ce module possède l'état, les messages et
//! l'ordonnancement des tâches. Tout appel au manager qui peut bloquer
//! (`ensure_running`, `stop_with_sync`, `create`, …) passe par `spawn_blocking`
//! pour que la fenêtre reste réactive pendant les ~60 s de démarrage n8n.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::core::config::ConfigStore;
use crate::core::first_launch;
use crate::core::models::{DbConfig, DbMode, Workspace, WorkspaceState};
use crate::docker::manager::DockerManager;
use crate::gui::{pages, theme, widgets};
use crate::gui::icons::Icon;
use crate::monitoring::events::Event;
use crate::monitoring::store::EventStore;
use crate::platform::browser;
use crate::platform::updater;
use crate::workspaces::manager::WorkspaceManager;
use crate::workspaces::status::WorkspaceAction;

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

/// Iced creates its own Application on each run(); nothing to hold globally.
pub fn ensure_application() {}

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

    let app = iced::application("n8n-launcher", update, view)
        .subscription(subscription)
        .theme(theme)
        .window(window(&LauncherState::default()));

    let docker = manager.docker();
    let state = LauncherState::new(store, manager, docker, monitor);
    app.run_with(|| {
        (
            state,
            iced::window::get_latest().map(Message::WindowReady),
        )
    })
    .map_err(|e| anyhow::anyhow!("iced run failed: {e}"))
}

// ---------------------------------------------------------------------------
// First-launch wizard
// ---------------------------------------------------------------------------

fn wizard_field<'a>(
    label: &'a str,
    value: &'a str,
    placeholder: &'a str,
    secure: bool,
    on_input: fn(String) -> WizardMessage,
) -> iced::Element<'a, WizardMessage> {
    iced::widget::column![
        iced::widget::text(label).size(13).style(|_theme| iced::widget::text::Style {
            color: Some(theme::MUTED),
        }),
        iced::widget::text_input(placeholder, value)
            .on_input(on_input)
            .secure(secure)
            .size(15)
            .padding(12)
            .style(theme::input_style),
    ]
    .spacing(6)
    .into()
}

struct WizardState {
    store: Arc<ConfigStore>,
    docker: Arc<DockerManager>,
    email: String,
    password: String,
    work_dir: String,
    error: Option<String>,
    busy: bool,
    completed: Arc<AtomicBool>,
    window_id: Option<iced::window::Id>,
}

#[derive(Debug, Clone)]
enum WizardMessage {
    EmailChanged(String),
    PasswordChanged(String),
    DirChanged(String),
    Submit,
    Submitted(Result<(), String>),
    WindowReady(Option<iced::window::Id>),
}

fn wizard_update(state: &mut WizardState, message: WizardMessage) -> iced::Task<WizardMessage> {
    match message {
        WizardMessage::EmailChanged(value) => state.email = value,
        WizardMessage::PasswordChanged(value) => state.password = value,
        WizardMessage::DirChanged(value) => state.work_dir = value,
        WizardMessage::WindowReady(id) => state.window_id = id,
        WizardMessage::Submit => {
            if state.busy {
                return iced::Task::none();
            }
            state.error = None;
            state.busy = true;
            let store = state.store.clone();
            let docker = state.docker.clone();
            let email = state.email.clone();
            let password = state.password.clone();
            let dir = state.work_dir.clone();
            return iced::Task::future(async move {
                let result = tokio::task::spawn_blocking(move || {
                    first_launch::run_first_launch(
                        &store,
                        &docker,
                        &email,
                        &password,
                        Path::new(&dir),
                    )
                    .map(|_| ())
                    .map_err(|e| e.to_string())
                })
                .await;
                WizardMessage::Submitted(match result {
                    Ok(value) => value,
                    Err(err) => Err(format!("Tâche interrompue : {err}")),
                })
            });
        }
        WizardMessage::Submitted(result) => {
            state.busy = false;
            match result {
                Ok(()) => {
                    state.completed.store(true, Ordering::SeqCst);
                    if let Some(id) = state.window_id {
                        return iced::window::close(id);
                    }
                }
                Err(err) => state.error = Some(err),
            }
        }
    }
    iced::Task::none()
}

fn wizard_view(state: &WizardState) -> iced::Element<'_, WizardMessage> {
    use iced::widget::{button, column, container, row, scrollable, text, Space};

    let error = if let Some(err) = &state.error {
        container(
            row![
                widgets::icon::<WizardMessage>(Icon::Warning, 16.0, theme::DANGER),
                text(err).size(13).style(|_theme| iced::widget::text::Style {
                    color: Some(theme::DANGER),
                }),
            ]
            .spacing(8),
        )
        .padding(12)
        .style(theme::surface)
        .into()
    } else {
        iced::Element::from(Space::with_height(0))
    };

    let card = container(
        column![
            row![
                widgets::icon::<WizardMessage>(Icon::Logo, 34.0, theme::ACCENT),
                column![
                    text("Bienvenue dans n8n-launcher").size(24),
                    text("Quelques informations pour créer votre espace de travail.").size(13)
                        .style(|_theme| iced::widget::text::Style { color: Some(theme::MUTED) }),
                ]
                .spacing(3),
            ]
            .spacing(14)
            .align_y(iced::alignment::Vertical::Center),
            Space::with_height(16),
            wizard_field("Email propriétaire", &state.email, "vous@exemple.com", false, WizardMessage::EmailChanged),
            wizard_field("Mot de passe n8n", &state.password, "8 caractères minimum, chiffre et majuscule", true, WizardMessage::PasswordChanged),
            wizard_field("Dossier de travail", &state.work_dir, "~/.n8n-launcher/work", false, WizardMessage::DirChanged),
            Space::with_height(6),
            error,
            button(
                row![
                    if state.busy { text("Vérification…").size(14) } else { text("Terminer la configuration").size(14) },
                ]
            )
            .on_press_maybe(if state.busy { None } else { Some(WizardMessage::Submit) })
            .padding([12, 20])
            .style(theme::primary_button),
        ]
        .spacing(14),
    )
    .padding(32)
    .width(iced::Length::Fixed(520.0))
    .style(theme::card);

    container(scrollable(
        container(card)
            .width(iced::Length::Fill)
            .height(iced::Length::Fill)
            .center_x(iced::Length::Fill)
            .center_y(iced::Length::Fill),
    ))
    .width(iced::Length::Fill)
    .height(iced::Length::Fill)
    .style(theme::app_background)
    .into()
}

/// Prompt for first-launch configuration with a real iced wizard.
///
/// The rules (email, password policy, Docker check, persistence) come from
/// `core::first_launch`; this function only owns the dialog and its lifecycle.
pub fn prompt_first_launch(store: &ConfigStore, docker: &DockerManager) -> anyhow::Result<bool> {
    if !display_available() {
        return Err(GuiUnavailable(
            "Aucun affichage disponible : lancez le launcher dans une session graphique.".to_string(),
        )
        .into());
    }

    let completed = Arc::new(AtomicBool::new(false));
    let state = WizardState {
        store: Arc::new(store.clone()),
        docker: Arc::new(docker.clone()),
        email: String::new(),
        password: String::new(),
        work_dir: crate::core::paths::config_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join("work")
            .to_string_lossy()
            .to_string(),
        error: None,
        busy: false,
        completed: completed.clone(),
        window_id: None,
    };

    iced::application("n8n-launcher — configuration", wizard_update, wizard_view)
        .theme(|_| theme::theme())
        .window(window(&LauncherState::default()))
        .run_with(|| {
            (
                state,
                iced::window::get_latest().map(WizardMessage::WindowReady),
            )
        })
        .map_err(|e| anyhow::anyhow!("wizard failed: {e}"))?;

    Ok(completed.load(Ordering::SeqCst))
}

/// Stop every workspace, then record the journal's end-of-session event.
pub fn close_session(
    store: &ConfigStore,
    docker: &DockerManager,
    started: Instant,
    monitor: Option<&EventStore>,
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
            let _ = manager.stop_with_sync(&workspace.id);
        }
    }
    let message = format!(
        "Surveillance terminée — session de {}, {} workspace(s) en cours à la fermeture",
        session_length(started),
        running
    );
    tracing::info!("{message}");
    if let Some(monitor) = monitor {
        let _ = monitor.append(&Event::new(
            "session.end".to_string(),
            "INFO".to_string(),
            message,
        ));
    }
    clear_shutdown();
    Ok(())
}

/// Best-effort shutdown of every launched workspace.
pub fn stop_all(store: &ConfigStore, docker: &DockerManager) {
    let manager = WorkspaceManager::new(store, docker);
    if let Ok(config) = store.load() {
        for workspace in config.workspaces {
            let _ = manager.stop_with_sync(&workspace.id);
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

// ---------------------------------------------------------------------------
// Main window state
// ---------------------------------------------------------------------------

/// Page currently displayed in the main area.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Nav {
    Workspaces,
    Journal,
    Settings,
}

/// A transient message shown at the bottom of the window.
pub(crate) struct Notice {
    pub(crate) text: String,
    pub(crate) created: Instant,
    pub(crate) error: bool,
}

/// The launcher's state: the data the UI renders.
pub struct LauncherState {
    pub(crate) store: Option<Arc<ConfigStore>>,
    pub(crate) manager: Option<Arc<WorkspaceManager>>,
    pub(crate) docker: Option<Arc<DockerManager>>,
    pub(crate) monitor: Option<Arc<EventStore>>,
    pub(crate) workspaces: Vec<Workspace>,
    pub(crate) status: String,
    pub(crate) nav: Nav,
    pub(crate) search: String,
    pub(crate) filter: Option<WorkspaceState>,
    pub(crate) busy: HashSet<String>,
    pub(crate) pending_delete: Option<String>,
    pub(crate) show_create: bool,
    pub(crate) creating: bool,
    pub(crate) create_name: String,
    pub(crate) create_port: String,
    pub(crate) create_dir: String,
    pub(crate) create_managed: bool,
    pub(crate) journal_query: String,
    pub(crate) events: Vec<Event>,
    pub(crate) token_input: String,
    pub(crate) token_present: bool,
    pub(crate) update_current: String,
    pub(crate) update_latest: Option<String>,
    pub(crate) update_error: Option<String>,
    pub(crate) checking_updates: bool,
    pub(crate) notice: Option<Notice>,
    pub(crate) window_id: Option<iced::window::Id>,
}

impl Default for LauncherState {
    fn default() -> Self {
        Self {
            store: None,
            manager: None,
            docker: None,
            monitor: None,
            workspaces: Vec::new(),
            status: "Initialisation…".to_string(),
            nav: Nav::Workspaces,
            search: String::new(),
            filter: None,
            busy: HashSet::new(),
            pending_delete: None,
            show_create: false,
            creating: false,
            create_name: String::new(),
            create_port: String::new(),
            create_dir: String::new(),
            create_managed: true,
            journal_query: String::new(),
            events: Vec::new(),
            token_input: String::new(),
            token_present: false,
            update_current: updater::current_version(),
            update_latest: None,
            update_error: None,
            checking_updates: false,
            notice: None,
            window_id: None,
        }
    }
}

impl LauncherState {
    /// Build the state from the services created by `main`.
    pub fn new(
        store: Arc<ConfigStore>,
        manager: Arc<WorkspaceManager>,
        docker: Arc<DockerManager>,
        monitor: Option<Arc<EventStore>>,
    ) -> Self {
        let mut state = Self {
            workspaces: manager.list(),
            status: "Prêt".to_string(),
            store: Some(store.clone()),
            manager: Some(manager),
            docker: Some(docker),
            monitor,
            ..Default::default()
        };
        state.refresh();
        state
    }

    fn refresh(&mut self) {
        if let Some(manager) = &self.manager {
            self.workspaces = manager.list();
        }
        self.status = format!("{} workspace(s)", self.workspaces.len());
        self.token_present = self
            .store
            .as_ref()
            .and_then(|store| store.load().ok())
            .and_then(|config| config.github_token)
            .map(|token| !token.is_empty())
            .unwrap_or(false);
    }

    fn notify(&mut self, text: String, error: bool) {
        self.notice = Some(Notice {
            text,
            created: Instant::now(),
            error,
        });
    }

    fn record(&self, name: &str, message: &str, level: &str) {
        if let Some(monitor) = &self.monitor {
            let _ = monitor.append(&Event::new(
                name.to_string(),
                level.to_string(),
                message.to_string(),
            ));
        }
    }
}

/// Messages the UI can produce.
#[derive(Debug, Clone)]
pub enum Message {
    Refresh,
    Tick,
    WindowReady(Option<iced::window::Id>),
    CloseRequested,
    Notice(String, bool),
    Navigate(Nav),
    SearchChanged(String),
    FilterChanged(Option<WorkspaceState>),
    StartWorkspace(String),
    StopWorkspace(String),
    OpenWorkspace(String),
    RequestDelete(String),
    ConfirmDelete(String),
    CancelDelete,
    WorkspaceOpFinished {
        id: String,
        verb: String,
        result: Result<(), String>,
    },
    NewWorkspace,
    CreateNameChanged(String),
    CreatePortChanged(String),
    CreateDirChanged(String),
    CreateManagedChanged(bool),
    SubmitCreate,
    CancelCreate,
    CreateFinished(Result<Workspace, String>),
    JournalQueryChanged(String),
    JournalRefresh,
    JournalLoaded(Vec<Event>),
    TokenChanged(String),
    SaveToken,
    ClearToken,
    TokenSaved(Result<bool, String>),
    CheckUpdates,
    UpdatesChecked(Result<(String, bool), String>),
    OpenUrl(String),
}

/// Run a blocking closure on the tokio pool and turn its result into a message.
fn blocking(job: impl FnOnce() -> Message + Send + 'static) -> iced::Task<Message> {
    iced::Task::future(async move {
        match tokio::task::spawn_blocking(job).await {
            Ok(message) => message,
            Err(err) => Message::Notice(format!("Tâche interrompue : {err}"), true),
        }
    })
}

/// Build a task that runs one manager operation without freezing the window.
fn workspace_op<F>(
    state: &LauncherState,
    id: String,
    verb: &'static str,
    operation: F,
) -> iced::Task<Message>
where
    F: Fn(&WorkspaceManager, &str) -> Result<(), String> + Send + 'static,
{
    let Some(store) = state.store.clone() else {
        return iced::Task::none();
    };
    let Some(docker) = state.docker.clone() else {
        return iced::Task::none();
    };
    blocking(move || {
        // `WorkspaceManager` holds a raw observer pointer and is not `Send`,
        // so each operation builds a short-lived manager from Send clones.
        let manager = WorkspaceManager::new(&store, &docker);
        let result = operation(&manager, &id);
        Message::WorkspaceOpFinished {
            id,
            verb: verb.to_string(),
            result,
        }
    })
}

/// Sanitize a workspace name into a directory component.
fn directory_name(name: &str) -> String {
    let base: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    let trimmed = base.trim_matches('-');
    if trimmed.is_empty() {
        "workspace".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Load journal events, optionally filtered by a text query.
fn journal_task(state: &LauncherState, query: String) -> iced::Task<Message> {
    let Some(monitor) = state.monitor.clone() else {
        return iced::Task::none();
    };
    blocking(move || {
        let events = if query.trim().is_empty() {
            monitor.latest_events(200)
        } else {
            monitor
                .search_events(Some(&query), None, None, None, None, Some(200))
                .map(|mut events| {
                    events.reverse();
                    events
                })
        };
        match events {
            Ok(events) => Message::JournalLoaded(events),
            Err(err) => Message::Notice(format!("Journal indisponible : {err}"), true),
        }
    })
}

/// Update function for iced.
pub fn update(state: &mut LauncherState, message: Message) -> iced::Task<Message> {
    match message {
        Message::Refresh => state.refresh(),
        Message::WindowReady(id) => state.window_id = id,
        Message::CloseRequested => {
            request_shutdown();
            if let Some(id) = state.window_id {
                return iced::window::close(id);
            }
        }
        Message::Tick => {
            if let Some(notice) = &state.notice {
                if notice.created.elapsed() >= Duration::from_secs(5) {
                    state.notice = None;
                }
            }
            if shutdown_requested() && state.busy.is_empty() {
                if let Some(id) = state.window_id {
                    return iced::window::close(id);
                }
                return iced::window::get_latest().map(Message::WindowReady);
            }
        }
        Message::Notice(text, error) => {
            state.notify(text, error);
            state.refresh();
        }
        Message::Navigate(nav) => {
            state.nav = nav;
            if nav == Nav::Journal {
                return journal_task(state, state.journal_query.clone());
            }
        }
        Message::SearchChanged(value) => state.search = value,
        Message::FilterChanged(filter) => state.filter = filter,
        Message::StartWorkspace(id) => {
            state.busy.insert(id.clone());
            return workspace_op(state, id, "Démarrage", |manager, id| {
                manager.ensure_running(id).map(|_| ())
            });
        }
        Message::StopWorkspace(id) => {
            state.busy.insert(id.clone());
            return workspace_op(state, id, "Arrêt", |manager, id| {
                manager.stop_with_sync(id).map(|_| ())
            });
        }
        Message::OpenWorkspace(id) => {
            state.busy.insert(id.clone());
            let Some(store) = state.store.clone() else {
                return iced::Task::none();
            };
            let Some(docker) = state.docker.clone() else {
                return iced::Task::none();
            };
            return blocking(move || {
                let manager = WorkspaceManager::new(&store, &docker);
                let result = manager
                    .ensure_running(&id)
                    .map_err(|e| e.to_string())
                    .and_then(|reachable| {
                        let url = format!("http://localhost:{}", reachable.workspace.port);
                        browser::open_url(&url)
                    });
                Message::WorkspaceOpFinished {
                    id,
                    verb: "Ouverture".to_string(),
                    result,
                }
            });
        }
        Message::RequestDelete(id) => state.pending_delete = Some(id),
        Message::CancelDelete => state.pending_delete = None,
        Message::ConfirmDelete(id) => {
            state.pending_delete = None;
            state.busy.insert(id.clone());
            return workspace_op(state, id, "Suppression", |manager, id| manager.delete(id));
        }
        Message::WorkspaceOpFinished { id, verb, result } => {
            state.busy.remove(&id);
            state.refresh();
            match result {
                Ok(()) => {
                    let text = format!("{verb} de {id} terminé");
                    state.notify(text.clone(), false);
                    state.record("workspace.action", &text, "INFO");
                }
                Err(err) => {
                    let text = format!("{verb} de {id} impossible : {err}");
                    state.notify(text.clone(), true);
                    state.record("workspace.action", &text, "ERREUR");
                }
            }
            if state.nav == Nav::Journal {
                return journal_task(state, state.journal_query.clone());
            }
        }
        Message::NewWorkspace => {
            state.show_create = true;
            state.create_name.clear();
            state.create_port.clear();
            state.create_dir.clear();
            state.create_managed = true;
        }
        Message::CancelCreate => {
            state.show_create = false;
            state.creating = false;
        }
        Message::CreateNameChanged(value) => state.create_name = value,
        Message::CreatePortChanged(value) => state.create_port = value,
        Message::CreateDirChanged(value) => state.create_dir = value,
        Message::CreateManagedChanged(value) => state.create_managed = value,
        Message::SubmitCreate => {
            if state.creating {
                return iced::Task::none();
            }
            let Some(store) = state.store.clone() else {
                return iced::Task::none();
            };
            let Some(docker) = state.docker.clone() else {
                return iced::Task::none();
            };
            let name = state.create_name.trim().to_string();
            if name.is_empty() {
                state.notify("Le nom du workspace est requis".to_string(), true);
                return iced::Task::none();
            }
            let port = state.create_port.trim().parse::<u16>().ok();
            if !state.create_port.trim().is_empty() && port.is_none() {
                state.notify("Port invalide (1 à 65535)".to_string(), true);
                return iced::Task::none();
            }
            let dir_input = state.create_dir.trim().to_string();
            let managed = state.create_managed;
            state.creating = true;
            return blocking(move || {
                let work_dir = store
                    .load()
                    .ok()
                    .map(|config| config.work_dir)
                    .unwrap_or_else(|| PathBuf::from("."));
                let workflows_dir = if dir_input.is_empty() {
                    work_dir.join(directory_name(&name))
                } else {
                    PathBuf::from(&dir_input)
                };
                let mode = if managed { DbMode::MANAGED } else { DbMode::NONE };
                let manager = WorkspaceManager::new(&store, &docker);
                let result = manager.create(
                    &name,
                    workflows_dir,
                    Some(DbConfig::new(mode)),
                    port,
                    None,
                );
                Message::CreateFinished(result)
            });
        }
        Message::CreateFinished(result) => {
            state.creating = false;
            match result {
                Ok(workspace) => {
                    state.show_create = false;
                    state.refresh();
                    let text = format!("Workspace {} créé", workspace.name);
                    state.notify(text.clone(), false);
                    state.record("workspace.created", &text, "INFO");
                }
                Err(err) => state.notify(format!("Création impossible : {err}"), true),
            }
        }
        Message::JournalQueryChanged(query) => {
            state.journal_query = query.clone();
            return journal_task(state, query);
        }
        Message::JournalRefresh => return journal_task(state, state.journal_query.clone()),
        Message::JournalLoaded(events) => state.events = events,
        Message::TokenChanged(value) => state.token_input = value,
        Message::SaveToken | Message::ClearToken => {
            let Some(store) = state.store.clone() else {
                return iced::Task::none();
            };
            let Some(docker) = state.docker.clone() else {
                return iced::Task::none();
            };
            let cleared = matches!(message, Message::ClearToken);
            let token = if cleared {
                None
            } else {
                let value = state.token_input.trim().to_string();
                if value.is_empty() {
                    None
                } else {
                    Some(value)
                }
            };
            return blocking(move || {
                let manager = WorkspaceManager::new(&store, &docker);
                let result = manager.set_github_token(token).map(|_| cleared);
                Message::TokenSaved(result)
            });
        }
        Message::TokenSaved(result) => match result {
            Ok(cleared) => {
                state.token_input.clear();
                state.refresh();
                state.notify(
                    if cleared {
                        "Token GitHub effacé".to_string()
                    } else {
                        "Token GitHub enregistré".to_string()
                    },
                    false,
                );
                // Le token n'est jamais journalisé, même masqué.
            }
            Err(err) => state.notify(format!("Enregistrement du token impossible : {err}"), true),
        },
        Message::CheckUpdates => {
            state.checking_updates = true;
            state.update_error = None;
            let current = state.update_current.clone();
            return blocking(move || {
                let result = updater::fetch_latest_release(updater::REPO, None, 10)
                    .map_err(|e| e.to_string())
                    .map(|release| {
                        let newer = updater::release_is_newer(&release, &current);
                        (release.tag_name, newer)
                    });
                Message::UpdatesChecked(result)
            });
        }
        Message::UpdatesChecked(result) => {
            state.checking_updates = false;
            match result {
                Ok((version, newer)) => {
                    state.update_latest = Some(version);
                    if newer {
                        state.notify("Une nouvelle version est disponible".to_string(), false);
                    }
                }
                Err(err) => state.update_error = Some(err),
            }
        }
        Message::OpenUrl(url) => {
            return blocking(move || match browser::open_url(&url) {
                Ok(()) => Message::Notice("Lien ouvert dans le navigateur".to_string(), false),
                Err(err) => Message::Notice(err, true),
            });
        }
    }
    iced::Task::none()
}

/// View function for iced.
pub fn view(state: &LauncherState) -> iced::Element<'_, Message> {
    pages::view(state)
}

/// Theme function for iced.
pub fn theme(_state: &LauncherState) -> iced::Theme {
    theme::theme()
}

/// Subscription function for iced.
pub fn subscription(_state: &LauncherState) -> iced::Subscription<Message> {
    iced::Subscription::batch([
        iced::time::every(Duration::from_millis(SIGNAL_TICK_MS)).map(|_| Message::Tick),
        iced::window::close_requests().map(|_| Message::CloseRequested),
    ])
}

/// Window settings for iced.
pub fn window(_state: &LauncherState) -> iced::window::Settings {
    iced::window::Settings {
        size: iced::Size::new(1180.0, 740.0),
        min_size: Some(iced::Size::new(1024.0, 640.0)),
        resizable: true,
        ..Default::default()
    }
}

/// Return the status line shown in the footer; kept pure for tests.
pub fn status_line(state: &LauncherState) -> String {
    let running = state
        .workspaces
        .iter()
        .filter(|ws| ws.state == WorkspaceState::RUNNING)
        .count();
    format!("{} · {} en marche", state.status, running)
}

/// Map a card action to the message the view must emit.
pub(crate) fn action_message(action: WorkspaceAction, id: &str) -> Message {
    match action {
        WorkspaceAction::Open => Message::OpenWorkspace(id.to_string()),
        WorkspaceAction::Stop => Message::StopWorkspace(id.to_string()),
        WorkspaceAction::Delete => Message::RequestDelete(id.to_string()),
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

    #[test]
    fn test_directory_name_sanitizes() {
        assert_eq!(directory_name("Mon Projet / Test"), "mon-projet---test");
        assert_eq!(directory_name("###"), "workspace");
    }

    #[test]
    fn test_status_line_counts_running() {
        let mut state = LauncherState::default();
        state.status = "2 workspace(s)".to_string();
        state.workspaces.push(Workspace {
            state: WorkspaceState::RUNNING,
            ..Default::default()
        });
        assert_eq!(status_line(&state), "2 workspace(s) · 1 en marche");
    }

    #[test]
    fn test_action_messages_carry_the_workspace_id() {
        assert!(matches!(
            action_message(WorkspaceAction::Open, "ws-1"),
            Message::OpenWorkspace(id) if id == "ws-1"
        ));
        assert!(matches!(
            action_message(WorkspaceAction::Stop, "ws-2"),
            Message::StopWorkspace(id) if id == "ws-2"
        ));
        assert!(matches!(
            action_message(WorkspaceAction::Delete, "ws-3"),
            Message::RequestDelete(id) if id == "ws-3"
        ));
    }

    #[test]
    fn test_card_actions_come_from_status_rules() {
        // The view must not reimplement availability: verify the source of truth.
        let actions = crate::workspaces::status::card_actions(WorkspaceState::STOPPED);
        assert_eq!(actions.len(), 3);
        assert!(actions.iter().any(|a| a.label == "Ouvrir" && a.enabled));
        assert!(actions.iter().any(|a| a.label == "Arrêter" && !a.enabled));
    }
}
