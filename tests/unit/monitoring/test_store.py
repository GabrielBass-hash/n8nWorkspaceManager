"""Tests for the SQLite-backed monitoring event store."""

import json
import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.store import RETENTION_DAYS, EventStore, event_store_path

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def make_store(tmp_path: Path, **kwargs) -> EventStore:
    """Build a store rooted in *tmp_path* with a frozen clock by default."""
    kwargs.setdefault("clock", lambda: NOW)
    return EventStore(logs_dir=tmp_path, **kwargs)


def test_retention_default_is_thirty_days() -> None:
    assert RETENTION_DAYS == 30


def test_event_store_path_uses_explicit_directory(tmp_path: Path) -> None:
    assert event_store_path(tmp_path) == tmp_path / "events.db"


def test_event_store_path_falls_back_to_platform_logs_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("n8n_launcher.monitoring.store.default_logs_dir", lambda: tmp_path)

    assert event_store_path() == tmp_path / "events.db"


def test_init_rejects_both_logs_dir_and_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="logs_dir"):
        EventStore(logs_dir=tmp_path, path=tmp_path / "other.db")


def test_init_rejects_negative_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retention_days"):
        EventStore(logs_dir=tmp_path, retention_days=-1)


def test_init_creates_the_database_and_restricts_permissions(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    assert store.path.exists()
    assert store.connection_is_open is True
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    store.close()


def test_append_assigns_an_id_and_reads_back_in_order(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        first = store.append(Event(name="a", timestamp=NOW - timedelta(seconds=2)))
        second = store.append(Event(name="b", timestamp=NOW - timedelta(seconds=1)))

        assert first.id == 1
        assert second.id == 2
        assert [event.name for event in store.read_events()] == ["a", "b"]


def test_append_prunes_events_beyond_retention(tmp_path: Path) -> None:
    with make_store(tmp_path, retention_days=30) as store:
        store.append(Event(name="ancient", timestamp=NOW - timedelta(days=31)))
        store.append(Event(name="recent", timestamp=NOW))

        assert [event.name for event in store.read_events()] == ["recent"]


def test_prune_old_events_deletes_rows_written_outside_the_retention_window(tmp_path: Path) -> None:
    with make_store(tmp_path, retention_days=7) as store:
        # Every append already prunes, so a row older than the window can only
        # be observed when it is inserted by another writer (e.g. a second
        # launcher process) straight into the database file.
        aged = (NOW - timedelta(days=30)).isoformat()
        store.append(Event(name="fresh", timestamp=NOW))
        with sqlite3.connect(store.path) as raw:
            raw.execute(
                "INSERT INTO events(timestamp, name, level, message, context, exception) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (aged, "stale", "INFO", "", "{}", None),
            )

        assert store.prune_old_events() == 1
        assert [event.name for event in store.read_events()] == ["fresh"]


def test_read_events_applies_limit_and_time_bounds(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        for offset in range(4):
            store.append(Event(name=f"e{offset}", timestamp=NOW - timedelta(hours=offset)))

        assert len(store.read_events(limit=2)) == 2
        bounded = store.read_events(
            since=NOW - timedelta(hours=2, minutes=1),
            until=NOW - timedelta(hours=1),
        )
        assert [event.name for event in bounded] == ["e2", "e1"]


def test_latest_events_returns_newest_first_and_honors_limit(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        for offset in range(4):
            store.append(Event(name=f"e{offset}", timestamp=NOW - timedelta(hours=offset)))

        assert [event.name for event in store.latest_events(2)] == ["e0", "e1"]


def test_latest_events_rejects_invalid_limit(tmp_path: Path) -> None:
    with make_store(tmp_path) as store, pytest.raises(ValueError, match="limit"):
        store.latest_events(0)


def test_read_events_rejects_invalid_limit_or_interval(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        with pytest.raises(ValueError, match="limit"):
            store.read_events(limit=0)
        with pytest.raises(ValueError, match="since"):
            store.read_events(since=NOW, until=NOW - timedelta(hours=1))


def test_search_events_filters_by_query_name_and_level(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.append(Event(name="docker.up", message="container started", context={"ws": "a"}))
        store.append(Event(name="ci.run", level="error", message="pipeline failed"))
        store.append(Event(name="docker.down", message="container stopped"))

        assert len(store.search_events("pipeline")) == 1
        assert len(store.search_events(None, name="docker")) == 2
        assert len(store.search_events(None, level="error")) == 1
        assert len(store.search_events(None, name="docker", level="info")) == 2


def test_search_events_matches_context_and_uses_like_wildcards_safely(tmp_path: Path) -> None:
    with make_store(tmp_path) as store:
        store.append(Event(name="ws", context={"port": 5678}))

        assert len(store.search_events("5678")) == 1
        # A LIKE wildcard typed by the user must not match everything.
        assert store.search_events("%") == []


def test_export_events_writes_a_private_json_array(tmp_path: Path) -> None:
    target = tmp_path / "export" / "events.json"
    with make_store(tmp_path) as store:
        store.append(Event(name="a", message="m", context={"k": "v"}))
        count = store.export_events(target)
        payload = json.loads(target.read_text(encoding="utf-8"))

    assert count == 1
    assert payload[0]["name"] == "a"
    assert payload[0]["context"] == {"k": "v"}
    assert list(target.parent.glob("*.tmp")) == []
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_export_events_honors_filters(tmp_path: Path) -> None:
    target = tmp_path / "events.json"
    with make_store(tmp_path) as store:
        store.append(Event(name="a", level="error"))
        store.append(Event(name="b"))

        assert store.export_events(target, level="ERROR") == 1


def test_operations_fail_once_the_store_is_closed(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.close()

    assert store.connection_is_open is False
    with pytest.raises(RuntimeError, match="closed"):
        store.append(Event(name="a"))
    with pytest.raises(RuntimeError, match="closed"):
        store.read_events()


def test_appends_are_safe_across_threads(tmp_path: Path) -> None:
    def worker(store: EventStore) -> None:
        for index in range(20):
            store.append(Event(name=f"thread-{index}"))

    with make_store(tmp_path) as store:
        threads = [threading.Thread(target=worker, args=(store,)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(store.read_events()) == 80
