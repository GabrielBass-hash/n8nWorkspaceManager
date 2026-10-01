"""Tests for the structured monitoring event model."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from n8n_launcher.monitoring.events import Event, MonitoringEvent


def test_monitoring_event_is_an_alias_of_event() -> None:
    assert MonitoringEvent is Event


def test_event_defaults_to_info_level_and_no_id() -> None:
    event = Event(name="workspace.start")

    assert event.level == "INFO"
    assert event.id is None
    assert event.message == ""
    assert event.context == {}
    assert event.exception is None


def test_event_normalizes_naive_timestamp_to_utc() -> None:
    event = Event(name="a", timestamp=datetime(2026, 1, 2, 3, 4, 5))

    assert event.timestamp.tzinfo is not None
    assert event.timestamp == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_event_converts_other_timezones_to_utc() -> None:
    event = Event(name="a", timestamp=datetime(2026, 1, 2, 5, tzinfo=timezone(timedelta(hours=2))))

    assert event.timestamp == datetime(2026, 1, 2, 3, tzinfo=UTC)


def test_event_uppercases_level() -> None:
    assert Event(name="a", level="warning").level == "WARNING"


def test_event_redacts_message_context_and_exception() -> None:
    event = Event(
        name="workspace.start",
        message="démarrage avec password=hunter2",
        context={"api_key": "abcd", "port": 5678},
        exception="boom token=ghp_abcdefghijklmnop",
    )

    assert "hunter2" not in event.message
    assert event.context["api_key"] == "[REDACTED]"
    assert event.context["port"] == 5678
    assert "ghp_abcdefghijklmnop" not in (event.exception or "")


def test_event_rejects_blank_name() -> None:
    with pytest.raises(ValueError, match="name"):
        Event(name="   ")


def test_event_rejects_non_datetime_timestamp() -> None:
    with pytest.raises(TypeError, match="timestamp"):
        Event(name="a", timestamp="2026-01-01T00:00:00Z")  # type: ignore[arg-type]


def test_event_rejects_non_dict_context() -> None:
    with pytest.raises(TypeError, match="context"):
        Event(name="a", context=["nope"])  # type: ignore[arg-type]


def test_event_to_dict_is_json_friendly() -> None:
    payload = Event(name="a", message="m", context={"k": "v"}).to_dict()

    assert set(payload) == {"id", "timestamp", "name", "level", "message", "context", "exception"}
    assert payload["timestamp"].endswith("Z")
    assert payload["context"] == {"k": "v"}


def test_event_round_trips_through_dict() -> None:
    original = Event(name="a", message="m", level="error", context={"k": "v"}, id=7)

    restored = Event.from_dict(original.to_dict())

    assert restored == original


def test_from_dict_parses_zulu_timestamp() -> None:
    restored = Event.from_dict({"name": "a", "timestamp": "2026-01-02T03:04:05.000000Z"})

    assert restored.timestamp == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_from_dict_rejects_unparseable_timestamp() -> None:
    with pytest.raises(ValueError):
        Event.from_dict({"name": "a", "timestamp": "hier"})


def test_from_dict_tolerates_missing_and_malformed_fields() -> None:
    restored = Event.from_dict({"name": "a", "timestamp": "2026-01-02T03:04:05Z", "context": "bad"})

    assert restored.context == {}
    assert restored.id is None
    assert restored.exception is None
    assert restored.level == "INFO"
