"""Application entry point."""

import atexit
import contextlib
import logging
import os
import signal
import sys
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox

from .core.config import ConfigError, ConfigStore
from .core.filelock import LockError, acquire_single_instance_lock
from .core.models import WorkspaceState
from .core.paths import logs_dir
from .docker.manager import DockerManager, resolve_docker_command
from .gui.app import LauncherApp, window_size
from .gui.first_launch import run_interactive_first_launch
from .monitoring.bootstrap import bootstrap_logging
from .monitoring.store import EventStore
from .workspaces.manager import WorkspaceManager

logger = logging.getLogger(__name__)


def _center(root: tk.Tk) -> None:
    """Centre the root window on screen with a reasonable vertical offset."""
    width, height = window_size(root.winfo_screenwidth(), root.winfo_screenheight())
    root.geometry(f"{width}x{height}")
    root.update_idletasks()
    x = (root.winfo_screenwidth() - width) // 2
    y = max((root.winfo_screenheight() - height) // 3, 0)
    root.geometry(f"+{x}+{y}")


def _backup_unreadable_config(store: ConfigStore) -> None:
    """Move an unreadable config aside before the wizard rewrites the live file.

    ``main`` only routes an unreadable config to the first-launch wizard when
    the file exists (true first launch is when the file is absent). The wizard
    then calls ``store.save()``, which would silently overwrite the user's
    workspace list — so the offending file is preserved under
    ``config.json.corrupt-<timestamp>`` and the user is told, never wiped
    without trace.
    """
    backup = store.path.with_name(f"{store.path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        os.replace(store.path, backup)
    except OSError:
        return
    messagebox.showwarning(
        "Configuration illisible",
        "Le fichier de configuration n'a pas pu être lu.\n"
        "Une copie a été sauvegardée sous :\n"
        f"{backup}\n\n"
        "⚠ Ce fichier contient vos identifiants (mot de passe du compte owner, "
        "token GitHub) — ne le partagez pas.\n\n"
        "L'assistant de premier lancement va vous permettre de repartir.\n"
        "Ré-importez vos workspaces après coup si besoin.",
    )


def stop_all(store: ConfigStore, docker: DockerManager) -> None:
    """Best-effort shutdown of every launched workspace (n8n + local DBs)."""
    manager = WorkspaceManager(store, docker)
    try:
        config = store.load()
    except Exception:
        return
    for workspace in config.workspaces:
        with contextlib.suppress(Exception):
            manager.stop(workspace.id)


def _signal_shutdown(store: ConfigStore, docker: DockerManager, *_args: object) -> None:
    """Stop every workspace on SIGTERM / SIGINT, then exit."""
    stop_all(store, docker)
    sys.exit(0)


def _start_monitoring() -> EventStore | None:
    """Install the persistent event store, degrading to stderr-only on failure.

    Monitoring must never keep the launcher from starting: an unwritable log
    directory (read-only home, missing ``platformdirs`` path) falls back to
    the default logging configuration instead of raising.
    """
    try:
        store = bootstrap_logging(logs_dir())
    except Exception as exc:
        logging.basicConfig(level=logging.INFO)
        logging.getLogger(__name__).warning("Journalisation persistante indisponible : %s", exc)
        return None
    logger.info(
        "Surveillance active",
        extra={"event_store": str(store.path), "retention_days": store.retention_days},
    )
    return store


def _session_length(started: float) -> str:
    """Return the elapsed session time as ``h:mm:ss`` for the journal."""
    return str(timedelta(seconds=round(time.monotonic() - started)))


def _close_session(store: ConfigStore, docker: DockerManager, started: float) -> None:
    """Stop every workspace, then record the journal's end-of-session event.

    The shutdown runs here rather than only from the ``atexit`` hook because
    ``atexit`` handlers fire *after* ``main``'s ``finally`` — that is, once the
    event store is already closed. A record emitted at that point is dropped by
    the monitoring handler (it only shows up as a ``--- Logging error ---`` on
    stderr), so without this pass the journal would never record the teardown
    and the ``atexit``-stopped workspaces would leave no trace at all. Running it
    here puts the ``Stopped <name>`` events and this closing event in the
    journal, in causal order.

    The still-running count is best effort: a config that cannot be read is
    already reported by the unreadable-config warning, so it counts as zero.
    """
    running = 0
    with contextlib.suppress(Exception):
        running = sum(
            1
            for workspace in store.load().workspaces
            if workspace.state is not WorkspaceState.STOPPED
        )
    with contextlib.suppress(Exception):
        stop_all(store, docker)
    logger.info(
        "Surveillance terminée — session de %s, %d workspace(s) en cours à la fermeture",
        _session_length(started),
        running,
    )


def main() -> None:
    """Application entry point: config, first-launch wizard, Tkinter GUI."""
    started = time.monotonic()
    store = ConfigStore()
    # Exactly one launcher process may hold the config + compose files at a
    # time, so refuse to start a second instance instead of racing it.
    try:
        instance_lock = acquire_single_instance_lock(store.path.parent)
    except LockError:
        with contextlib.suppress(Exception):
            messagebox.showwarning(
                "n8n Launcher",
                "Une autre instance de l'application est déjà en cours d'exécution.",
            )
        return
    monitor = _start_monitoring()
    docker = DockerManager(command=resolve_docker_command())
    # Fallback only: `_close_session` already ran the shutdown on every path
    # that unwinds `main`, and a record emitted from here would no longer reach
    # the journal (the store is closed by then). It still covers the exits that
    # never run `main`'s `finally`.
    atexit.register(stop_all, store, docker)
    # LIFO: released after stop_all has finished its best-effort shutdown.
    atexit.register(instance_lock.release)
    try:
        signal.signal(signal.SIGTERM, lambda *_args: _signal_shutdown(store, docker, *_args))
        signal.signal(signal.SIGINT, lambda *_args: _signal_shutdown(store, docker, *_args))
    except ValueError:
        pass
    root: tk.Tk | None = None
    try:
        try:
            store.load()
        except ConfigError as exc:
            # The wizard only represents a true first launch when no config file
            # exists at all; an existing-but-unreadable file must be backed up
            # instead of silently replaced with an empty workspace list.
            root = tk.Tk()
            _center(root)
            if store.path.exists():
                _backup_unreadable_config(store)
                # The reason is the whole point of the journal here: without it
                # the warning is unattributable, and the traceback (chained to
                # the underlying sqlite/OSError) tells a corrupt database from a
                # permission or locking problem.
                logger.warning(
                    "Configuration illisible (%s) : copie de sauvegarde créée",
                    exc,
                    exc_info=True,
                )
            logger.info("Assistant de premier lancement affiché")
            if run_interactive_first_launch(store, docker, root=root) is None:
                root.destroy()
                return
        manager = WorkspaceManager(store, docker)
        logger.info(
            "Interface ouverte",
            extra={"workspaces": len(manager.list())},
        )
        LauncherApp(store, manager, manager.docker, root=root, monitor=monitor).run()
    finally:
        # The journal session is closed *before* the store: this stops the
        # workspaces (logging the teardown) and writes the closing event, both
        # of which need a writable store.
        with contextlib.suppress(Exception):
            _close_session(store, docker, started)
        with contextlib.suppress(Exception):
            instance_lock.release()
        if monitor is not None:
            with contextlib.suppress(Exception):
                monitor.close()


if __name__ == "__main__":
    main()
