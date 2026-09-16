"""Cross-platform browser app-mode launching."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Browser:
    """A discoverable browser and whether it supports Chromium app mode."""

    name: str
    executable: str
    app_mode: bool = True


# Chromium-family browsers support true app mode: a kiosk-like window with no
# tabs and no URL bar — the "app like Discord" feel. Firefox and the generic
# x-www-browser wrapper cannot hide their chrome, so they fall back to a plain
# new window.
_CHROMIUM_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
    "brave",
    "chromium",
    "chromium-browser",
)
_FALLBACK_NAMES = ("firefox", "x-www-browser")

# macOS apps live in .app bundles, not on PATH; (bundle-name, binary-in-MacOS).
_MAC_BUNDLES = {
    "google-chrome": ("Google Chrome", "Google Chrome"),
    "microsoft-edge": ("Microsoft Edge", "Microsoft Edge"),
    "brave-browser": ("Brave Browser", "Brave Browser"),
    "chromium": ("Chromium", "Chromium"),
    "firefox": ("Firefox", "firefox"),
}

# Windows executables live under %LOCALAPPDATA% or %ProgramFiles%; each tuple is
# a relative path from any of those roots (same _CHROMIUM_NAMES ordering).
_WINDOWS_EXES = {
    "google-chrome": ("Google", "Chrome", "Application", "chrome.exe"),
    "microsoft-edge": ("Microsoft", "Edge", "Application", "msedge.exe"),
    "brave-browser": ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
    "chromium": ("Chromium", "Application", "chrome.exe"),
    "firefox": ("Mozilla Firefox", "firefox.exe"),
}


def _is_app_mode(name: str) -> bool:
    """Return True when the browser name supports Chromium's ``--app`` window."""
    return name in _CHROMIUM_NAMES


def _linux_candidate(name: str) -> Browser | None:
    executable = shutil.which(name)
    if executable is None:
        return None
    return Browser(name, executable, _is_app_mode(name))


def _mac_candidate(name: str) -> Browser | None:
    bundle, binary = _MAC_BUNDLES.get(name, (None, None))
    if bundle is None:
        return None
    path = Path("/Applications") / f"{bundle}.app" / "Contents" / "MacOS" / binary
    if not path.exists():
        return None
    return Browser(name, str(path), _is_app_mode(name))


def _windows_candidate(name: str) -> Browser | None:
    parts = _WINDOWS_EXES.get(name)
    if parts is None:
        return None
    roots = (
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
    )
    for root in roots:
        if not root:
            continue
        path = Path(root, *parts)
        if path.exists():
            return Browser(name, str(path), _is_app_mode(name))
    return None


def _candidate(name: str) -> Browser | None:
    """Return the browser discovered for ``name`` on the current platform."""
    if sys.platform == "darwin":
        return _mac_candidate(name)
    if os.name == "nt":
        return _windows_candidate(name)
    return _linux_candidate(name)


def find_browser() -> Browser | None:
    """Return the first installed browser, preferring Chromium app-mode ones."""
    for name in (*_CHROMIUM_NAMES, *_FALLBACK_NAMES):
        browser = _candidate(name)
        if browser is not None:
            return browser
    return None


def open_app(
    url: str,
    browser: Browser | None = None,
    *,
    profile_dir: Path | None = None,
) -> None:
    """Open ``url`` in a standalone app window of the given (or a discovered) browser.

    Chromium-family browsers get a dedicated ``--user-data-dir`` whenever
    ``profile_dir`` is provided, so the window always launches its own isolated
    instance — no address bar, no tabs, no matter whether a browser is already
    running. Firefox only supports a plain new window.
    """
    selected = browser or find_browser()
    if selected is None:
        open_url(url)
        return
    args = [selected.executable]
    if selected.app_mode:
        if profile_dir is not None:
            args.append(f"--user-data-dir={profile_dir}")
        args.append(f"--app={url}")
        # Keep the window purely functional: no first-run interstitial and
        # no default-browser nagging from the fresh isolated profile.
        args.append("--no-first-run")
    else:
        args.append("--new-window")
        args.append(url)
    try:
        subprocess.Popen(args)
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