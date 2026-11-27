//! n8n-launcher — cross-platform launcher for isolated n8n workspaces.
//! Rust rewrite. CLI/ GUI entry point.

pub mod core;
pub mod database;
pub mod docker;
pub mod git;
pub mod github;
pub mod n8n;
pub mod monitoring;
pub mod platform;
pub mod remote;
pub mod workspaces;
pub mod app;

pub use core::models::*;

#[cfg(feature = "gui")]
mod views;
