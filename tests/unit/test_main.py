"""Entry-point tests: first-launch routing and corrupt-config backup."""

import logging
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

pytest.importorskip("tkinter")

from n8n_launcher.__main__ import (
    _backup_unreadable_config,
    _center,
    _close_session,
    _session_length,
    _signal_shutdown,
    _start_monitoring,
    main,
    stop_all,
)
from n8n_launcher.core.config import ConfigStore


class RootStub:
    """Minimal root exposing the geometry surface the entry point uses."""

    def __init__(self):
        self._geometry_calls: list[str] = []
        self.destroyed = False

    def geometry(self, value: str) -> None:
        self._geometry_calls.append(value)

    def update_idletasks(self) -> None:
        pass

    def winfo_screenwidth(self) -> int:
        return 7680

    def winfo_screenheight(self) -> int:
        return 2160

    def destroy(self) -> None:
        self.destroyed = True


def test_center_uses_responsive_window_size() -> None:
    root = RootStub()

    _center(root)

    assert root._geometry_calls[0] == "1600x900"
    assert root._geometry_calls[1] == "+3040+420"


def test_backup_unreadable_config_preserves_file_and_warns(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.path.write_text("{broken json", encoding="utf-8")
    messagebox = MagicMock()

    with patch("n8n_launcher.__main__.messagebox", messagebox):
        _backup_unreadable_config(store)

    assert not store.path.exists()
    backups = list(tmp_path.glob("launcher.db.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken json"
    messagebox.showwarning.assert_called_once()


def _patch_main(store: ConfigStore, wizard_value):
    """Return a namespace wiring ``main`` to *store* and a fake root.

    ``_start_monitoring`` is stubbed here for every ``main()`` test: left real,
    it would call ``bootstrap_logging(logs_dir())`` and register the pytest
    process in the developer's real monitoring journal, where the corrupt-config
    test's warning reads like a launcher failure. The patches stay un-entered:
    each test drives them through an ``ExitStack`` so extra patches (e.g.
    ``LauncherApp``) can be added.
    """
    root = RootStub()
    messagebox = MagicMock()
    monitor = MagicMock()
    docker = MagicMock()
    # Patching a class with a mock makes *calling* it return ``return_value``,
    # so the factory is what gets patched and ``docker`` is the instance
    # ``main`` works with.
    docker_factory = MagicMock(return_value=docker)
    wizard = MagicMock(return_value=wizard_value)
    return SimpleNamespace(
        root=root,
        messagebox=messagebox,
        monitor=monitor,
        docker=docker,
        wizard=wizard,
        patches=(
            patch("n8n_launcher.__main__.ConfigStore", return_value=store),
            patch("n8n_launcher.__main__.tk.Tk", return_value=root),
            patch("n8n_launcher.__main__.run_interactive_first_launch", wizard),
            patch("n8n_launcher.__main__.messagebox", messagebox),
            patch("n8n_launcher.__main__.resolve_docker_command", return_value="docker"),
            patch("n8n_launcher.__main__.DockerManager", docker_factory),
            patch("n8n_launcher.__main__._start_monitoring", return_value=monitor),
        ),
    )


def _enter(ctx) -> ExitStack:
    stack = ExitStack()
    for patch_call in ctx.patches:
        stack.enter_context(patch_call)
    return stack


@pytest.fixture(autouse=True)
def restore_root_handlers():
    """Undo any ``bootstrap_logging`` a test performed, once its store is closed.

    ``bootstrap_logging`` installs a handler on the root logger that keeps a
    hard reference to the store. A test that closes its store therefore leaves
    the *next* record of the process hitting a closed connection, which the
    monitoring handler reports as a ``--- Logging error ---`` traceback on
    stderr — noise that hides a real one.
    """
    root_logger = logging.getLogger()
    saved = list(root_logger.handlers)
    try:
        yield
    finally:
        for handler in list(root_logger.handlers):
            if handler not in saved:
                root_logger.removeHandler(handler)


def test_stop_all_continues_after_one_workspace_fails() -> None:
    store = MagicMock()
    store.load.return_value = SimpleNamespace(
        workspaces=[SimpleNamespace(id="first"), SimpleNamespace(id="second")]
    )
    docker = MagicMock()
    manager = MagicMock()
    manager.stop.side_effect = [RuntimeError("stop failed"), None]

    with patch("n8n_launcher.__main__.WorkspaceManager", return_value=manager) as manager_type:
        stop_all(store, docker)

    manager_type.assert_called_once_with(store, docker)
    assert manager.stop.call_args_list == [call("first"), call("second")]


def test_stop_all_does_nothing_when_config_cannot_be_loaded() -> None:
    store = MagicMock()
    store.load.side_effect = RuntimeError("unreadable")
    manager = MagicMock()

    with patch("n8n_launcher.__main__.WorkspaceManager", return_value=manager):
        stop_all(store, MagicMock())

    manager.stop.assert_not_called()


def test_signal_shutdown_stops_workspaces_then_exits() -> None:
    store = MagicMock()
    docker = MagicMock()

    with (
        patch("n8n_launcher.__main__.stop_all") as stop,
        pytest.raises(SystemExit) as excinfo,
    ):
        _signal_shutdown(store, docker, 15, None)

    assert excinfo.value.code == 0
    stop.assert_called_once_with(store, docker)


def test_start_monitoring_installs_the_event_store(tmp_path: Path) -> None:
    from n8n_launcher.monitoring.store import EventStore

    with patch("n8n_launcher.__main__.logs_dir", return_value=tmp_path):
        monitor = _start_monitoring()

    try:
        assert isinstance(monitor, EventStore)
        assert monitor.path == tmp_path / "events.db"
        assert any(event.message == "Surveillance active" for event in monitor.read_events())
    finally:
        assert monitor is not None
        monitor.close()


def test_start_monitoring_degrades_to_stderr_when_unwritable() -> None:
    with (
        patch("n8n_launcher.__main__.bootstrap_logging", side_effect=OSError("read-only")),
        patch("n8n_launcher.__main__.logging") as logging_module,
    ):
        assert _start_monitoring() is None

    logging_module.basicConfig.assert_called_once()


def test_session_length_is_formatted_as_h_mm_ss() -> None:
    with patch("n8n_launcher.__main__.time.monotonic", return_value=3725.4):
        assert _session_length(0) == "1:02:05"


def test_close_session_stops_the_workspaces_before_the_closing_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from n8n_launcher.core.models import WorkspaceState

    store = MagicMock()
    store.load.return_value = SimpleNamespace(
        workspaces=[
            SimpleNamespace(id="running", state=WorkspaceState.RUNNING),
            SimpleNamespace(id="stopped", state=WorkspaceState.STOPPED),
        ]
    )
    logged_before_teardown: list[logging.LogRecord] = []

    def teardown(_store, _docker) -> None:
        logged_before_teardown.extend(caplog.records)

    with (
        patch("n8n_launcher.__main__.stop_all", side_effect=teardown) as stop,
        patch("n8n_launcher.__main__.time.monotonic", return_value=65.0),
        caplog.at_level(logging.INFO, logger="n8n_launcher.__main__"),
    ):
        _close_session(store, MagicMock(), started=0.0)

    # The teardown must be logged *before* the session is declared closed, else
    # the journal claims a shutdown that has not happened yet.
    assert stop.call_count == 1
    assert logged_before_teardown == []
    closing = caplog.records[-1]
    assert closing.getMessage().startswith("Surveillance terminée — session de 0:01:05")
    assert "1 workspace(s) en cours à la fermeture" in closing.getMessage()


def test_close_session_records_the_end_of_a_session_with_an_unreadable_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = MagicMock()
    store.load.side_effect = RuntimeError("unreadable")

    with (
        patch("n8n_launcher.__main__.stop_all") as stop,
        caplog.at_level(logging.INFO, logger="n8n_launcher.__main__"),
    ):
        _close_session(store, MagicMock(), started=0.0)

    # A session that could not even read its config still happened: leaving it
    # unbracketed is what makes a hard kill look like a launcher still running.
    stop.assert_called_once()
    assert "0 workspace(s) en cours à la fermeture" in caplog.records[-1].getMessage()


def test_close_session_persists_the_closing_event_in_the_open_store(tmp_path: Path) -> None:
    from n8n_launcher.monitoring.bootstrap import bootstrap_logging
    from n8n_launcher.monitoring.store import EventStore

    journal = EventStore(tmp_path / "events.db")
    try:
        bootstrap_logging(store=journal)
        with patch("n8n_launcher.__main__.stop_all"):
            _close_session(MagicMock(), MagicMock(), started=0.0)
        messages = [event.message for event in journal.read_events()]
    finally:
        journal.close()

    # The whole point of closing the session before the store: an event emitted
    # afterwards is dropped by the monitoring handler instead of journaled.
    assert any(message.startswith("Surveillance terminée") for message in messages)


def test_main_closes_the_journal_session_after_the_app_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from n8n_launcher.core.models import AppConfig

    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    ctx = _patch_main(store, wizard_value=None)
    closing_events_when_store_closed: list[int] = []
    ctx.monitor.close.side_effect = lambda *_args: closing_events_when_store_closed.append(
        len([record for record in caplog.records if "Surveillance terminée" in record.getMessage()])
    )

    with (
        _enter(ctx) as stack,
        stack.enter_context(
            patch(
                "n8n_launcher.__main__.LauncherApp",
                return_value=SimpleNamespace(run=MagicMock()),
            )
        ),
        patch("n8n_launcher.__main__.stop_all") as stop,
        caplog.at_level(logging.INFO, logger="n8n_launcher.__main__"),
    ):
        main()

    # The session is closed exactly once, by shutting the workspaces down, and
    # it happens while the store is still open.
    stop.assert_called_once_with(store, ctx.docker)
    assert closing_events_when_store_closed == [1]


def test_main_passes_the_monitor_to_the_app(tmp_path: Path) -> None:
    from n8n_launcher.core.models import AppConfig

    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx) as stack:
        launcher = stack.enter_context(
            patch(
                "n8n_launcher.__main__.LauncherApp",
                return_value=SimpleNamespace(run=MagicMock()),
            )
        )
        main()

    assert launcher.call_args.kwargs["monitor"] is ctx.monitor
    ctx.monitor.close.assert_called_once()


def test_main_missing_config_runs_wizard_without_backup(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx):
        main()

    ctx.wizard.assert_called_once()
    assert ctx.root.destroyed
    ctx.messagebox.showwarning.assert_not_called()
    assert not list(tmp_path.glob("launcher.db.corrupt-*"))


def test_main_corrupt_config_is_backed_up_before_wizard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.path.write_text("{broken json", encoding="utf-8")
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx), caplog.at_level(logging.INFO, logger="n8n_launcher.__main__"):
        main()

    ctx.wizard.assert_called_once()
    assert ctx.root.destroyed
    ctx.messagebox.showwarning.assert_called_once()
    backups = list(tmp_path.glob("launcher.db.corrupt-*"))
    assert len(backups) == 1
    # The journal entry must name the file and carry the cause: an
    # unattributable "configuration illisible" is what made this warning
    # undiagnosable from the monitoring panel. Looked up by level, because the
    # session now closes with an INFO line of its own.
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert str(store.path) in warnings[0].getMessage()
    assert warnings[0].exc_info is not None
    assert any("Surveillance terminée" in record.getMessage() for record in caplog.records)


def test_main_valid_config_skips_wizard(tmp_path: Path) -> None:
    from n8n_launcher.core.models import AppConfig

    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx) as stack:
        launcher = stack.enter_context(patch("n8n_launcher.__main__.LauncherApp"))
        main()

    ctx.wizard.assert_not_called()
    ctx.messagebox.showwarning.assert_not_called()
    assert ctx.root.destroyed is False
    launcher.return_value.run.assert_called_once()
