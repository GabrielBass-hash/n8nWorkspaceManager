"""Cross-platform browser app-mode launching."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from dataclasses import dataclass


@dataclass(frozen=True)
class Browser:
    """A discoverable browser with the flag that opens it in app mode."""

    name: str
    executable: str
    app_flag: str = "--app"


_BROWSER_FLAGS = {
    # Chromium-family supports true app mode (window without chrome UI).
    "google-chrome": "--app",
    "microsoft-edge": "--app",
    "brave-browser": "--app",
    "chromium": "--app",
    "chromium-browser": "--app",
    # Firefox has no --app; --new-window is the closest drop-in.
    "firefox": "--new-window",
    "x-www-browser": "--new-window",
}


def find_browser() -> Browser | None:
    """Return the first installed browser, preferring Chromium app-mode ones."""
    for name, flag in _BROWSER_FLAGS.items():
        executable = shutil.which(name)
        if executable:
            return Browser(name, executable, flag)
    return None


def open_app(url: str, browser: Browser | None = None) -> None:
    """Open ``url`` in a dedicated app window of the given (or a discovered) browser."""
    selected = browser or find_browser()
    if selected is None:
        open_url(url)
        return
    try:
        subprocess.Popen([selected.executable, selected.app_flag, url])
    except OSError:
        # Preferred binary disappeared between discovery and launch — fall back
        # to the system handler instead of failing silently.
        open_url(url)


def open_url(url: str) -> None:
    """Open ``url`` via the system handler, trying xdg-open on Linux as a fallback."""
    if webbrowser.open(url):
        return
    # No registered handler succeeded; try xdg-open directly on Linux/X11.
    xdg = shutil.which("xdg-open")
    if xdg is not None:
        subprocess.Popen([xdg, url])
        return
    raise RuntimeError(f"Could not open URL: {url}")
