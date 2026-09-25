"""Thread-safe SQLite persistence for application monitoring events."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from ..core.paths import logs_dir as default_logs_dir
from .events import Event, _format_timestamp, _parse_timestamp

RETENTION_DAYS = 30
_EVENTS_FILENAME = "events.db"
_BUSY_TIMEOUT_MS = 10_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name      TEXT NOT NULL,
    level     TEXT NOT NULL,
    message   TEXT NOT NULL,
    context   TEXT NOT NULL,
    exception TEXT
);
CREATE INDEX IF NOT EXISTS events_timestamp_idx ON events(timestamp);
CREATE INDEX IF NOT EXISTS events_name_idx ON events(name);
CREATE INDEX IF NOT EXISTS events_level_idx ON events(level);
"""


def event_store_path(logs_dir: Path | None = None) -> Path:
    """Return the event database path inside *logs_dir* or the platform log directory."""
    directory = logs_dir if logs_dir is not None else default_logs_dir()
    return directory / _EVENTS_FILENAME


class EventStore:
    """Persist and query structured events in a local SQLite database.

    The store owns one SQLite connection guarded by a re-entrant lock. SQLite
    WAL mode and a busy timeout additionally make the file safe when another
    process opens the same database. Events older than ``retention_days`` are
    removed whenever the store is opened or queried.
    """

    def __init__(
        self,
        logs_dir: Path | None = None,
        *,
        path: Path | None = None,
        retention_days: int = RETENTION_DAYS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create a store rooted at *logs_dir* or an explicit database *path*."""
        if logs_dir is not None and path is not None:
            raise ValueError("logs_dir and path cannot both be provided")
        if retention_days < 0:
            raise ValueError("retention_days must be greater than or equal to zero")
        self.path = path if path is not None else event_store_path(logs_dir)
        self.retention_days = retention_days
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._open()
        self.prune_old_events()

    @property
    def connection_is_open(self) -> bool:
        """Return whether the store still has an open SQLite connection."""
        with self._lock:
            return self._connection is not None

    def append(self, event: Event) -> Event:
        """Insert *event*, return it with its assigned ID, and enforce retention."""
        context = json.dumps(
            event.context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            connection = self._require_connection()
            with self._transaction(connection):
                cursor = connection.execute(
                    "INSERT INTO events(timestamp, name, level, message, context, exception) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        _format_timestamp(event.timestamp),
                        event.name,
                        event.level,
                        event.message,
                        context,
                        event.exception,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return an event identifier")
                event_id = int(cursor.lastrowid)
                connection.execute(
                    "DELETE FROM events WHERE timestamp < ?",
                    (_retention_cutoff(self._clock(), self.retention_days),),
                )
        return replace(event, id=event_id)

    def prune_old_events(self) -> int:
        """Delete events outside the configured retention window and return the count."""
        cutoff = _retention_cutoff(self._clock(), self.retention_days)
        with self._lock:
            connection = self._require_connection()
            with self._transaction(connection):
                cursor = connection.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        return max(cursor.rowcount, 0)

    def read_events(
        self,
        *,
        limit: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Event]:
        """Read events in chronological order, optionally bounded by time or count."""
        _validate_limit(limit)
        _validate_interval(since, until)
        self.prune_old_events()
        clauses: list[str] = []
        parameters: list[object] = []
        _add_time_filters(clauses, parameters, since, until)
        return self._query(clauses, parameters, limit)

    def search_events(
        self,
        query: str | None = None,
        *,
        name: str | None = None,
        level: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Search event names, messages, contexts, and exception text."""
        _validate_limit(limit)
        _validate_interval(since, until)
        self.prune_old_events()
        clauses: list[str] = []
        parameters: list[object] = []
        if query:
            pattern = _like_pattern(query)
            clauses.append(
                "(name LIKE ? ESCAPE '\\' OR message LIKE ? ESCAPE '\\' "
                "OR context LIKE ? ESCAPE '\\' OR exception LIKE ? ESCAPE '\\')"
            )
            parameters.extend((pattern, pattern, pattern, pattern))
        if name is not None:
            clauses.append("name LIKE ? ESCAPE '\\'")
            parameters.append(_like_pattern(name))
        if level is not None:
            clauses.append("level = ?")
            parameters.append(level.upper())
        _add_time_filters(clauses, parameters, since, until)
        return self._query(clauses, parameters, limit)

    def export_events(
        self,
        target: Path,
        *,
        query: str | None = None,
        name: str | None = None,
        level: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """Write matching events as a UTF-8 JSON array and return the row count."""
        events = self.search_events(
            query,
            name=name,
            level=level,
            since=since,
            until=until,
            limit=limit,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [event.to_dict() for event in events],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                os.chmod(temporary_name, 0o600)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name)
            raise
        _restrict_file(target)
        return len(events)

    def close(self) -> None:
        """Close the SQLite connection; subsequent operations raise a runtime error."""
        with self._lock:
            connection, self._connection = self._connection, None
            if connection is not None:
                connection.close()

    def __enter__(self) -> EventStore:
        """Return this store for use in a context manager."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the store when leaving a context manager."""
        self.close()

    def _open(self) -> None:
        """Open the database, configure SQLite, and create the event schema."""
        _restrict_dir(self.path.parent)
        connection = sqlite3.connect(
            str(self.path),
            timeout=_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            connection.executescript(_SCHEMA)
        except BaseException:
            connection.close()
            raise
        _restrict_db_files(self.path)
        self._connection = connection

    def _require_connection(self) -> sqlite3.Connection:
        """Return the active connection or report that the store is closed."""
        if self._connection is None:
            raise RuntimeError("event store is closed")
        return self._connection

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        """Run a write transaction with rollback on every failure path."""
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _query(
        self,
        clauses: list[str],
        parameters: list[object],
        limit: int | None,
    ) -> list[Event]:
        """Execute a filtered event query and materialize redacted events."""
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Every clause is a static fragment here; all caller values are bound
        # through ``parameters`` placeholders, never interpolated.
        sql = "SELECT id, timestamp, name, level, message, context, exception FROM events"
        sql += where + " ORDER BY timestamp ASC, id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            parameters = [*parameters, limit]
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(sql, parameters).fetchall()
        return [_row_to_event(row) for row in rows]


def _like_pattern(value: str) -> str:
    """Return *value* as a contains-pattern with LIKE wildcards escaped.

    A GUI search box receives arbitrary text: without escaping, ``ws_1`` would
    also match ``wsX1`` and a lone ``%`` would return every event.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _retention_cutoff(now: datetime, retention_days: int) -> str:
    """Return the storage timestamp before which events are expired."""
    normalized = _parse_timestamp(now)
    return _format_timestamp(normalized - timedelta(days=retention_days))


def _add_time_filters(
    clauses: list[str],
    parameters: list[object],
    since: datetime | None,
    until: datetime | None,
) -> None:
    """Add normalized inclusive time bounds to a SQL filter."""
    if since is not None:
        clauses.append("timestamp >= ?")
        parameters.append(_format_timestamp(_parse_timestamp(since)))
    if until is not None:
        clauses.append("timestamp <= ?")
        parameters.append(_format_timestamp(_parse_timestamp(until)))


def _validate_limit(limit: int | None) -> None:
    """Validate an optional result limit."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than zero")


def _validate_interval(since: datetime | None, until: datetime | None) -> None:
    """Validate an optional inclusive time interval."""
    if since is None or until is None:
        return
    if _parse_timestamp(since) > _parse_timestamp(until):
        raise ValueError("since must not be after until")


def _row_to_event(row: sqlite3.Row) -> Event:
    """Convert a SQLite row to an event, tolerating a malformed context blob."""
    raw_context = row["context"]
    try:
        context = json.loads(str(raw_context))
    except (TypeError, json.JSONDecodeError):
        context = {}
    if not isinstance(context, dict):
        context = {}
    return Event.from_dict(
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "name": row["name"],
            "level": row["level"],
            "message": row["message"],
            "context": cast(dict[str, object], context),
            "exception": row["exception"],
        }
    )


def _restrict_dir(path: Path) -> None:
    """Create a private directory and tighten its mode on POSIX systems."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _restrict_file(path: Path) -> None:
    """Tighten a monitoring file's mode on POSIX systems."""
    if os.name != "nt":
        path.chmod(0o600)


def _restrict_db_files(path: Path) -> None:
    """Restrict the SQLite database and its WAL/SHM sidecars."""
    _restrict_file(path)
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar.exists():
            _restrict_file(sidecar)


def append_event(event: Event, store: EventStore | None = None) -> Event:
    """Append an event to *store*, or to the default platform log store."""
    if store is not None:
        return store.append(event)
    with EventStore() as owned_store:
        return owned_store.append(event)


def read_events(
    store: EventStore | None = None,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Event]:
    """Read events from *store*, or from the default platform log store."""
    if store is not None:
        return store.read_events(limit=limit, since=since, until=until)
    with EventStore() as owned_store:
        return owned_store.read_events(limit=limit, since=since, until=until)


def search_events(
    query: str | None = None,
    store: EventStore | None = None,
    *,
    name: str | None = None,
    level: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[Event]:
    """Search events in *store*, or in the default platform log store."""
    if store is not None:
        return store.search_events(
            query,
            name=name,
            level=level,
            since=since,
            until=until,
            limit=limit,
        )
    with EventStore() as owned_store:
        return owned_store.search_events(
            query,
            name=name,
            level=level,
            since=since,
            until=until,
            limit=limit,
        )


def export_events(
    target: Path,
    store: EventStore | None = None,
    *,
    query: str | None = None,
    name: str | None = None,
    level: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> int:
    """Export events from *store*, or from the default platform log store."""
    if store is not None:
        return store.export_events(
            target,
            query=query,
            name=name,
            level=level,
            since=since,
            until=until,
            limit=limit,
        )
    with EventStore() as owned_store:
        return owned_store.export_events(
            target,
            query=query,
            name=name,
            level=level,
            since=since,
            until=until,
            limit=limit,
        )


def prune_old_events(store: EventStore | None = None) -> int:
    """Prune expired events from *store*, or from the default platform log store."""
    if store is not None:
        return store.prune_old_events()
    with EventStore() as owned_store:
        return owned_store.prune_old_events()


__all__ = [
    "RETENTION_DAYS",
    "EventStore",
    "append_event",
    "event_store_path",
    "export_events",
    "prune_old_events",
    "read_events",
    "search_events",
]
