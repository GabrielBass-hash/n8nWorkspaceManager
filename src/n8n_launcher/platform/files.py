"""Reveal an exported file in the OS file manager."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Linux has no single file-manager binary, but every freedesktop desktop
# provides the xdg-open entry point; macOS and Windows have one canonical
# command each, so they are invoked directly.
_MAC_OPENER = "open"
_WINDOWS_OPENER = "explorer"
_LINUX_OPENER = "xdg-open"


def _opener() -> str | None:
    """Return this platform's file-manager command, or ``None`` when missing."""
    if sys.platform == "darwin":
        return _MAC_OPENER
    if os.name == "nt":
        return _WINDOWS_OPENER
    # Resolved through PATH: a minimal environment (a GUI app started from a
    # desktop file, a frozen build) does not always carry /usr/bin.
    return shutil.which(_LINUX_OPENER)


def open_folder(target: Path) -> None:
    """Open the folder holding *target* in the file manager.

    A directory argument is opened as-is, anything else reveals its parent —
    the caller usually has just written a file and wants to see where it
    landed. This is a cosmetic follow-up to a completed action, so it never
    raises: a missing or broken file manager is logged and swallowed rather
    than turned into an error dialog on top of a successful export.
    """
    folder = target if target.is_dir() else target.parent
    opener = _opener()
    if opener is None:
        logger.debug("No file manager available to reveal %s", folder)
        return
    try:
        # Detached with no stdio: the file manager outlives the launcher, and
        # the result is never inspected — on Windows `explorer` reports a
        # non-zero status even when it opened the folder successfully.
        subprocess.Popen(
            [opener, str(folder)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.debug("Could not open %s in the file manager: %s", folder, exc)


__all__ = ["open_folder"]
