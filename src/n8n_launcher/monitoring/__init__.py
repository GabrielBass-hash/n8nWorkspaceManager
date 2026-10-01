"""Application observability, event persistence, and logging integration."""

from .bootstrap import bootstrap_logging, capture_exceptions, install_exception_capture
from .events import Event, MonitoringEvent
from .redaction import redact_secrets
from .store import (
    RETENTION_DAYS,
    EventStore,
    append_event,
    event_store_path,
    export_events,
    prune_old_events,
    read_events,
    search_events,
)

__all__ = [
    "RETENTION_DAYS",
    "Event",
    "EventStore",
    "MonitoringEvent",
    "append_event",
    "bootstrap_logging",
    "capture_exceptions",
    "event_store_path",
    "export_events",
    "install_exception_capture",
    "prune_old_events",
    "read_events",
    "redact_secrets",
    "search_events",
]
