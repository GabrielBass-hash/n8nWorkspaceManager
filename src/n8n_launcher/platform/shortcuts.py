"""Desktop shortcut installation for the desktop distribution."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class ShortcutError(RuntimeError):
    """Raised when a desktop shortcut cannot be installed."""


def desktop_dir() -> Path:
    """Return the current user's Desktop directory."""
    return Path.home() / "Desktop"


def install_desktop_shortcut(
    executable: str | Path,
    *,
    name: str = "n8n Launcher",
    target_dir: Path | None = None,
) -> Path:
    """Create a platform-native desktop shortcut for the launcher executable."""
    directory = target_dir or desktop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = Path(executable)
    if sys.platform.startswith("linux"):
        shortcut = directory / "n8n-launcher.desktop"
        content = "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={name}",
                f'Exec="{target.as_posix()}"',
                "Terminal=false",
                "Categories=Utility;",
                "",
            ]
        )
        shortcut.write_text(content, encoding="utf-8")
        shortcut.chmod(0o755)
        return shortcut
    if os.name == "nt":
        shortcut = directory / "n8n-launcher.url"
        shortcut.write_text(
            f"[InternetShortcut]\nURL=file:///{target.as_posix()}\n", encoding="utf-8"
        )
        return shortcut
    shortcut = directory / "n8n-launcher.command"
    shortcut.write_text(f'#!/bin/sh\nexec "{target.as_posix()}"\n', encoding="utf-8")
    shortcut.chmod(0o755)
    return shortcut
