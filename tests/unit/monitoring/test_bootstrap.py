"""Tests for the global logging and exception-capture bootstrap."""

import logging
import sys
import threading
from pathlib import Path

import pytest

from n8n_launcher.monitoring import bootstrap as bootstrap_module
from n8n_launcher.monitoring.bootstrap import (
    bootstrap_logging,
    capture_exceptions,
    install_exception_capture,
)
from n8n_launcher.monitoring.store import EventStore


@pytest.fixture(autouse=True)
def restore_logging_state():
    """Restore root handlers, exception hooks, and bootstrap globals after each test."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    sys_hook = sys.excepthook
    threading_hook = threading.excepthook
    active_store = bootstrap_module._ACTIVE_STORE
    try:
        yield
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)
        sys.excepthook = sys_hook
        threading.excepthook = threading_hook
        bootstrap_module._ACTIVE_STORE = active_store


def owned_handlers() -> list[logging.Handler]:
    """Return the handlers installed by the bootstrap (pytest adds its own)."""
    return [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_n8n_launcher_bootstrap_handler", False)
    ]


def test_bootstrap_logging_installs_handlers_and_returns_the_store(tmp_path: Path) -> None:
    store = bootstrap_logging(tmp_path)

    try:
        assert isinstance(store, EventStore)
        assert store.path == tmp_path / "events.db"
        assert len(owned_handlers()) == 2
    finally:
        store.close()


def test_bootstrap_logging_rejects_logs_dir_together_with_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="logs_dir"):
        bootstrap_logging(tmp_path, store=EventStore(logs_dir=tmp_path / "other"))


def test_logging_records_are_persisted_as_events(tmp_path: Path) -> None:
    store = bootstrap_logging(tmp_path)
    try:
        logging.getLogger("n8n_launcher.test").warning("workspace %s stopped", "ws1")

        events = store.read_events()
        assert [event.name for event in events] == ["n8n_launcher.test"]
        assert events[0].message == "workspace ws1 stopped"
        assert events[0].level == "WARNING"
        assert events[0].context["logger"] == "n8n_launcher.test"
        assert events[0].context["thread"] is not None
    finally:
        store.close()


def test_extra_record_attributes_land_in_the_event_context(tmp_path: Path) -> None:
    store = bootstrap_logging(tmp_path)
    try:
        logging.getLogger("n8n_launcher.test").info("published", extra={"workspace_id": "ws1"})

        assert store.read_events()[0].context["workspace_id"] == "ws1"
    finally:
        store.close()


def test_exception_information_is_persisted(tmp_path: Path) -> None:
    store = bootstrap_logging(tmp_path)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("n8n_launcher.test").exception("sync failed")

        event = store.read_events()[0]
        assert event.level == "ERROR"
        assert event.exception is not None
        assert "ValueError: boom" in event.exception
    finally:
        store.close()


def test_stream_output_is_redacted(tmp_path: Path, capsys) -> None:
    store = bootstrap_logging(tmp_path)
    try:
        logging.getLogger("n8n_launcher.test").error("login password=hunter2")

        captured = capsys.readouterr()
        assert "hunter2" not in captured.err
        assert "[REDACTED]" in captured.err
    finally:
        store.close()


def test_bootstrap_is_idempotent_and_keeps_foreign_handlers(tmp_path: Path) -> None:
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    first = bootstrap_logging(tmp_path)
    second = bootstrap_logging(tmp_path)
    try:
        assert len(owned_handlers()) == 2
        assert foreign in logging.getLogger().handlers
    finally:
        first.close()
        second.close()


def test_capture_exceptions_records_and_reraises(tmp_path: Path) -> None:
    with EventStore(logs_dir=tmp_path) as store:
        with (
            pytest.raises(ValueError, match="boom"),
            capture_exceptions(store, context={"operation": "publish"}),
        ):
            raise ValueError("boom")

        event = store.read_events()[0]
        assert event.name == "uncaught_exception"
        assert event.level == "ERROR"
        assert event.context["operation"] == "publish"
        assert event.context["source"] == "context"
        assert "ValueError: boom" in (event.exception or "")


def test_capture_exceptions_stays_silent_on_success(tmp_path: Path) -> None:
    with EventStore(logs_dir=tmp_path) as store:
        with capture_exceptions(store):
            pass

        assert store.read_events() == []


def test_install_exception_capture_records_process_and_thread_failures(tmp_path: Path) -> None:
    sys.excepthook = lambda *_args: None
    threading.excepthook = lambda _args: None
    with EventStore(logs_dir=tmp_path) as store:
        install_exception_capture(store)

        sys.excepthook(ValueError, ValueError("process boom"), None)
        try:
            raise RuntimeError("thread boom")
        except RuntimeError as exc:
            args = threading.ExceptHookArgs(
                (type(exc), exc, exc.__traceback__, threading.current_thread())
            )
            threading.excepthook(args)

        events = store.search_events(None, name="uncaught_exception")
        sources = {event.context.get("source"): event for event in events}
        assert set(sources) == {"sys.excepthook", "threading.excepthook"}
        assert "ValueError: process boom" in (sources["sys.excepthook"].exception or "")
        assert "RuntimeError: thread boom" in (sources["threading.excepthook"].exception or "")


def test_handler_failure_never_breaks_logging(tmp_path: Path) -> None:
    class BrokenStore:
        def append(self, event):
            raise RuntimeError("store is gone")

    bootstrap_logging(store=BrokenStore())
    logging.getLogger("n8n_launcher.test").info("still logged")
