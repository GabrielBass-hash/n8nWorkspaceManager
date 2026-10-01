"""Structured application monitoring events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from .redaction import _jsonable, redact_secrets


def _utc_timestamp(value: datetime) -> datetime:
    """Return *value* as a timezone-aware UTC timestamp."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime:
    """Parse an event timestamp from a mapping or database payload."""
    if isinstance(value, datetime):
        return _utc_timestamp(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return _utc_timestamp(datetime.fromisoformat(text))
    raise ValueError("event timestamp must be an ISO-8601 string or datetime")


@dataclass(frozen=True, slots=True)
class Event:
    """One redacted, structured application observation.

    ``name`` identifies the occurrence, ``level`` carries the usual logging
    levels, and ``context`` contains JSON-friendly structured attributes.
    ``exception`` holds an already-formatted traceback when an event describes
    a failure. The store assigns the optional database ``id`` after append.
    """

    name: str
    message: str = ""
    level: str = "INFO"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    context: dict[str, object] = field(default_factory=dict)
    exception: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Normalize timestamps and redact every user-controlled field."""
        if not isinstance(self.timestamp, datetime):
            raise TypeError("event timestamp must be a datetime")
        if not self.name.strip():
            raise ValueError("event name must not be empty")
        if not isinstance(self.context, dict):
            raise TypeError("event context must be a dictionary")
        normalized_context = _jsonable(self.context)
        if not isinstance(normalized_context, dict):
            normalized_context = {}
        normalized_name = redact_secrets(self.name)
        normalized_message = redact_secrets(self.message)
        normalized_exception = (
            redact_secrets(self.exception) if self.exception is not None else None
        )
        object.__setattr__(self, "name", cast(str, normalized_name))
        object.__setattr__(self, "message", cast(str, normalized_message))
        object.__setattr__(self, "level", str(self.level).upper() or "INFO")
        object.__setattr__(self, "timestamp", _utc_timestamp(self.timestamp))
        object.__setattr__(self, "context", cast(dict[str, object], normalized_context))
        object.__setattr__(
            self,
            "exception",
            cast(str, normalized_exception) if normalized_exception is not None else None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly representation of the event."""
        return {
            "id": self.id,
            "timestamp": _format_timestamp(self.timestamp),
            "name": self.name,
            "level": self.level,
            "message": self.message,
            "context": self.context,
            "exception": self.exception,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Event:
        """Build an event from a mapping produced by :meth:`to_dict`."""
        timestamp = _parse_timestamp(data.get("timestamp"))
        raw_context = data.get("context", {})
        context = raw_context if isinstance(raw_context, dict) else {}
        raw_id = data.get("id")
        event_id = int(raw_id) if isinstance(raw_id, (int, str)) and str(raw_id) else None
        return cls(
            name=str(data.get("name", "")),
            message=str(data.get("message", "")),
            level=str(data.get("level", "INFO")),
            timestamp=timestamp,
            context=cast(dict[str, object], context),
            exception=(str(data["exception"]) if data.get("exception") is not None else None),
            id=event_id,
        )


def _format_timestamp(value: datetime) -> str:
    """Format a UTC timestamp for stable lexical SQLite ordering."""
    return _utc_timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


MonitoringEvent = Event

__all__ = ["Event", "MonitoringEvent"]
