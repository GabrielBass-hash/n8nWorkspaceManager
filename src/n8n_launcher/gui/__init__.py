"""Tkinter user interface.

The launcher shell lives in :mod:`n8n_launcher.gui.app`; creation dialogs,
the close sequence, the update flow and the workspace display helpers live
in dedicated sibling modules. The *decisions* those dialogs collect, and the
sizing rules the tables obey, are toolkit-free and live outside this package
(:mod:`n8n_launcher.workspaces.dialogs`, :mod:`n8n_launcher.gui_utils.text`).
"""

from ..workspaces.dialogs import CreatePlan
from ..workspaces.display import GitRowStatus
from .app import LauncherApp

__all__ = ["CreatePlan", "GitRowStatus", "LauncherApp"]
