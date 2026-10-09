"""The launcher's PySide6 user interface.

Everything under this package is the only place a GUI toolkit is imported; the
managers (:mod:`n8n_launcher.workspaces`, ``docker``, ``git``, ``remote``) stay
importable on a headless machine. :func:`run_gui` is the entry point
``n8n_launcher.__main__`` calls; :class:`LauncherApp` is the shell behind it.

The visual decisions that used to live in Tkinter widgets are functions without
widgets — the sizing rules in :mod:`n8n_launcher.gui_utils.text`, the status
rules in :mod:`n8n_launcher.workspaces.status` — and are tested by plain
assertion. See ``docs/history/MIGRATION.md`` for the contract.

Entry: :func:`run_gui` (``app.py``), :class:`~n8n_launcher.gui.window.MainWindow`
(``window.py``), ``open_web_app`` (``browser.py``).
Gotcha: reopening an instance raises the window it already has rather than
starting a second one — see ``browser.open_web_app``.
Map: ``docs/architecture.md``.
"""

from .app import LauncherApp, run_gui
from .first_launch import prompt_first_launch

__all__ = ["LauncherApp", "prompt_first_launch", "run_gui"]
