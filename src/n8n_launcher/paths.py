"""Platform-specific launcher paths."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

APP_NAME = "n8n-launcher"


def config_dir() -> Path:
    """Return the platform-specific configuration directory for n8n-launcher."""
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    """Return the full path of the JSON configuration file."""
    return config_dir() / "config.json"


def logs_dir() -> Path:
    """Return the platform-specific log directory for n8n-launcher."""
    return Path(user_log_dir(APP_NAME))


def workspace_runtime_dir(workspace_id: str) -> Path:
    """Return the runtime directory for a workspace (Compose file, etc.)."""
    return config_dir() / "workspaces" / workspace_id


def compose_file(workspace_id: str) -> Path:
    """Return the Compose YAML path for a workspace."""
    return workspace_runtime_dir(workspace_id) / "compose.yml"


def updates_dir() -> Path:
    """Return the staging directory for downloaded update artifacts and helpers."""
    return config_dir() / "updates"
