"""The launcher's user interface.

Tkinter is gone. Everything under the old ``gui/`` package has either been
extracted to the package that owns the subject — the sizing rules to
:mod:`n8n_launcher.gui_utils.text`, the creation/Git plans to
:mod:`n8n_launcher.workspaces.dialogs`, the update flow to
:mod:`n8n_launcher.platform.update_flow`, the close order to
:mod:`n8n_launcher.workspaces.close` — or deleted with the widgets it drew.

Nothing is wired yet: :func:`run_gui` is the entry point the application will
call once a real shell exists, and it refuses rather than pretending. See
``MIGRATION.md`` for what phase 2 has to rebuild.
"""

from .app import LauncherApp, run_gui

__all__ = ["LauncherApp", "run_gui"]
