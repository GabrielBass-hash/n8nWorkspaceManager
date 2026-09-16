"""Application entry point."""

import atexit
import signal
import sys
import tkinter as tk

from .core.config import ConfigError, ConfigStore
from .docker.manager import DockerManager, resolve_docker_command
from .gui.app import LauncherApp
from .gui.first_launch import run_interactive_first_launch
from .workspaces.manager import WorkspaceManager


def _center(root: tk.Tk, width: int = 820, height: int = 460) -> None:
    """Centre the root window on screen with a reasonable vertical offset."""
    root.geometry(f"{width}x{height}")
    root.update_idletasks()
    x = (root.winfo_screenwidth() - width) // 2
    y = max((root.winfo_screenheight() - height) // 3, 0)
    root.geometry(f"+{x}+{y}")


def stop_all(store: ConfigStore, docker: DockerManager) -> None:
    """Best-effort shutdown of every launched workspace (n8n + local DBs)."""
    manager = WorkspaceManager(store, docker)
    try:
        config = store.load()
    except Exception:
        return
    for workspace in config.workspaces:
        try:
            manager.stop(workspace.id)
        except Exception:
            pass


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
        root = tk.Tk()
        _center(root)
        if run_interactive_first_launch(store, docker, root=root) is None:
            root.destroy()
            return
    manager = WorkspaceManager(store, docker)
    LauncherApp(store, manager, manager.docker, root=root).run()


if __name__ == "__main__":
    main()
