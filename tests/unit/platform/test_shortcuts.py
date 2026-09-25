import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from n8n_launcher.platform import shortcuts as shortcuts_module
from n8n_launcher.platform.shortcuts import desktop_dir, install_desktop_shortcut


def test_linux_shortcut_is_executable_desktop_entry(tmp_path) -> None:
    with patch.object(shortcuts_module, "sys", SimpleNamespace(platform="linux")):
        shortcut = install_desktop_shortcut("/opt/n8n-launcher", target_dir=tmp_path)

    assert shortcut.suffix == ".desktop"
    assert 'Exec="/opt/n8n-launcher"' in shortcut.read_text(encoding="utf-8")
    if os.name != "nt":
        assert shortcut.stat().st_mode & 0o111


def test_windows_shortcut_writes_url_file(tmp_path) -> None:
    target = Path("C:/Apps/n8n-launcher.exe")
    with (
        patch.object(shortcuts_module, "sys", SimpleNamespace(platform="win32")),
        patch.object(shortcuts_module, "os", SimpleNamespace(name="nt")),
    ):
        shortcut = install_desktop_shortcut(target, target_dir=tmp_path)

    assert shortcut == tmp_path / "n8n-launcher.url"
    assert shortcut.read_text(encoding="utf-8") == (
        f"[InternetShortcut]\nURL=file:///{target.as_posix()}\n"
    )


def test_macos_shortcut_writes_executable_command(tmp_path) -> None:
    target = Path("/Applications/n8n-launcher.app/Contents/MacOS/n8n-launcher")
    with (
        patch.object(shortcuts_module, "sys", SimpleNamespace(platform="darwin")),
        patch.object(shortcuts_module, "os", SimpleNamespace(name="posix")),
    ):
        shortcut = install_desktop_shortcut(target, target_dir=tmp_path)

    assert shortcut == tmp_path / "n8n-launcher.command"
    assert shortcut.read_text(encoding="utf-8") == (f'#!/bin/sh\nexec "{target.as_posix()}"\n')
    assert shortcut.stat().st_mode & 0o111


def test_desktop_dir_is_home_desktop() -> None:
    with patch.object(Path, "home", return_value=Path("/Users/test")):
        assert desktop_dir() == Path("/Users/test/Desktop")


def test_shortcut_creates_missing_target_dir(tmp_path) -> None:
    target_dir = tmp_path / "nested" / "desktop"
    with patch.object(shortcuts_module, "sys", SimpleNamespace(platform="linux")):
        shortcut = install_desktop_shortcut("/opt/n8n-launcher", target_dir=target_dir)

    assert target_dir.is_dir()
    assert shortcut.parent == target_dir
