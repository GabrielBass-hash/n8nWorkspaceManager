"""Shared fixtures for the widget tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def qt_app():
    """Return the process's ``QApplication``, creating it if needed.

    A ``QWidget`` cannot be built without one. ``ensure_application`` is the
    same singleton the shell uses, so tests and production share it.
    """
    from n8n_launcher.gui.app import ensure_application

    return ensure_application()
