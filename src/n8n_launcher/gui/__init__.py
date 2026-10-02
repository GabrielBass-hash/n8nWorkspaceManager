"""The launcher's PySide6 user interface.

Everything under this package is the only place a GUI toolkit is imported; the
managers (:mod:`n8n_launcher.workspaces`, ``docker``, ``git``, ``remote``) stay
importable on a headless machine. :func:`run_gui` is the entry point
``n8n_launcher.__main__`` calls; :class:`LauncherApp` is the shell behind it.

The visual decisions that used to live in Tkinter widgets are functions without
widgets — the sizing rules in :mod:`n8n_launcher.gui_utils.text`, the status
rules in :mod:`n8n_launcher.workspaces.status` — and are tested by plain
assertion. See ``MIGRATION.md`` for the contract.
"""

from .app import LauncherApp, run_gui
from .first_launch import prompt_first_launch

__all__ = ["LauncherApp", "prompt_first_launch", "run_gui"]
