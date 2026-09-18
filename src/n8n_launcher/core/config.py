"""Persistent application configuration."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .models import AppConfig
from .paths import config_dir, config_file


class ConfigError(RuntimeError):
    """Raised when launcher configuration cannot be read or written."""


class ConfigStore:
    """Load and save :class:`AppConfig` to disk, atomically and securely.

    Every ``load``/``save`` takes a reentrant process lock so the poller thread
    and the Tk-interactive manager actions never interleave a load-modify-save
    sequence on the shared file. Single ``load``/``save`` calls are still
    protected by the lock; :meth:`mutate` additionally serializes a whole
    read-modify-write cycle so a slow poller can never clobber a quick edit.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file()
        self._lock = threading.RLock()

    def load(self) -> AppConfig:
        """Read, parse and validate the configuration file."""
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    return AppConfig.from_dict(json.load(handle))
            except FileNotFoundError as exc:
                raise ConfigError("Launcher configuration does not exist") from exc
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"Invalid launcher configuration: {self.path}") from exc

    def save(self, config: AppConfig) -> None:
        """Persist the config via temp-file + atomic rename with 0o600 perms."""
        with self._lock:
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
                raise ConfigError(
                    f"Could not save launcher configuration: {self.path}"
                ) from exc

    def mutate(self, fn) -> Any:
        """Serialize one read-modify-write cycle and return its result.

        Under the lock, loads the config, passes it to *fn*, saves it again and
        returns the config — unless *fn* returned a non-``None`` value, which
        is returned instead (so callers can hand back a freshly built
        workspace, a replaced object, etc.).
        """
        with self._lock:
            config = self.load()
            result = fn(config)
            self.save(config)
            return config if result is None else result


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


def ensure_directories() -> None:
    """Create the configuration directory if it does not exist yet."""
    config_dir().mkdir(parents=True, exist_ok=True)
