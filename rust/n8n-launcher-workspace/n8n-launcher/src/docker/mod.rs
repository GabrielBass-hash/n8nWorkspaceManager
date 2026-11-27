//! Docker module: Compose rendering and `docker compose` wrapper.
//! Port de `src/n8n_launcher/docker/`.

pub mod compose;
pub mod manager;

pub use compose::{
    compose_project_name, render_compose, render_remote_compose, write_compose,
};
