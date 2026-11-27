//! Application entry point (Rust rewrite).
//!
//! Replaces `src/n8n_launcher/__main__.py` + `run.py`.
//! Single-instance lock → Docker resolution → journal → config load → wizard or iced UI → ordered shutdown.

use std::process::ExitCode;
use std::sync::Arc;

use n8n_launcher::core::config::ConfigStore;
use n8n_launcher::core::filelock::acquire_single_instance_lock;
use n8n_launcher::core::paths::config_dir;
use n8n_launcher::docker::manager::{resolve_docker_command, DockerManager};
use n8n_launcher::monitoring::bootstrap::bootstrap_logging;
use n8n_launcher::workspaces::manager::WorkspaceManager;

fn main() -> ExitCode {
    if std::env::args().any(|a| a == "--self-test") {
        return match self_test() {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("self-test failed: {e}");
                ExitCode::FAILURE
            }
        };
    }

    let started = std::time::Instant::now();
    let launcher_db = match launcher_db_path() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("Erreur : {e}");
            return ExitCode::FAILURE;
        }
    };
    let store = Arc::new(ConfigStore::new(launcher_db));

    // Exactly one launcher process may hold the config + compose files at a time.
    let mut instance_lock = match acquire_single_instance_lock(config_dir().expect("config dir").as_path()) {
        Ok(lock) => lock,
        Err(_) => {
            eprintln!("Une autre instance de l'application est déjà en cours d'exécution.");
            return ExitCode::SUCCESS;
        }
    };

    let monitor = match bootstrap_logging() {
        Ok(store) => {
            tracing::info!("Surveillance active");
            Some(Arc::new(store))
        }
        Err(e) => {
            tracing::warn!("Journalisation persistante indisponible : {e}");
            None
        }
    };

    let docker = Arc::new(DockerManager::new(&resolve_docker_command()));

    // Install signal handlers for graceful shutdown.
    let _signal_guard = n8n_launcher::app::install_signal_handlers(&store, &docker);

    let result = (|| -> Result<(), anyhow::Error> {
        match store.load() {
            Ok(_config) => {
                let manager = Arc::new(WorkspaceManager::new(&store, &docker));
                tracing::info!(
                    "Ouverture de l'interface (workspaces={})",
                    manager.list().len()
                );
                #[cfg(feature = "gui")]
                {
                    n8n_launcher::app::run_gui(store.clone(), manager, monitor.clone())?;
                }
                #[cfg(not(feature = "gui"))]
                {
                    let _ = manager;
                    let _ = monitor;
                    anyhow::bail!("GUI feature not enabled");
                }
            }
            Err(e) if store.path().exists() => {
                tracing::warn!("Configuration illisible, arrêt du lanceur ({e})");
                // Backup unreadable config and stop
                n8n_launcher::core::config::ConfigStore::backup_unreadable(&store)?;
                return Ok(());
            }
            Err(_) => {
                tracing::info!("Aucune configuration : premier lancement");
                // First launch wizard
                let launched = n8n_launcher::app::prompt_first_launch(&store, &docker)?;
                if !launched {
                    return Ok(());
                }
            }
        }
        Ok(())
    })();

    // Ordered shutdown — bracketed like the Python `main()` finally block.
    let _ = n8n_launcher::app::close_session(&*store, &*docker, started);
    let _ = instance_lock.release();
    if let Some(m) = monitor {
        let _ = m.close();
    }
    let _ = store.close();

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("Erreur : {e}");
            ExitCode::FAILURE
        }
    }
}

fn launcher_db_path() -> Result<std::path::PathBuf, String> {
    n8n_launcher::core::paths::launcher_db()
        .map_err(|e| format!("Impossible de résoudre le chemin de la base de configuration : {e}"))
}

fn self_test() -> Result<(), anyhow::Error> {
    if !n8n_launcher::app::display_available() {
        eprintln!("self-test: no display available");
        anyhow::bail!("no display available");
    }
    let scratch = tempfile::tempdir()?;
    let store = ConfigStore::new(scratch.path().join("launcher.db"));
    store.save(&n8n_launcher::core::models::AppConfig::new_test(
        "self-test@example.invalid",
        "SelfTest1",
        scratch.path().to_path_buf(),
    ))?;
    #[cfg(feature = "gui")]
    {
        // One tick of the event loop is enough to prove the window can open.
        let _app = iced::application(
            "n8n-launcher self-test",
            n8n_launcher::app::update,
            n8n_launcher::app::view,
        )
        .subscription(n8n_launcher::app::subscription)
        .theme(n8n_launcher::app::theme)
        .window(n8n_launcher::app::window);
    }
    store.close()?;
    drop(scratch);
    Ok(())
}
