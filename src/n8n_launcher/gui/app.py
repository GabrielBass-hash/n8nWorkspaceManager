"""The application shell — not written yet.

This is the seam the entry point calls into. It exists so the rest of the
launcher can be built, tested and released while there is no interface: the
business logic, the journal and the ordered shutdown all run headless, and the
moment a shell is needed this is the one place that has to say so.

Both entry points refuse instead of raising something vaguer. ``run_gui`` is
what :func:`n8n_launcher.__main__.main` calls, and a launcher with no interface
is a launcher that must not pretend to have one — it logs and exits rather than
opening an empty window.
"""

from __future__ import annotations

from ..core.config import ConfigStore
from ..monitoring.store import EventStore
from ..workspaces.manager import WorkspaceManager

#: The one message both entry points refuse with, so a log line and a traceback
#: say the same thing.
NOT_IMPLEMENTED = "new GUI not yet implemented (phase 2)"


def run_gui(
    store: ConfigStore,
    manager: WorkspaceManager,
    monitor: EventStore | None = None,
) -> None:
    """Open the launcher shell over *manager*, with *store* and *monitor** wired.

    Args:
        store: The persistent configuration every view reads and writes through.
        manager: The workspace controller; a view never touches Docker itself.
        monitor: The event journal, or ``None`` when it could not be opened.

    Raises:
        NotImplementedError: Always, until a shell is implemented.
    """
    raise NotImplementedError(NOT_IMPLEMENTED)


class LauncherApp:
    """The launcher's window: a list of workspaces, its pages and its journal.

    Constructed with the same collaborators :func:`run_gui` takes, so wiring a
    shell is a matter of filling this in — the arguments below are already the
    ones every view needs.
    """

    def __init__(
        self,
        store: ConfigStore,
        manager: WorkspaceManager,
        monitor: EventStore | None = None,
    ) -> None:
        """Hold the collaborators a shell would drive; draw nothing yet."""
        self.store = store
        self.manager = manager
        self.monitor = monitor

    def run(self) -> None:
        """Show the window and return when it closes.

        Raises:
            NotImplementedError: Always, until a shell is implemented.
        """
        raise NotImplementedError(NOT_IMPLEMENTED)


__all__ = ["NOT_IMPLEMENTED", "LauncherApp", "run_gui"]
