"""Global logging and uncaught-exception integration for monitoring."""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import cast

from .events import Event
from .redaction import redact_secrets
from .store import EventStore

_BOOTSTRAP_LOCK = threading.RLock()
_ACTIVE_STORE: EventStore | None = None
_PREVIOUS_SYS_EXCEPTHOOK: Callable[..., object] | None = None
_PREVIOUS_THREADING_EXCEPTHOOK: Callable[..., object] | None = None
_OWNED_HANDLER_ATTRIBUTE = "_n8n_launcher_bootstrap_handler"
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class _RedactingFormatter(logging.Formatter):
    """Format records while removing secret-shaped values from the result."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a redacted formatted log record."""
        return cast(str, redact_secrets(super().format(record)))


class _MonitoringHandler(logging.Handler):
    """Write standard logging records into the monitoring event store."""

    def __init__(self, store: EventStore) -> None:
        """Create a handler backed by *store*."""
        super().__init__()
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        """Persist *record* without allowing monitoring failures to break logging."""
        try:
            context: dict[str, object] = {
                "logger": record.name,
                "thread": record.threadName,
                "process": record.processName,
                "process_id": record.process,
                "thread_id": record.thread,
            }
            for key, value in record.__dict__.items():
                if key not in _STANDARD_RECORD_FIELDS:
                    context[key] = value
            exception = None
            if record.exc_info:
                exception = logging.Formatter().formatException(record.exc_info)
            self.store.append(
                Event(
                    name=record.name,
                    level=record.levelname,
                    message=record.getMessage(),
                    context=context,
                    exception=exception,
                )
            )
        except Exception:
            self.handleError(record)
            return


def _append_exception(
    store: EventStore,
    exception: BaseException,
    *,
    source: str,
    context: Mapping[str, object] | None = None,
) -> None:
    """Append one exception event while preserving the original failure path."""
    try:
        store.append(
            Event(
                name="uncaught_exception",
                level="ERROR",
                message=str(exception),
                context={"source": source, **(dict(context) if context is not None else {})},
                exception="".join(
                    traceback.format_exception(type(exception), exception, exception.__traceback__)
                ),
            )
        )
    except Exception:
        return


def _make_sys_hook() -> Callable[..., object]:
    """Build a process exception hook that uses the currently active store."""

    def hook(
        exception_type: type[BaseException],
        exception: BaseException,
        exception_traceback: TracebackType | None,
    ) -> object:
        """Capture a process-level exception and delegate to the prior hook."""
        store = _ACTIVE_STORE
        if store is not None:
            _append_exception(store, exception, source="sys.excepthook")
        previous = _PREVIOUS_SYS_EXCEPTHOOK
        if previous is not None:
            return previous(exception_type, exception, exception_traceback)
        return None

    return hook


def _make_threading_hook() -> Callable[..., object]:
    """Build a thread exception hook that uses the currently active store."""

    def hook(args: threading.ExceptHookArgs) -> object:
        """Capture a thread-level exception and delegate to the prior hook."""
        store = _ACTIVE_STORE
        if store is not None and args.exc_value is not None:
            _append_exception(store, args.exc_value, source="threading.excepthook")
        previous = _PREVIOUS_THREADING_EXCEPTHOOK
        if previous is not None:
            return previous(args)
        return None

    return hook


def install_exception_capture(store: EventStore) -> None:
    """Install process and thread exception hooks that write to *store*."""
    global _ACTIVE_STORE, _PREVIOUS_SYS_EXCEPTHOOK, _PREVIOUS_THREADING_EXCEPTHOOK
    with _BOOTSTRAP_LOCK:
        if _ACTIVE_STORE is None:
            _PREVIOUS_SYS_EXCEPTHOOK = sys.excepthook
            _PREVIOUS_THREADING_EXCEPTHOOK = threading.excepthook
            sys.excepthook = _make_sys_hook()
            threading.excepthook = _make_threading_hook()
        _ACTIVE_STORE = store


@contextmanager
def capture_exceptions(
    store: EventStore,
    *,
    context: Mapping[str, object] | None = None,
) -> Iterator[EventStore]:
    """Capture exceptions raised inside the context and re-raise them unchanged."""
    try:
        yield store
    except BaseException as exc:
        _append_exception(store, exc, source="context", context=context)
        raise


def bootstrap_logging(
    logs_dir: Path | None = None,
    *,
    store: EventStore | None = None,
    level: int = logging.INFO,
) -> EventStore:
    """Configure global logging, monitoring persistence, and exception capture."""
    if logs_dir is not None and store is not None:
        raise ValueError("logs_dir and store cannot both be provided")
    active_store = store if store is not None else EventStore(logs_dir=logs_dir)
    with _BOOTSTRAP_LOCK:
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        for handler in list(root_logger.handlers):
            if getattr(handler, _OWNED_HANDLER_ATTRIBUTE, False):
                root_logger.removeHandler(handler)
                handler.close()
        stream_handler = logging.StreamHandler()
        setattr(stream_handler, _OWNED_HANDLER_ATTRIBUTE, True)
        stream_handler.setFormatter(
            _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        event_handler = _MonitoringHandler(active_store)
        setattr(event_handler, _OWNED_HANDLER_ATTRIBUTE, True)
        root_logger.addHandler(stream_handler)
        root_logger.addHandler(event_handler)
        install_exception_capture(active_store)
    return active_store


__all__ = ["bootstrap_logging", "capture_exceptions", "install_exception_capture"]
