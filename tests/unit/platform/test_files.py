"""Tests for revealing an exported file in the OS file manager."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from n8n_launcher.platform.files import _opener, open_folder


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``subprocess.Popen`` and return the mock recording the calls."""
    launcher = MagicMock()
    monkeypatch.setattr("n8n_launcher.platform.files.subprocess.Popen", launcher)
    return launcher


def _patch_platform(monkeypatch: pytest.MonkeyPatch, *, platform: str, name: str) -> None:
    """Force the platform branch of ``_opener`` regardless of the runner OS."""
    monkeypatch.setattr("n8n_launcher.platform.files.sys.platform", platform)
    monkeypatch.setattr("n8n_launcher.platform.files.os.name", name)
    monkeypatch.setattr(
        "n8n_launcher.platform.files.shutil.which", lambda _name: "/usr/bin/xdg-open"
    )


def test_opener_is_platform_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_platform(monkeypatch, platform="darwin", name="posix")
    assert _opener() == "open"
    _patch_platform(monkeypatch, platform="win32", name="nt")
    assert _opener() == "explorer"
    _patch_platform(monkeypatch, platform="linux", name="posix")
    assert _opener() == "/usr/bin/xdg-open"


def test_opener_is_none_without_a_file_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_platform(monkeypatch, platform="linux", name="posix")
    monkeypatch.setattr("n8n_launcher.platform.files.shutil.which", lambda _name: None)

    assert _opener() is None


def test_open_folder_reveals_the_parent_of_a_file(tmp_path: Path, popen: MagicMock) -> None:
    exported = tmp_path / "events-export.json"
    exported.write_text("[]", encoding="utf-8")

    open_folder(exported)

    assert popen.call_args.args[0][1] == str(tmp_path)


def test_open_folder_uses_the_directory_itself(tmp_path: Path, popen: MagicMock) -> None:
    open_folder(tmp_path)

    assert popen.call_args.args[0][1] == str(tmp_path)


def test_open_folder_detaches_the_file_manager(tmp_path: Path, popen: MagicMock) -> None:
    """The file manager outlives the launcher, so it must not share its stdio."""
    open_folder(tmp_path / "events-export.json")

    kwargs = popen.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_open_folder_is_a_no_op_without_a_file_manager(
    tmp_path: Path, popen: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_platform(monkeypatch, platform="linux", name="posix")
    monkeypatch.setattr("n8n_launcher.platform.files.shutil.which", lambda _name: None)

    open_folder(tmp_path / "events-export.json")

    popen.assert_not_called()


def test_open_folder_never_raises_when_the_launch_fails(tmp_path: Path, popen: MagicMock) -> None:
    popen.side_effect = OSError("no file manager here")

    open_folder(tmp_path / "events-export.json")


def test_opener_does_not_probe_the_filesystem_on_darwin_or_windows(
    tmp_path: Path, popen: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen macOS/Windows build has no ``which`` lookup to perform."""
    which = MagicMock()
    monkeypatch.setattr("n8n_launcher.platform.files.shutil.which", which)
    _patch_platform(monkeypatch, platform="darwin", name="posix")
    open_folder(tmp_path)
    _patch_platform(monkeypatch, platform="win32", name="nt")
    open_folder(tmp_path)

    which.assert_not_called()
    assert [call.args[0][0] for call in popen.call_args_list] == ["open", "explorer"]


def test_open_folder_uses_the_resolved_linux_binary(
    tmp_path: Path, popen: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH resolution matters: a GUI launch may not carry /usr/bin."""
    monkeypatch.setattr("n8n_launcher.platform.files.sys.platform", sys.platform)
    monkeypatch.setattr("n8n_launcher.platform.files.os.name", "posix")
    monkeypatch.setattr(
        "n8n_launcher.platform.files.shutil.which", lambda _name: "/snap/bin/xdg-open"
    )

    open_folder(tmp_path)

    assert popen.call_args.args[0][0] == "/snap/bin/xdg-open"
