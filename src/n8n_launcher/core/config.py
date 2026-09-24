"""Persistent application configuration."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, overload

from .filelock import FileLock
from .models import AppConfig
from .paths import config_dir, config_file

T = TypeVar("T")


class ConfigError(RuntimeError):
    """Raised when launcher configuration cannot be read or written."""


class ConfigStore:
    """Load and save :class:`AppConfig` to disk, atomically and securely.

    ``load``/``save`` are safe under threads (reentrant ``_lock``) *and* under
    separate processes (advisory ``FileLock`` on ``config.json.lock``), so a
    second launcher instance can never interleave a read-modify-write on the
    shared file. :meth:`mutate` additionally serializes a whole cycle under the
    exclusive file lock so a slow poller can never clobber a quick edit.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_file()
        self._lock = threading.RLock()
        self._file_lock = FileLock(self.path.with_name(f"{self.path.name}.lock"))

    def load(self) -> AppConfig:
        """Read, parse and validate the configuration file."""
        with self._lock, self._file_lock.locked(shared=True):
            return self._read()

    def save(self, config: AppConfig) -> None:
        """Persist the config via temp-file + atomic rename with 0o600 perms."""
        with self._lock, self._file_lock.locked():
            self._write(config)

    @overload
    def mutate(self, fn: Callable[[AppConfig], None]) -> AppConfig: ...

    @overload
    def mutate(self, fn: Callable[[AppConfig], T]) -> T: ...

    def mutate(self, fn: Callable[[AppConfig], T | None]) -> AppConfig | T:
        """Serialize one read-modify-write cycle and return its result.

        Holds the exclusive file lock for the whole cycle, so no sibling
        process can read an intermediate state. Under the lock, reads the
        config, passes it to *fn*, saves it again and returns the config —
        unless *fn* returned a non-``None`` value, which is returned instead
        (so callers can hand back a freshly built workspace, etc.).
        """
        with self._lock, self._file_lock.locked():
            config = self._read()
            result = fn(config)
            self._write(config)
            return config if result is None else result

    def _read(self) -> AppConfig:
        """Read and validate the config file (callers hold the locks)."""
        try:
            with self.path.open(encoding="utf-8") as handle:
                return AppConfig.from_dict(json.load(handle))
        except FileNotFoundError as exc:
            raise ConfigError("Launcher configuration does not exist") from exc
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid launcher configuration: {self.path}") from exc

    def _write(self, config: AppConfig) -> None:
        """Write *config* atomically (callers hold the locks)."""
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
    """Create the configuration directory if it does not exist yet."""
    config_dir().mkdir(parents=True, exist_ok=True)
