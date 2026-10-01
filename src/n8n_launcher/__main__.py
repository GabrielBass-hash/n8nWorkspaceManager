"""Application entry point."""

import atexit
import contextlib
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

from .core.config import ConfigError, ConfigStore
from .core.filelock import LockError, acquire_single_instance_lock
from .core.models import WorkspaceState
from .core.paths import logs_dir
from .docker.manager import DockerManager, resolve_docker_command
from .gui import prompt_first_launch, run_gui
from .gui.app import SELF_TEST_FLAG, GuiUnavailable, request_shutdown, self_test
from .monitoring.bootstrap import bootstrap_logging
from .monitoring.store import EventStore
from .workspaces.manager import WorkspaceManager

logger = logging.getLogger(__name__)


def _backup_unreadable_config(store: ConfigStore) -> None:
    """Move an unreadable config aside so a later write cannot destroy it.

    A config that exists but cannot be read is the user's workspace list, their
    owner password and their GitHub token. Whatever happens next must never
    overwrite it silently, so the file is preserved under
    ``launcher.db.corrupt-<timestamp>`` and the copy is named in the journal.
    """
    backup = store.path.with_name(f"{store.path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        os.replace(store.path, backup)
    except OSError as exc:
        # Nothing moved, so the original is still where it was: say that instead
        # of pointing the user at a copy that does not exist.
        logger.warning("Configuration illisible : aucune sauvegarde créée (%s)", exc)
        return
    logger.warning(
        "Configuration illisible : copie de sauvegarde créée sous %s "
        "(elle contient vos identifiants — ne la partagez pas)",
        backup,
    )


def _prompt_first_launch(store: ConfigStore, docker: DockerManager) -> bool:
    """Run the first-launch wizard; never let a missing display crash the run.

    Returns True once the wizard wrote a config, False when it was cancelled or
    when there is no display to show it on. The latter is not a failure to
    report to a window that does not exist: it is logged, and the caller's
    ``finally`` still brackets the session.
    """
    try:
        return prompt_first_launch(store, docker)
    except GuiUnavailable as exc:
        logger.error("Interface indisponible : %s", exc)
        return False


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
    """Stop every workspace on SIGTERM / SIGINT, then exit — or ask Qt to.

    Under the GUI the event loop is in C++, where raising ``SystemExit`` from a
    Qt slot is swallowed by the exception hook installed for monitoring. When a
    :class:`QApplication` exists, ``request_shutdown()`` asks it to quit instead
    and this returns; the loop unwinds and ``main``'s ``finally`` tears down.
    """
    stop_all(store, docker)
    if not request_shutdown():
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


def main(argv: list[str] | None = None) -> None:
    """Application entry point: open the journal, hand off to the interface, close cleanly.

    ``--self-test`` short-circuits everything (no config, no lock, no Docker) and
    exits with the shell's own smoke-test code, so a packager can prove a
    finished artifact opens a window.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if SELF_TEST_FLAG in arguments:
        raise SystemExit(self_test())

    started = time.monotonic()
    store = ConfigStore()
    # Exactly one launcher process may hold the config + compose files at a
    # time, so refuse to start a second instance instead of racing it.
    try:
        instance_lock = acquire_single_instance_lock(store.path.parent)
    except LockError:
        # No window to warn in; the journal is not open yet either, and opening
        # it here would take the very lock this instance does not hold.
        logger.warning("Une autre instance de l'application est déjà en cours d'exécution.")
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
    try:
        try:
            store.load()
        except ConfigError as exc:
            # A config that exists but cannot be read is the user's workspace
            # list and credentials: preserve it and stop. One that is absent is
            # a true first launch, and the wizard collects the owner identity and
            # the work directory, checks Docker and writes the config before the
            # manager below reads it.
            if store.path.exists():
                logger.warning("Configuration illisible, arrêt du lanceur (%s)", exc, exc_info=True)
                _backup_unreadable_config(store)
                return
            logger.info("Aucune configuration : premier lancement")
            if not _prompt_first_launch(store, docker):
                return
        manager = WorkspaceManager(store, docker)
        logger.info(
            "Ouverture de l'interface",
            extra={"workspaces": len(manager.list())},
        )
        try:
            run_gui(store, manager, monitor)
        except GuiUnavailable as exc:
            # No display (a service, an ssh command): say so and exit through
            # the normal path, so the session is still bracketed and the
            # workspaces are still stopped by `finally`.
            logger.error("Interface indisponible : %s", exc)
            return
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
        # Same reasoning for the config store: the ordered shutdown above is
        # the last reader, and an open SQLite handle keeps the database (and
        # its -wal / -shm sidecars) locked until the process exits.
        with contextlib.suppress(Exception):
            store.close()


if __name__ == "__main__":
    main()
