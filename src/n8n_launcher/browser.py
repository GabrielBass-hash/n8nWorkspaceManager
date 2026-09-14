"""Cross-platform browser app-mode launching."""

from __future__ import annotations

import shutil
import subprocess
import webbrowser
from dataclasses import dataclass


@dataclass(frozen=True)
class Browser:
    name: str
    executable: str
    app_flag: str = "--app"


_BROWSER_NAMES = (
    "google-chrome",
    "microsoft-edge",
    "brave-browser",
    "chromium-browser",
    "chromium",
)


def find_browser() -> Browser | None:
    for name in _BROWSER_NAMES:
        executable = shutil.which(name)
        if executable:
            return Browser(name, executable)
    return None


def open_app(url: str, browser: Browser | None = None) -> None:
    selected = browser or find_browser()
    if selected is None:
        open_url(url)
        return
    subprocess.Popen([selected.executable, selected.app_flag, url])


def open_url(url: str) -> None:
    if not webbrowser.open(url):
        raise RuntimeError(f"Could not open URL: {url}")
