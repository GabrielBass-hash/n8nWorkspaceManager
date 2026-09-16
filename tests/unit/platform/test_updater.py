import os
import subprocess
from pathlib import Path

import pytest
import requests


from n8n_launcher.platform.updater import (
    REPO,
    Asset,
    Release,
    UpdateError,
    cleanup_stale,
    compatible_asset,
    current_version,
    download_asset,
    fetch_latest_release,
    install_target,
    installer_command,
    installer_script,
    parse_version,
    release_is_newer,
    release_page_url,
    spawn_installer,
)


def make_release(*, tag: str = "v0.4.01", assets: list[Asset] | None = None) -> Release:
    return Release(
        tag_name=tag,
        version=parse_version(tag),
        assets={asset.name: asset for asset in (assets or [])},
    )


def make_asset(name: str, *, url: str = "", size: int = 0) -> Asset:
    return Asset(name=name, url=url or f"https://example.test/{name}", size=size)


# --- version parsing -------------------------------------------------------


def test_parse_version_normalizes_prefix_and_padding() -> None:
    assert parse_version("0.3.0") == parse_version("v0.3.0")
    assert parse_version("0.4.01") == parse_version("v0.4.1")
    assert parse_version("v1.2.34") == parse_version("1.2.034")
    assert parse_version("5").major == 5
    assert parse_version("1.2.3").build == 3


def test_parse_version_rejects_garbage() -> None:
    for bad in ("", "v", "abc", "1.2.x", "1.2.3.4"):
        with pytest.raises(ValueError):
            parse_version(bad)


def test_release_is_newer_compares_full_triplets() -> None:
    assert release_is_newer(make_release(tag="v0.4.01"), "0.3.0") is True
    assert release_is_newer(make_release(tag="v1.0.01"), "0.4.01") is True
    assert release_is_newer(make_release(tag="v0.4.00"), "0.4.01") is False
    assert release_is_newer(make_release(tag="v0.3.0"), "0.3.0") is False


# --- release fetching -------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


PAYLOAD = {
    "tag_name": "v0.4.01",
    "assets": [
        {
            "name": "n8n-launcher-windows.exe",
            "browser_download_url": "https://example.test/n8n-launcher-windows.exe",
            "size": 12345,
        },
        {
            "name": "n8n-launcher-macos.dmg",
            "browser_download_url": "https://example.test/n8n-launcher-macos.dmg",
            "size": 54321,
        },
        {"junk": True},
    ],
}


class FakeSession:
    def __init__(self, response) -> None:
        self._response = response
        self.last_url: str | None = None

    def get(self, url, **_kwargs):
        self.last_url = url
        return self._response


def test_fetch_latest_release_parses_api_payload() -> None:
    session = FakeSession(FakeResponse(200, PAYLOAD))
    release = fetch_latest_release(session=session)

    assert release.tag_name == "v0.4.01"
    assert release.version == parse_version("0.4.01")
    assert session.last_url.endswith("/releases/latest")
    assert release.assets["n8n-launcher-macos.dmg"].size == 54321
    assert "junk" not in release.assets


def test_fetch_latest_release_raises_on_http_error() -> None:
    session = FakeSession(FakeResponse(404, {}))
    with pytest.raises(UpdateError):
        fetch_latest_release(session=session)


def test_fetch_latest_release_raises_on_network_error() -> None:
    class BrokenSession:
        def get(self, url, **_kwargs):
            raise requests.ConnectionError("offline")

    with pytest.raises(UpdateError) as excinfo:
        fetch_latest_release(session=BrokenSession())
    assert "offline" in str(excinfo.value)


def test_fetch_latest_release_raises_on_corrupt_payload() -> None:
    for payload in ("nope", [], {"assets": []}):
        session = FakeSession(FakeResponse(200, payload))
        with pytest.raises(UpdateError):
            fetch_latest_release(session=session)


# --- asset selection --------------------------------------------------------


def test_compatible_asset_selects_per_platform() -> None:
    release = make_release(
        assets=[
            make_asset("n8n-launcher-macos.dmg"),
            make_asset("n8n-launcher-windows.exe"),
            make_asset("n8n-launcher-linux"),
        ]
    )
    assert compatible_asset(release, system="Darwin").name == "n8n-launcher-macos.dmg"
    assert compatible_asset(release, system="Windows").name == "n8n-launcher-windows.exe"
    assert compatible_asset(release, system="Linux").name == "n8n-launcher-linux"


def test_compatible_asset_falls_back_to_appimage() -> None:
    release = make_release(assets=[make_asset("n8n-launcher-linux-x86_64.AppImage")])
    assert (
        compatible_asset(release, system="Linux").name
        == "n8n-launcher-linux-x86_64.AppImage"
    )


def test_compatible_asset_returns_none_when_missing() -> None:
    release = make_release(assets=[make_asset("other-file.txt")])
    assert compatible_asset(release, system="Windows") is None
    assert compatible_asset(release, system="Linux") is None


# --- download ---------------------------------------------------------------


class FakeStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int):
        for chunk in self._chunks:
            yield chunk


class StreamSession:
    def __init__(self, status: int = 200, chunks: list[bytes] | None = None) -> None:
        self._status = status
        self._chunks = chunks or [b"a", b"bc", b"def"]

    def get(self, url, **_kwargs):
        if self._status >= 400:
            return FakeStreamError(self._status)
        return FakeStream(self._chunks)


class FakeStreamError:
    def __init__(self, status: int) -> None:
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def raise_for_status(self) -> None:
        raise requests.HTTPError(f"HTTP {self._status}")


def test_download_asset_writes_and_verifies_size(tmp_path: Path) -> None:
    dest = tmp_path / "downloaded.bin"
    progress: list[int] = []

    download_asset(
        "https://example.test/n8n-launcher-linux",
        dest,
        session=StreamSession(chunks=[b"ab", b"cdef"]),
        expected_size=6,
        progress=progress.append,
    )

    assert dest.read_bytes() == b"abcdef"
    assert progress == [2, 6]
    assert not (tmp_path / "downloaded.bin.part").exists()


def test_download_asset_rejects_wrong_size(tmp_path: Path) -> None:
    dest = tmp_path / "downloaded.bin"
    with pytest.raises(UpdateError):
        download_asset(
            "https://example.test/n",
            dest,
            session=StreamSession(chunks=[b"abc"]),
            expected_size=99,
        )
    assert not dest.exists()
    assert not (tmp_path / "downloaded.bin.part").exists()


def test_download_asset_raises_on_http_error(tmp_path: Path) -> None:
    with pytest.raises(UpdateError):
        download_asset(
            "https://example.test/n",
            tmp_path / "x.bin",
            session=StreamSession(status=404),
        )


# --- install target ---------------------------------------------------------


def test_install_target_skips_when_not_frozen(monkeypatch) -> None:
    monkeypatch.delattr("sys.frozen", raising=False)
    assert install_target() is None


def test_install_target_macos_finds_app_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Darwin"
    )
    monkeypatch.setattr(
        "sys.executable",
        str(tmp_path / "n8n-launcher.app" / "Contents" / "MacOS" / "n8n-launcher"),
    )
    assert install_target() == tmp_path / "n8n-launcher.app"


def test_install_target_other_platforms_return_executable(monkeypatch) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Windows"
    )
    monkeypatch.setattr("sys.executable", "C:\\Program Files\\n8n-launcher.exe")
    assert install_target() == Path("C:\\Program Files\\n8n-launcher.exe")


def test_release_page_url_points_at_latest() -> None:
    assert release_page_url() == f"https://github.com/{REPO}/releases/latest"


# --- installer helpers -------------------------------------------------------


def test_installer_script_writes_executable_sh(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Linux"
    )
    target = tmp_path / "n8n-launcher"
    asset = tmp_path / "n8n-launcher.new"
    script = installer_script(asset, target, script_dir=tmp_path)

    assert script.name == "apply_update.sh"
    if os.name != "nt":
        assert script.stat().st_mode & 0o100
    text = script.read_text(encoding="utf-8")
    assert "kill -0" in text
    assert "mv -f" in text
    assert '"$OLD" &' in text


def test_installer_script_macos_replaces_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Darwin"
    )
    target = tmp_path / "n8n-launcher.app"
    asset = tmp_path / "n8n-launcher-macos.dmg"
    script = installer_script(asset, target, script_dir=tmp_path)

    text = script.read_text(encoding="utf-8")
    assert "hdiutil attach" in text
    assert "ditto" in text
    assert "xattr -dr com.apple.quarantine" in text
    assert "codesign --verify" in text
    assert "open \"$APP\"" in text


def test_installer_script_windows_waits_and_swaps(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Windows"
    )
    target = tmp_path / "n8n-launcher.exe"
    asset = tmp_path / "n8n-launcher.new.exe"
    script = installer_script(asset, target, script_dir=tmp_path)

    assert script.name == "apply_update.cmd"
    text = script.read_text(encoding="utf-8")
    assert "tasklist" in text
    assert "move /Y" in text
    assert "copy /Y" in text
    assert "start \"\" \"%OLD%\"" in text


def test_installer_command_uses_shell_per_platform(monkeypatch, tmp_path: Path) -> None:
    script = tmp_path / "apply_update.sh"
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Darwin"
    )
    assert installer_command(script) == ["/bin/sh", str(script)]
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Windows"
    )
    assert installer_command(script) == ["cmd", "/c", str(script)]


def test_spawn_installer_launches_detached(monkeypatch, tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Linux"
    )
    script = tmp_path / "apply_update.sh"
    mock_popen = MagicMock()
    monkeypatch.setattr("n8n_launcher.platform.updater.subprocess.Popen", mock_popen)

    spawn_installer(script)

    assert mock_popen.call_count == 1
    command, kwargs = mock_popen.call_args
    assert command == (["/bin/sh", str(script)],)
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_cleanup_stale_removes_windows_backup(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "n8n-launcher.exe"
    target.write_bytes(b"old")
    (tmp_path / "n8n-launcher.exe.old").write_bytes(b"backup")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "n8n_launcher.platform.updater.platform.system", lambda: "Windows"
    )
    monkeypatch.setattr("sys.executable", str(target))

    cleanup_stale()

    assert not (tmp_path / "n8n-launcher.exe.old").exists()
    assert target.exists()