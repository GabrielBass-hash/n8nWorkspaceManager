"""Self-update: fetch the newest GitHub release and swap the running app.

Once, at startup (never periodically), the launcher inspects
``https://api.github.com/repos/<owner>/<repo>/releases/latest`` on a
background thread. If that release is newer than the compiled ``__version__``
and ships an asset for the current platform, the GUI offers a download ->
restore-and-relaunch flow.

The actual swap never happens inside the running process: the updater writes
a tiny platform helper script (``sh`` on macOS/Linux, ``.cmd`` on Windows)
and launches it detached. The helper waits for the launcher to exit, replaces
the binary or ``.app`` bundle, and reopens the fresh version. This mirrors
what ``scripts/install_macos.sh`` already does by hand, and is the only way to
swap a running app on Windows (a running executable can be renamed, not
deleted).

If the app lives in a directory the current user cannot write to (e.g. macOS
``/Applications``), the GUI falls back to a clickable status-bar link to the
release page instead of attempting an install.
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from ..core.paths import updates_dir

#: GitHub owner/repo that publishes the launcher releases.
REPO = "GabrielBass-hash/n8nWorkspaceManager"

#: Per-platform release asset names, in preference order.
_ASSET_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "Darwin": ("n8n-launcher-macos.dmg",),
    "Windows": ("n8n-launcher-windows.exe",),
    "Linux": ("n8n-launcher-linux",),
}


class UpdateError(RuntimeError):
    """Raised when a release cannot be fetched, downloaded or installed."""


@dataclass(frozen=True, order=True)
class Version:
    """Comparable SemVer ``MAJOR.MINOR.PATCH`` triplet."""

    major: int
    minor: int
    patch: int


@dataclass(frozen=True)
class Asset:
    """A downloadable artifact attached to a GitHub release."""

    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Release:
    """The ``releases/latest`` payload, reduced to what the updater needs."""

    tag_name: str
    version: Version
    assets: dict[str, Asset]


def parse_version(text: str) -> Version:
    """Parse ``MAJOR[.MINOR[.PATCH]]``, tolerating a ``v`` prefix.

    GitHub tags historically used zero-padded builds (``v0.4.01``) while the
    current releases are plain SemVer (``v4.0.2``); both normalize to the same
    integer triplet.
    """
    raw = text.strip()
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    try:
        numbers = tuple(int(part) for part in raw.split("."))
    except ValueError as exc:
        raise ValueError(f"invalid version string: {text!r}") from exc
    if len(numbers) == 0 or len(numbers) > 3:
        raise ValueError(f"invalid version string: {text!r}")
    if len(numbers) == 1:
        numbers += (0, 0)
    elif len(numbers) == 2:
        numbers += (0,)
    return Version(numbers[0], numbers[1], numbers[2])


def current_version() -> str:
    """Return the compiled-in version of the running launcher."""
    from .. import __version__  # noqa: PLC0415 – avoid circular import

    return __version__


def release_is_newer(release: Release, current: str) -> bool:
    """True when the release is strictly newer than the running version."""
    return release.version > parse_version(current)


def release_page_url(owner_repo: str = REPO) -> str:
    """Public page where a human can read about and download the newest release."""
    return f"https://github.com/{owner_repo}/releases/latest"


def fetch_latest_release(
    owner_repo: str = REPO,
    *,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> Release:
    """Fetch the newest non-prerelease, non-draft release from the GitHub API.

    ``session`` is injectable for tests (same pattern as ``N8nApiClient``).
    Any network, HTTP or payload problem raises :class:`UpdateError`; callers
    treat that as "stay silent" — an update check must never crash the app.
    """
    url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    http = session or requests.Session()
    try:
        response = http.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "n8n-launcher",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise UpdateError(f"update check failed: {exc}") from exc
    if response.status_code != 200:
        raise UpdateError(f"update check returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpdateError("update check returned invalid JSON") from exc
    try:
        tag_name = payload["tag_name"]
        version = parse_version(tag_name)
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("update check returned an unreadable release") from exc
    assets: dict[str, Asset] = {}
    for item in payload.get("assets", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        try:
            size = int(item.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        assets[name] = Asset(
            name=name,
            url=str(item.get("browser_download_url", "")),
            size=size,
        )
    return Release(tag_name=tag_name, version=version, assets=assets)


def compatible_asset(release: Release, system: str | None = None) -> Asset | None:
    """Pick the release asset matching the current platform, if any.

    On Linux the release workflow attaches a one-file binary named
    ``n8n-launcher-linux``; an AppImage built out-of-band is accepted, too.
    """
    system = system or platform.system()
    for name in _ASSET_BY_PLATFORM.get(system, ()):
        asset = release.assets.get(name)
        if asset is not None:
            return asset
    if system not in ("Darwin", "Windows"):
        for name, asset in release.assets.items():
            if name.endswith(".AppImage"):
                return asset
    return None


def download_asset(
    url: str,
    dest: Path,
    *,
    session: requests.Session | None = None,
    expected_size: int | None = None,
    progress: Callable[[int], None] | None = None,
) -> None:
    """Stream a release asset into ``dest`` via a temp file + atomic rename.

    ``progress`` receives the number of bytes received so far. The download is
    discarded (and an :class:`UpdateError` raised) when its size does not match
    the size reported by the GitHub API.
    """
    http = session or requests.Session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".part")
    received = 0
    try:
        with http.get(url, stream=True, timeout=(10.0, 60.0)) as response, temporary.open(
            "wb"
        ) as handle:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                handle.write(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received)
    except requests.RequestException as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"update download failed: {exc}") from exc
    if expected_size is not None and received != expected_size:
        temporary.unlink(missing_ok=True)
        raise UpdateError(
            f"update download is incomplete: {received}/{expected_size} bytes"
        )
    os.replace(temporary, dest)


def install_target() -> Path | None:
    """Return the app binary or bundle the updater would replace.

    ``None`` when running from the source tree (no binary to swap). On macOS
    the target is the ``.app`` bundle found by walking up from ``sys.executable``;
    elsewhere it is the executable itself.
    """
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable)
    if platform.system() == "Darwin":
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
        return None
    return executable


def cleanup_stale() -> None:
    """Best-effort removal of leftovers from a previously failed Windows swap.

    A half-finished update can leave ``<exe>.old`` in place; deleting it at
    startup keeps the app directory clean. No-op elsewhere and always silent.
    """
    target = install_target()
    if target is None or platform.system() != "Windows":
        return
    try:
        Path(str(target) + ".old").unlink(missing_ok=True)
    except OSError:
        pass


def installer_script(
    asset_path: Path,
    target: Path,
    *,
    script_dir: Path | None = None,
) -> Path:
    """Write the detached update helper script and return its path.

    The script waits for the launcher process (the PID captured at call time)
    to exit, swaps the target for the downloaded asset, and relaunches the
    fresh version. ``script_dir`` is injectable for tests.
    """
    pid = os.getpid()
    directory = script_dir or updates_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        script = directory / "apply_update.cmd"
        script.write_text(_windows_source(asset_path, target, pid), encoding="utf-8")
        return script
    if platform.system() == "Darwin":
        body = _macos_source(asset_path, target, pid)
    else:
        body = _linux_source(asset_path, target, pid)
    script = directory / "apply_update.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o700)
    return script


def installer_command(script: Path) -> list[str]:
    """Build the shell command that runs the helper script detached."""
    if platform.system() == "Windows":
        return ["cmd", "/c", str(script)]
    return ["/bin/sh", str(script)]


def spawn_installer(script: Path) -> None:
    """Launch the helper fully detached so it outlives the launcher process."""
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(installer_command(script), **kwargs)


def _macos_source(asset_path: Path, target: Path, pid: int) -> str:
    """macOS helper: replace the ``.app`` bundle from a mounted DMG, then reopen."""
    app = shlex.quote(str(target))
    dmg = shlex.quote(str(asset_path))
    mount = shlex.quote(str(updates_dir() / "mount"))
    return "\n".join(
        [
            "#!/bin/sh",
            "# n8n Launcher auto-update helper (macOS).",
            f"APP={app}",
            f"DMG={dmg}",
            f"MNT={mount}",
            f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
            'mkdir -p "$MNT"',
            'hdiutil attach -nobrowse -noautoopen -mountpoint "$MNT" "$DMG" >/dev/null 2>&1',
            "SRC=$(find \"$MNT\" -maxdepth 1 -name '*.app' -print -quit)",
            'if [ -n "$SRC" ]; then',
            '  rm -rf "$APP"',
            '  ditto "$SRC" "$APP"',
            '  xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true',
            '  codesign --verify --deep --strict "$APP"',
            "fi",
            'hdiutil detach "$MNT" >/dev/null 2>&1 || true',
            'open "$APP"',
            "",
        ]
    )


def _linux_source(asset_path: Path, target: Path, pid: int) -> str:
    """Linux helper: atomically replace the executable, then relaunch it."""
    old = shlex.quote(str(target))
    new = shlex.quote(str(asset_path))
    return "\n".join(
        [
            "#!/bin/sh",
            "# n8n Launcher auto-update helper (Linux).",
            f"OLD={old}",
            f"NEW={new}",
            f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
            'TMP="$OLD.tmp"',
            'cp "$NEW" "$TMP"',
            'chmod +x "$TMP"',
            'mv -f "$TMP" "$OLD"',
            '"$OLD" &',
            "",
        ]
    )


def _windows_source(asset_path: Path, target: Path, pid: int) -> str:
    """Windows helper: rename the running exe, swap in the new one, relaunch.

    A running executable can be renamed (not deleted) on Windows, so the old
    binary moves aside, the new one takes its name and the stale backup is
    removed. If either step fails the helper restores the old binary and
    relaunches it, so a locked file never bricks the install.
    """
    old = str(target)
    new = str(asset_path)
    return "\r\n".join(
        [
            "@echo off",
            "rem n8n Launcher auto-update helper (Windows).",
            f"set \"PID={pid}\"",
            f"set \"OLD={old}\"",
            f"set \"NEW={new}\"",
            "set \"OLD_BAK=%OLD%.old\"",
            "",
            ":wait",
            'tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"%PID%" >nul',
            "if %errorlevel%==0 (",
            "  timeout /t 1 /nobreak >nul",
            "  goto :wait",
            ")",
            "",
            'move /Y "%OLD%" "%OLD_BAK%" >nul 2>nul',
            'if not exist "%OLD_BAK%" (',
            '  start "" "%OLD%"',
            "  exit /b 1",
            ")",
            'copy /Y "%NEW%" "%OLD%" >nul 2>nul',
            'if not exist "%OLD%" (',
            '  move /Y "%OLD_BAK%" "%OLD%" >nul 2>nul',
            '  start "" "%OLD%"',
            "  exit /b 1",
            ")",
            'del /Q "%OLD_BAK%" >nul 2>nul',
            'start "" "%OLD%"',
            "",
        ]
    )