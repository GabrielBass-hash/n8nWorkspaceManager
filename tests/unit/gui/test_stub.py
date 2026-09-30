"""The GUI seam: there is no interface yet, and both entry points say so.

These are the only tests under ``tests/unit/gui/``. They exist so that "Tkinter
is gone" is a checked fact rather than an absence: a shell that silently started
doing nothing, or a caller that stopped noticing the refusal, would show up
here.
"""

from __future__ import annotations

import re
import sys
from unittest.mock import MagicMock

import pytest

from n8n_launcher.gui import LauncherApp, run_gui
from n8n_launcher.gui.app import NOT_IMPLEMENTED


def test_the_package_exposes_exactly_the_entry_points() -> None:
    import n8n_launcher.gui as gui

    assert set(gui.__all__) == {"LauncherApp", "run_gui"}


def test_run_gui_refuses_rather_than_opening_an_empty_window() -> None:
    with pytest.raises(NotImplementedError, match="phase 2"):
        run_gui(MagicMock(), MagicMock(), MagicMock())


def test_run_gui_accepts_a_launch_without_a_journal() -> None:
    # Monitoring is best effort (an unwritable log directory must not stop the
    # launcher), so a missing journal is a valid call, not a different one.
    with pytest.raises(NotImplementedError):
        run_gui(MagicMock(), MagicMock())


def test_the_shell_refuses_to_run() -> None:
    app = LauncherApp(MagicMock(), MagicMock(), MagicMock())
    with pytest.raises(NotImplementedError, match=re.escape(NOT_IMPLEMENTED)):
        app.run()


def test_the_shell_holds_the_collaborators_it_was_given() -> None:
    store, manager, monitor = MagicMock(), MagicMock(), MagicMock()
    app = LauncherApp(store, manager, monitor)
    assert (app.store, app.manager, app.monitor) == (store, manager, monitor)


def test_importing_the_shell_pulls_in_no_toolkit() -> None:
    """A shipped module must not reach for a GUI toolkit on the way in.

    Checked on ``sys.modules`` rather than by grepping the sources: an import
    that only happens inside a function would pass a grep and still put a
    toolkit on a headless machine's import path.
    """
    before = {name for name in sys.modules if name.split(".")[0] in {"tkinter", "_tkinter"}}
    assert before == set(), "the toolkit is already loaded by an earlier test"

    import importlib

    for name in ("n8n_launcher.gui", "n8n_launcher.gui.app"):
        importlib.import_module(name)

    loaded = {name for name in sys.modules if name.split(".")[0] in {"tkinter", "_tkinter"}}
    assert loaded == set()
