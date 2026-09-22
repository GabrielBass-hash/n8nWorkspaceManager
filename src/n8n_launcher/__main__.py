"""Application entry point."""

import atexit
import contextlib
import os
import signal
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from .core.config import ConfigError, ConfigStore
from .docker.manager import DockerManager, resolve_docker_command
from .gui.app import LauncherApp, window_size
from .gui.first_launch import run_interactive_first_launch
from .workspaces.manager import WorkspaceManager


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


def main() -> None:
    """Application entry point: config, first-launch wizard, Tkinter GUI."""
    store = ConfigStore()
    docker = DockerManager(command=resolve_docker_command())
    atexit.register(stop_all, store, docker)
    try:
        signal.signal(signal.SIGTERM, lambda *_args: _signal_shutdown(store, docker, *_args))
        signal.signal(signal.SIGINT, lambda *_args: _signal_shutdown(store, docker, *_args))
    except ValueError:
        pass
    root: tk.Tk | None = None
    try:
        store.load()
    except ConfigError:
        # The wizard only represents a true first launch when no config file
        # exists at all; an existing-but-unreadable file must be backed up
        # instead of silently replaced with an empty workspace list.
        root = tk.Tk()
        _center(root)
        if store.path.exists():
            _backup_unreadable_config(store)
        if run_interactive_first_launch(store, docker, root=root) is None:
            root.destroy()
            return
    manager = WorkspaceManager(store, docker)
    LauncherApp(store, manager, manager.docker, root=root).run()


if __name__ == "__main__":
    main()
