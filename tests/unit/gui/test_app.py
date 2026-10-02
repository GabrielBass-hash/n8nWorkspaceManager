"""The application shell: display checks, the Qt application and the lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.gui import app as app_module
from n8n_launcher.gui.app import (
    GuiUnavailable,
    LauncherApp,
    clear_shutdown,
    display_available,
    ensure_application,
    request_shutdown,
    run_gui,
    self_test,
    shutdown_requested,
)


@pytest.fixture(autouse=True)
def _forget_shutdown():
    """Keep the process-wide shutdown flag from leaking between tests."""
    clear_shutdown()
    yield
    clear_shutdown()


def test_display_is_available_when_a_platform_plugin_is_forced(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert display_available() is True


def test_display_is_unavailable_on_a_linux_session_without_a_display(monkeypatch) -> None:
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    assert display_available() is False


def test_display_is_available_on_wayland(monkeypatch) -> None:
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    assert display_available() is True


def test_display_is_assumed_available_off_linux(monkeypatch) -> None:
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    assert display_available() is True


def test_ensure_application_returns_a_singleton() -> None:
    first = ensure_application()
    assert ensure_application() is first


def test_request_shutdown_reports_whether_a_gui_is_running() -> None:
    ensure_application()
    assert request_shutdown() is True
    assert shutdown_requested() is True


def test_run_gui_refuses_without_a_display(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "display_available", lambda: False)
    with pytest.raises(GuiUnavailable, match="affichage"):
        run_gui(MagicMock(), MagicMock())


def test_launcher_run_wires_the_observer_and_returns_on_shutdown() -> None:
    manager = MagicMock()
    manager.list.return_value = []
    ensure_application()
    request_shutdown()

    LauncherApp(MagicMock(), manager).run()

    manager.reconcile_all.assert_called_once()
    manager.add_observer.assert_called_once()
    manager.remove_observer.assert_called_once()


def test_the_window_subscribes_and_unsubscribes() -> None:
    from n8n_launcher.gui.window import MainWindow

    manager = MagicMock()
    manager.list.return_value = []
    window = MainWindow(manager)
    manager.add_observer.assert_called_once()

    window.show()
    window.close()

    manager.remove_observer.assert_called_once()


def test_self_test_builds_the_window_and_reports_success(qt_app, monkeypatch) -> None:
    """The smoke check a packager runs must end at 0, over its own config only."""
    opened: list[Path] = []
    real_store = app_module.ConfigStore

    def spy(path: Path | None = None) -> ConfigStore:
        store = real_store(path)
        opened.append(store.path)
        return store

    monkeypatch.setattr(app_module, "ConfigStore", spy)

    assert self_test() == 0

    # The user's real configuration is never the one the smoke check touches.
    assert opened, "the self-test must build a store to open the board over"
    assert all("n8n-launcher-self-test-" in str(path) for path in opened)
    # The store holds its SQLite handle for the process lifetime: an open handle
    # makes the file undeletable on Windows, so the scratch directory has to be
    # gone or the check leaks a locked database on every run.
    assert not any(path.parent.exists() for path in opened)


def test_self_test_refuses_without_a_display(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app_module, "display_available", lambda: False)

    assert self_test() == 1

    assert "no display available" in capsys.readouterr().err


def test_self_test_reports_a_shell_that_cannot_be_built(monkeypatch, capsys) -> None:
    """A bundle that lost its Qt plugin has to fail loudly, not hang or pass."""
    real_store = app_module.ConfigStore

    def spy(path=None):
        # Still a real store: the report must name the window failure, never be
        # masked by whatever goes wrong while cleaning up after it.
        return real_store(path)

    monkeypatch.setattr(app_module, "ConfigStore", spy)
    monkeypatch.setattr(
        app_module.MainWindow,
        "__init__",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert self_test() == 1

    assert "boom" in capsys.readouterr().err


def test_self_test_reports_even_without_a_console(monkeypatch) -> None:
    """A --windowed Windows build has no stderr: the verdict must still land."""
    monkeypatch.setattr(app_module.sys, "stderr", None)
    monkeypatch.setattr(app_module, "display_available", lambda: False)

    assert self_test() == 1


def test_self_test_never_fails_on_its_own_cleanup(qt_app, monkeypatch) -> None:
    """A scratch directory that cannot be removed must not decide the verdict."""
    seen: list[bool] = []
    real = app_module.shutil.rmtree

    def spy(path, ignore_errors: bool = False, **kwargs):
        seen.append(ignore_errors)
        return real(path, ignore_errors=ignore_errors, **kwargs)

    monkeypatch.setattr(app_module.shutil, "rmtree", spy)

    assert self_test() == 0
    assert seen == [True]
