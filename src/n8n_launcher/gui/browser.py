"""Open local n8n instances as standalone browser applications.

Two gestures have to be told apart, and only the browser can tell them apart:

* the instance is **not** on screen — launch a window for it;
* it *is* on screen — raise the window it already has, whatever page of the
  n8n SPA that window happens to be showing.

Reloading the URL cannot decide this. Chromium matches a command-line URL
against a window's *current* URL, and n8n routes client-side (a fresh window on
``/`` ends on ``/signin?redirect=%252F`` without any HTTP redirect), so measured
on Chromium 154 the same ``--app=/`` launched twice leaves two windows. The
launcher therefore owns the profile it opens windows with
(:func:`~n8n_launcher.core.paths.browser_profile_dir`) and asks the running
browser over its DevTools endpoint — ``--remote-debugging-port=0`` publishes the
chosen port in ``DevToolsActivePort``, ``/json/activate/<id>`` raises a window —
matching the **origin** of the instance, which the SPA cannot change.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import requests

from ..core.paths import browser_profile_dir

logger = logging.getLogger(__name__)

_LINUX_BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
)
_MACOS_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_WINDOWS_BROWSERS = ("chrome.exe", "msedge.exe", "chromium.exe")

#: DevTools calls are loopback and must never hold up an open.
_DEVTOOLS_TIMEOUT = 3.0


class WebAppLaunchError(RuntimeError):
    """Raised when no supported standalone browser can be launched."""


class WebAppOutcome(StrEnum):
    """What an open actually did, so the journal can say which it was."""

    RAISED = "raised"
    OPENED = "opened"


def workspace_url(port: int) -> str:
    """Return the local URL of the n8n instance listening on *port*."""
    return f"http://127.0.0.1:{port}"


def _browser_candidates() -> tuple[str, ...]:
    """Return Chromium-family candidates for the current operating system."""
    if os.name == "nt":
        return _WINDOWS_BROWSERS
    if os.uname().sysname == "Darwin":
        return _MACOS_BROWSERS
    return _LINUX_BROWSERS


def _origin(url: str) -> str:
    """Return the ``scheme://host:port`` an n8n window can be recognized by."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _devtools_port(profile: Path) -> int | None:
    """Return the DevTools port a Chromium running on *profile* published.

    Chromium writes ``DevToolsActivePort`` next to the profile once it chose a
    port, and removes it on exit. ``None`` therefore covers both "no browser
    yet" and "the browser is gone", which is the same answer for us: launch.
    """
    try:
        published = profile.joinpath("DevToolsActivePort").read_text(encoding="utf-8")
        port = int(published.splitlines()[0])
    except (OSError, IndexError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _targets(
    session: requests.Session | None,
    port: int,
) -> list[dict[str, str]]:
    """Return the browser's open targets, or ``[]`` when it cannot be asked.

    A refused connection is normal, not exceptional: the port file appears
    slightly before the endpoint accepts (measured), and a crashed browser
    leaves the file behind. Neither may stop an instance from opening.
    """
    try:
        response = (session or requests).get(
            f"http://127.0.0.1:{port}/json/list", timeout=_DEVTOOLS_TIMEOUT
        )
        if not response.ok:
            return []
        targets = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("DevTools on port %d is unreachable: %s", port, exc)
        return []
    return [target for target in targets if isinstance(target, dict)]


def _target_ids(session: requests.Session | None, port: int, origin: str) -> list[str]:
    """Return the ids of the page targets serving *origin*."""
    return [
        str(target["id"])
        for target in _targets(session, port)
        if target.get("type") == "page"
        and str(target.get("url", "")).startswith(origin)
        and target.get("id")
    ]


def _activate(session: requests.Session | None, port: int, target_id: str) -> bool:
    """Ask the browser to bring *target_id* to the front; report success."""
    try:
        response = (session or requests).get(
            f"http://127.0.0.1:{port}/json/activate/{target_id}", timeout=_DEVTOOLS_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.debug("Could not activate target %s: %s", target_id, exc)
        return False
    return response.ok


def _close(session: requests.Session | None, port: int, target_id: str) -> None:
    """Close a window left over from a previous life of the instance."""
    try:
        (session or requests).get(
            f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=_DEVTOOLS_TIMEOUT
        )
    except requests.RequestException as exc:
        logger.debug("Could not close stale target %s: %s", target_id, exc)


def open_web_app(
    url: str,
    *,
    reuse: bool = True,
    which: Callable[[str], str | None] = shutil.which,
    popen: Callable[..., object] = subprocess.Popen,
    profile: Callable[[], Path] = browser_profile_dir,
    session: requests.Session | None = None,
) -> WebAppOutcome:
    """Show *url* in a standalone Chromium-family application window.

    A normal browser fallback is deliberately not used: tabs and the address
    bar violate the launcher workflow. The caller receives a clear error when
    no supported browser is installed.

    Args:
        url: The instance's local URL.
        reuse: Whether a window already showing that instance may be raised
            instead. False when the instance has just been (re)started, because
            the window left over from its previous life can only show the dead
            connection it died on.

    Raises:
        ValueError: If *url* is not an HTTP(S) URL.
        WebAppLaunchError: If no Chromium-family executable can be found or
            the process cannot be started.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL web invalide: {url!r}")

    executable = next((candidate for candidate in _browser_candidates() if which(candidate)), None)
    if executable is None:
        raise WebAppLaunchError(
            "Aucun navigateur Chromium compatible n'est installé pour ouvrir n8n en mode app."
        )

    directory = profile()
    port = _devtools_port(directory)
    if port is not None:
        windows_on_origin = _target_ids(session, port, _origin(url))
        if reuse and windows_on_origin and _activate(session, port, windows_on_origin[0]):
            logger.info("Raised the existing n8n web app at %s", url)
            return WebAppOutcome.RAISED
        for target_id in windows_on_origin:
            _close(session, port, target_id)

    try:
        popen(
            [
                executable,
                f"--app={url}",
                f"--user-data-dir={directory}",
                # 0 asks Chromium to pick a free port and publish it in the
                # profile, so no fixed port is ever exposed or fought over.
                "--remote-debugging-port=0",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            start_new_session=os.name != "nt",
            # Chromium narrates its own start-up (GTK theme warnings, GCM
            # registration failures) on stderr; inheriting ours would dump all
            # of it into the launcher's journal, which records launcher events.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise WebAppLaunchError(f"Impossible d'ouvrir n8n en mode app: {exc}") from exc
    logger.info("Opened a new n8n web app at %s", url)
    return WebAppOutcome.OPENED


__all__ = ["WebAppLaunchError", "WebAppOutcome", "open_web_app", "workspace_url"]
