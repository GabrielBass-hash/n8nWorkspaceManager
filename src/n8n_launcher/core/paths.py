"""Platform-specific launcher paths."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

APP_NAME = "n8n-launcher"


def config_dir() -> Path:
    """Return the platform-specific configuration directory for n8n-launcher."""
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    """Return the path of the legacy JSON configuration file."""
    return config_dir() / "config.json"


def launcher_db() -> Path:
    """Return the full path of the SQLite configuration database."""
    return config_dir() / "launcher.db"


def logs_dir() -> Path:
    """Return the platform-specific log directory for n8n-launcher."""
    return Path(user_log_dir(APP_NAME))


def workspace_runtime_dir(workspace_id: str) -> Path:
    """Return the runtime directory for a workspace (Compose file, etc.)."""
    return config_dir() / "workspaces" / workspace_id


def browser_profile_dir() -> Path:
    """Return the Chromium profile the launcher opens its n8n windows with.

    Owned by the launcher on purpose: n8n's session cookie lives there, so
    reopening an instance does not ask the owner to sign in again, and the
    windows the launcher is able to raise are only ever its own.
    """
    return config_dir() / "browser"


def compose_file(workspace_id: str) -> Path:
    """Return the Compose YAML path for a workspace."""
    return workspace_runtime_dir(workspace_id) / "compose.yml"


def updates_dir() -> Path:
    """Return the staging directory for downloaded update artifacts and helpers."""
    return config_dir() / "updates"
