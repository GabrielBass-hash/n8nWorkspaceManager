"""Persistent application configuration, stored in SQLite.

The store keeps the same front-end contract as the historical JSON backend —
``load`` / ``save`` / ``mutate`` plus a ``ConfigError`` — so every call site
stays identical. Only the storage changes: instead of rewriting a whole
``config.json`` on every write, the configuration lives in a single SQLite
database where meta values and per-workspace rows are upserted individually.
This keeps the write cost proportional to what actually changed (O(1) for a
single workspace) instead of to the total number of workspaces.

Cross-process safety comes from SQLite itself: writers serialize under
``BEGIN IMMEDIATE`` (with a 10 s busy timeout) and readers use the WAL, so a
second launcher instance can never interleave a read-modify-write on the
shared database. An optional legacy ``config.json`` is migrated in once the
first time the database is found missing; the JSON file itself is never
deleted, so it doubles as a backup of the pre-SQLite state.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar, overload

from .models import AppConfig
from .paths import config_dir, config_file, launcher_db

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Workspace rows are stored as JSON blobs keyed by id, so a mutate touching
# one workspace rewrites a single row instead of the whole file.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspaces (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""

# meta keys that map 1:1 onto AppConfig attributes (work_dir is stored as its
# string form because Path is not JSON-native).
_APP_KEYS = ("owner_email", "owner_password", "work_dir", "github_token")

_SCHEMA_VERSION = 1

# How long a write waits for a sibling process holding BEGIN IMMEDIATE before
# giving up. 10 s keeps a slow poller from failing while a quick edit commits.
_BUSY_TIMEOUT_MS = 10_000


def _store_meta(config: AppConfig) -> dict[str, str]:
    """Serialize the flat :class:`AppConfig` fields into ``meta`` values."""
    return {
        "owner_email": config.owner_email,
        "owner_password": config.owner_password,
        "work_dir": str(config.work_dir),
        # A null token is stored as "" so the value never needs JSON nulls;
        # the empty string is read back as None.
        "github_token": config.github_token or "",
    }


class ConfigError(RuntimeError):
    """Raised when launcher configuration cannot be read or written."""


class ConfigStore:
    """Load and save :class:`AppConfig` to a SQLite database.

    ``load``/``save`` are safe under threads (reentrant ``_lock``) *and* under
    separate processes (SQLite's own file locking), so a second launcher
    instance can never interleave a read-modify-write on the shared database.
    :meth:`mutate` additionally holds ``BEGIN IMMEDIATE`` for the whole cycle
    so a slow poller can never clobber a quick edit.

    *path* is the database file (default ``launcher.db`` under the config
    dir). *legacy_json* points at a pre-SQLite ``config.json`` to migrate once
    when the database is missing; when *path* is left at the default, the
    standard ``config_file()`` is used.
    """

    def __init__(self, path: Path | None = None, *, legacy_json: Path | None = None) -> None:
        self.path = path or launcher_db()
        self._legacy_json = legacy_json
        if legacy_json is None and path is None:
            self._legacy_json = config_file()
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def load(self) -> AppConfig:
        """Read, parse and validate the stored configuration."""
        with self._lock:
            try:
                if not self.path.exists():
                    if self._legacy_json is not None and self._legacy_json.exists():
                        self._migrate_legacy()
                    else:
                        raise ConfigError("Launcher configuration does not exist")
                with self._connect() as conn:
                    return self._read(conn)
            except (OSError, sqlite3.Error) as exc:
                raise ConfigError(f"Invalid launcher configuration: {self.path}") from exc

    def save(self, config: AppConfig) -> None:
        """Persist *config*, upserting only the rows that changed."""
        with self._lock:
            try:
                conn = self._connect()
                with self._transaction(conn):
                    self._write(conn, config)
            except (OSError, sqlite3.Error) as exc:
                raise ConfigError(f"Could not save launcher configuration: {self.path}") from exc

    @overload
    def mutate(self, fn: Callable[[AppConfig], None]) -> AppConfig: ...

    @overload
    def mutate(self, fn: Callable[[AppConfig], T]) -> T: ...

    def mutate(self, fn: Callable[[AppConfig], T | None]) -> AppConfig | T:
        """Serialize one read-modify-write cycle and return its result.

        Holds the ``BEGIN IMMEDIATE`` write transaction for the whole cycle,
        so no sibling process can read an intermediate state. Under the lock,
        reads the config, passes it to *fn*, saves it again and returns the
        config — unless *fn* returned a non-``None`` value, which is returned
        instead (so callers can hand back a freshly built workspace, etc.).
        """
        with self._lock:
            try:
                if not self.path.exists():
                    if self._legacy_json is not None and self._legacy_json.exists():
                        self._migrate_legacy()
                    else:
                        raise ConfigError("Launcher configuration does not exist")
                conn = self._connect()
                with self._transaction(conn):
                    config = self._read(conn)
                    result = fn(config)
                    self._write(conn, config)
            except (OSError, sqlite3.Error) as exc:
                raise ConfigError(f"Could not save launcher configuration: {self.path}") from exc
            # fn's own exceptions propagate unchanged: the rollback ran in the
            # context manager and the previous on-disk state is untouched.
            return config if result is None else result

    def export_json(self, target: Path) -> None:
        """Export the current configuration as a portable JSON document.

        Intended for backups and manual inspection: the live format is the
        SQLite database, but a JSON snapshot keeps the data honour-format
        portable across machines.
        """
        config = self.load()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(
                json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise ConfigError(f"Could not write configuration export: {target}") from exc

    @contextlib.contextmanager
    def _transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        """Serialize a write as ``BEGIN IMMEDIATE`` + commit / rollback."""
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def _connect(self) -> sqlite3.Connection:
        """Open (once) the SQLite connection and ensure the schema exists."""
        if self._connection is not None:
            return self._connection
        _restrict_dir(self.path.parent)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
        except (OSError, sqlite3.Error):
            conn.close()
            raise
        _restrict_db_files(self.path)
        self._connection = conn
        return conn

    def _read(self, conn: sqlite3.Connection) -> AppConfig:
        """Read and validate the stored configuration (callers hold the lock)."""
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        if "owner_email" not in meta:
            raise ConfigError("Launcher configuration does not exist")
        payload: dict[str, object] = {key: meta.get(key) or None for key in _APP_KEYS}
        try:
            payload["workspaces"] = [
                json.loads(row["data"])
                for row in conn.execute("SELECT data FROM workspaces ORDER BY rowid")
            ]
            return AppConfig.from_dict(payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid launcher configuration: {self.path}") from exc

    def _write(self, conn: sqlite3.Connection, config: AppConfig) -> None:
        """Upsert the changed rows and delete removed workspaces.

        Only rows whose serialized form differs are rewritten, so a poll
        bumping one state touches one row instead of the whole document.
        """
        previous = {
            row["id"]: row["data"] for row in conn.execute("SELECT id, data FROM workspaces")
        }
        current = {ws.id: json.dumps(ws.to_dict(), ensure_ascii=False) for ws in config.workspaces}
        for key, value in _store_meta(config).items():
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        for workspace_id, data in current.items():
            if previous.get(workspace_id) != data:
                conn.execute(
                    "INSERT INTO workspaces(id, data) VALUES(?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                    (workspace_id, data),
                )
        for workspace_id in previous.keys() - current.keys():
            conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )

    def _migrate_legacy(self) -> None:
        """Import a legacy ``config.json`` once into the SQLite database."""
        legacy = self._legacy_json
        if legacy is None:
            raise ConfigError("Launcher configuration does not exist")
        try:
            with legacy.open(encoding="utf-8") as handle:
                config = AppConfig.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid launcher configuration: {legacy}") from exc
        conn = self._connect()
        try:
            with self._transaction(conn):
                self._write(conn, config)
        except (OSError, sqlite3.Error) as exc:
            raise ConfigError(f"Could not save launcher configuration: {self.path}") from exc
        logger.info("Migrated legacy config %s to SQLite database %s", legacy, self.path)


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


def _restrict_dir(path: Path) -> None:
    """Create *path* and force ``0o700`` so the config tree is not traversable.

    ``mkdir(mode=0o700)`` only applies the mode at creation time, so an
    already-existing directory (created by an older launcher or by
    ``platformdirs``) is chmodded explicitly as well.
    """
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _restrict_db_files(path: Path) -> None:
    """Restrict the SQLite database and its WAL/SHM sidecars to ``0o600``.

    In WAL mode SQLite writes ``<name>-wal`` and ``<name>-shm`` next to the
    database; those sidecars hold the same pages (owner password, GitHub
    token, …) but are created with the default umask. They appear as soon as
    the WAL pragma runs, so they are restricted here too — guarded by
    ``exists()`` because a future schema-less open may not create them.
    """
    _restrict_file(path)
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar.exists():
            _restrict_file(sidecar)


def ensure_directories() -> None:
    """Create the configuration directory if it does not exist yet."""
    _restrict_dir(config_dir())
