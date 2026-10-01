"""The application shell.

This is the seam the entry point calls into. It owns exactly three things: the
:class:`QApplication`, the signal-aware lifecycle of the event loop, and the one
window. Business logic lives in :mod:`n8n_launcher.workspaces`; the shell asks
and renders.

The shutdown is the subtle part. ``__main__`` installs the SIGTERM/SIGINT
handlers, but a Python signal handler only runs when the interpreter regains
control — and inside ``QApplication.exec()`` it does not, because the loop is in
C++. A :class:`QTimer` gives the interpreter a callback on a fixed interval, so
the handler runs. That handler must not ``raise SystemExit`` inside a Qt slot:
PySide6 routes an unhandled slot exception to ``sys.excepthook`` (which
monitoring installs), so a ``SystemExit`` would be logged and swallowed and the
launcher would never quit. Instead the handler calls :func:`request_shutdown`,
which asks the running application to ``quit()``; the loop returns normally and
``main``'s ``finally`` performs the ordered teardown.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..core.config import ConfigStore
from ..core.models import AppConfig
from ..docker.manager import DockerManager
from ..monitoring.store import EventStore
from ..workspaces.manager import WorkspaceManager
from .theme import apply_theme
from .window import MainWindow

#: How often the interpreter gets a callback so Python signal handlers can run.
SIGNAL_TICK_MS = 200

#: Flag a packager can run on a finished artifact to prove it can show a window.
SELF_TEST_FLAG = "--self-test"

_shutdown = threading.Event()


def request_shutdown() -> bool:
    """Ask a running Qt application to quit.

    Returns True when a :class:`QApplication` exists (and ``quit()`` was asked
    for), False when the process is running without one. The signal handler uses
    this to choose between letting Qt unwind and terminating the process.
    """
    _shutdown.set()
    app = QApplication.instance()
    if app is None:
        return False
    app.quit()
    return True


def shutdown_requested() -> bool:
    """Return True once :func:`request_shutdown` has been called."""
    return _shutdown.is_set()


def clear_shutdown() -> None:
    """Forget a shutdown request (tests and a fresh start)."""
    _shutdown.clear()


def display_available() -> bool:
    """Return True when a GUI can be opened on this host.

    A Linux session without a display (a service, an ssh command) would make Qt
    abort the process rather than fail: ``QApplication`` construction without a
    platform plugin calls ``qFatal``. Checking the environment first lets the
    entry point log a refusal instead of dying inside C++.
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return True
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


class GuiUnavailable(RuntimeError):
    """Raised when the shell cannot be shown on this host."""


def ensure_application() -> QApplication:
    """Return the process's :class:`QApplication`, creating it if needed."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication(sys.argv)


def run_gui(
    store: ConfigStore,
    manager: WorkspaceManager,
    monitor: EventStore | None = None,
) -> None:
    """Open the launcher shell over *manager*, with *store* and *monitor* wired.

    Args:
        store: The persistent configuration every view reads and writes through.
        manager: The workspace controller; a view never touches Docker itself.
        monitor: The event journal, or ``None`` when it could not be opened.

    Raises:
        GuiUnavailable: When there is no display to open the window on.
    """
    LauncherApp(store, manager, monitor).run()


def self_test() -> int:
    """Build the real window once and report whether this binary can show it.

    A bundle that lost its Qt platform plugin during packaging is the one
    failure a packager cannot see: the app starts, prints nothing and dies with
    no window. This creates the application object, the themed main window and
    one turn of the event loop over a throwaway configuration, then returns 0.

    It reads and writes nothing outside a temporary directory, so it is safe to
    run on a build machine, and it returns an exit code instead of raising
    because a packager's only question is "did it start?".
    """
    if not display_available():
        print("self-test: no display available", file=sys.stderr)
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="n8n-launcher-self-test-") as scratch:
            store = ConfigStore(Path(scratch) / "launcher.db")
            store.save(AppConfig("self-test@example.invalid", "SelfTest1", Path(scratch)))
            app = ensure_application()
            apply_theme(app)
            window = MainWindow(WorkspaceManager(store, DockerManager()))
            window.show()
            app.processEvents()
            window.close()
    except Exception as exc:
        # Deliberately broad: anything that goes wrong while building the window
        # (a missing plugin, an unloadable dylib, a theme typo) is the same
        # answer for a packager, so it becomes one line and one exit code
        # instead of a traceback nobody reads on a machine with no display.
        print(f"self-test failed: {exc}", file=sys.stderr)
        return 1
    return 0


class LauncherApp:
    """The launcher's window: a list of workspaces and the event loop around it."""

    def __init__(
        self,
        store: ConfigStore,
        manager: WorkspaceManager,
        monitor: EventStore | None = None,
    ) -> None:
        """Hold the collaborators and nothing else; nothing is drawn yet."""
        self.store = store
        self.manager = manager
        self.monitor = monitor

    def run(self) -> None:
        """Show the window and return when it closes.

        Raises:
            GuiUnavailable: When there is no display to open the window on.
        """
        if not display_available():
            raise GuiUnavailable(
                "Aucun affichage disponible : lancez le launcher dans une session graphique."
            )
        app = ensure_application()
        apply_theme(app)
        window = MainWindow(self.manager)
        window.show()
        # Gives the interpreter a periodic callback so a Python signal handler
        # runs while exec() is parked in C++, and lets a request posted before
        # exec() started still be honoured.
        timer = QTimer()
        timer.setInterval(SIGNAL_TICK_MS)

        def on_tick() -> None:
            # The callback does little; its existence is what lets a Python
            # signal handler run at all. It also covers a shutdown requested
            # while the loop was already winding down.
            if shutdown_requested():
                app.quit()

        timer.timeout.connect(on_tick)
        timer.start()
        try:
            if not shutdown_requested():
                app.exec()
        finally:
            timer.stop()
            window.close()


__all__ = [
    "SELF_TEST_FLAG",
    "SIGNAL_TICK_MS",
    "GuiUnavailable",
    "LauncherApp",
    "clear_shutdown",
    "display_available",
    "ensure_application",
    "request_shutdown",
    "run_gui",
    "self_test",
    "shutdown_requested",
]
