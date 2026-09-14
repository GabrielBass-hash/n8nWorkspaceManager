"""Platform-specific launcher paths."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

APP_NAME = "n8n-launcher"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.json"


def logs_dir() -> Path:
    return Path(user_log_dir(APP_NAME))


def workspace_runtime_dir(workspace_id: str) -> Path:
    return config_dir() / "workspaces" / workspace_id


def compose_file(workspace_id: str) -> Path:
    return workspace_runtime_dir(workspace_id) / "compose.yml"
