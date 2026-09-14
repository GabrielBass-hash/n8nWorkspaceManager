"""Persistent application configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import AppConfig
from .paths import config_dir, config_file


class ConfigError(RuntimeError):
    """Raised when launcher configuration cannot be read or written."""


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file()

    def load(self) -> AppConfig:
        try:
            with self.path.open(encoding="utf-8") as handle:
                return AppConfig.from_dict(json.load(handle))
        except FileNotFoundError as exc:
            raise ConfigError("Launcher configuration does not exist") from exc
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid launcher configuration: {self.path}") from exc

    def save(self, config: AppConfig) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config.to_dict(), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _restrict_file(temporary_path)
            temporary_path.replace(self.path)
            _restrict_file(self.path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise ConfigError(f"Could not save launcher configuration: {self.path}") from exc


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


def ensure_directories() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
