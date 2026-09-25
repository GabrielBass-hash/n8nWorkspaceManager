from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from n8n_launcher.platform import browser as browser_module
from n8n_launcher.platform.browser import (
    Browser,
    _candidate,
    _is_app_mode,
    _linux_candidate,
    _mac_candidate,
    _windows_candidate,
    find_browser,
    open_app,
    open_url,
)


@contextmanager
def _path_only_discovery():
    """Force PATH-only candidate discovery regardless of the runner platform.

    On macOS/Windows ``_candidate`` looks up real installed app bundles, so
    these tests would probe the CI machine's actual Chrome instead of the
    mocked ``shutil.which``. Routing through ``_linux_candidate`` makes the
    outcome deterministic everywhere.
    """
    with patch(
        "n8n_launcher.platform.browser._candidate",
        side_effect=_linux_candidate,
    ):
        yield


@pytest.mark.parametrize(
    "name",
    [
        "google-chrome",
        "google-chrome-stable",
        "microsoft-edge",
        "microsoft-edge-stable",
        "brave-browser",
        "brave",
        "chromium",
        "chromium-browser",
    ],
)
def test_is_app_mode_true_for_chromium_names(name: str) -> None:
    assert _is_app_mode(name) is True


@pytest.mark.parametrize("name", ["firefox", "x-www-browser", "unknown"])
def test_is_app_mode_false_for_non_chromium_names(name: str) -> None:
    assert _is_app_mode(name) is False


def test_browser_dataclass_app_mode_defaults_and_override() -> None:
    assert Browser("chromium", "/bin/chromium").app_mode is True
    assert Browser("firefox", "/bin/firefox", app_mode=False).app_mode is False


@pytest.mark.parametrize(
    ("name", "executable", "app_mode"),
    [
        (
            "google-chrome",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            True,
        ),
        (
            "microsoft-edge",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            True,
        ),
        (
            "brave-browser",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            True,
        ),
        (
            "chromium",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            True,
        ),
        (
            "firefox",
            "/Applications/Firefox.app/Contents/MacOS/firefox",
            False,
        ),
    ],
)
def test_mac_candidate_returns_browser_for_existing_bundle(
    name: str, executable: str, app_mode: bool
) -> None:
    with patch.object(Path, "exists", return_value=True):
        # `Path` renders absolute POSIX-style bundles with native separators
        # (Windows would rewrite them with `\`), so build the expectation the
        # same way the implementation does.
        expected = Browser(name, str(Path(executable)), app_mode)
        assert _mac_candidate(name) == expected


def test_mac_candidate_returns_none_for_missing_bundle() -> None:
    with patch.object(Path, "exists", return_value=False):
        assert _mac_candidate("google-chrome") is None


def test_mac_candidate_returns_none_for_unknown_name() -> None:
    with patch.object(Path, "exists", return_value=True):
        assert _mac_candidate("unknown") is None


def test_windows_candidate_finds_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    expected = tmp_path / "local" / "Google" / "Chrome" / "Application" / "chrome.exe"

    with patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == expected):
        browser = _windows_candidate("google-chrome")

    assert browser == Browser("google-chrome", str(expected))


def test_windows_candidate_falls_back_to_programfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "program"))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    expected = tmp_path / "program" / "Microsoft" / "Edge" / "Application" / "msedge.exe"

    with patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == expected):
        browser = _windows_candidate("microsoft-edge")

    assert browser == Browser("microsoft-edge", str(expected))


def test_windows_candidate_supports_programfiles_x86(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "x86"))
    expected = tmp_path / "x86" / "Mozilla Firefox" / "firefox.exe"

    with patch.object(Path, "exists", autospec=True, side_effect=lambda path: path == expected):
        browser = _windows_candidate("firefox")

    assert browser == Browser("firefox", str(expected), app_mode=False)


def test_windows_candidate_returns_none_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "program"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "x86"))

    with patch.object(Path, "exists", return_value=False):
        assert _windows_candidate("chromium") is None


def test_windows_candidate_returns_none_for_unknown_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)

    assert _windows_candidate("unknown") is None


@pytest.mark.parametrize(
    ("platform", "os_name", "target"),
    [
        ("darwin", "posix", "_mac_candidate"),
        ("win32", "nt", "_windows_candidate"),
        ("linux", "posix", "_linux_candidate"),
    ],
)
def test_candidate_dispatches_by_platform(platform: str, os_name: str, target: str) -> None:
    browser = Browser("selected", "/bin/browser")
    with ExitStack() as stack:
        stack.enter_context(patch.object(browser_module, "sys", SimpleNamespace(platform=platform)))
        stack.enter_context(patch.object(browser_module, "os", SimpleNamespace(name=os_name)))
        candidates = {
            name: stack.enter_context(patch.object(browser_module, name))
            for name in ("_mac_candidate", "_windows_candidate", "_linux_candidate")
        }
        candidates[target].return_value = browser

        assert _candidate("selected") == browser

    for name, candidate_mock in candidates.items():
        if name == target:
            candidate_mock.assert_called_once_with("selected")
        else:
            candidate_mock.assert_not_called()


def test_find_browser_prefers_first_found_chromium() -> None:
    def which(name: str) -> str:
        if name == "google-chrome-stable":
            return "/usr/bin/unexpected"
        return "/usr/bin/edge" if name == "microsoft-edge" else None

    with (
        _path_only_discovery(),
        patch("n8n_launcher.platform.browser.shutil.which", side_effect=which),
    ):
        assert find_browser() == Browser("google-chrome-stable", "/usr/bin/unexpected")


def test_find_browser_falls_back_to_firefox() -> None:
    def which(name: str) -> str:
        return "/usr/bin/firefox" if name == "firefox" else None

    with (
        _path_only_discovery(),
        patch("n8n_launcher.platform.browser.shutil.which", side_effect=which),
    ):
        browser = find_browser()

    assert browser == Browser("firefox", "/usr/bin/firefox", app_mode=False)


def test_find_browser_returns_none_when_no_supported_browser() -> None:
    with (
        _path_only_discovery(),
        patch("n8n_launcher.platform.browser.shutil.which", return_value=None),
    ):
        assert find_browser() is None


def test_find_browser_prefers_chromium_over_firefox() -> None:
    def which(name: str) -> str:
        if name == "chromium-browser":
            return "/usr/bin/chromium-browser"
        if name == "firefox":
            return "/usr/bin/firefox"
        return None

    with (
        _path_only_discovery(),
        patch("n8n_launcher.platform.browser.shutil.which", side_effect=which),
    ):
        browser = find_browser()

    assert browser == Browser("chromium-browser", "/usr/bin/chromium-browser")


def test_open_app_uses_isolated_profile_and_app_flag() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")
    profile = Path("/tmp/n8n-profile")

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser, profile_dir=profile)

    popen.assert_called_once_with(
        [
            "/usr/bin/google-chrome",
            f"--user-data-dir={profile}",
            "--app=http://127.0.0.1:5678",
            "--no-first-run",
        ]
    )


def test_open_app_omits_profile_when_none_given() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser)

    popen.assert_called_once_with(
        [
            "/usr/bin/google-chrome",
            "--app=http://127.0.0.1:5678",
            "--no-first-run",
        ]
    )


def test_open_app_uses_new_window_flag_for_firefox() -> None:
    browser = Browser("firefox", "/usr/bin/firefox", app_mode=False)

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser, profile_dir=Path("/tmp/p"))

    popen.assert_called_once_with(
        [
            "/usr/bin/firefox",
            "--new-window",
            "http://127.0.0.1:5678",
        ]
    )


def test_open_app_falls_back_to_webbrowser_when_none_found() -> None:
    with (
        patch("n8n_launcher.platform.browser.find_browser", return_value=None),
        patch(
            "n8n_launcher.platform.browser.webbrowser.open", return_value=True
        ) as webbrowser_open,
    ):
        open_app("http://127.0.0.1:5678")

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_app_falls_back_when_popen_fails() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with (
        patch("n8n_launcher.platform.browser.subprocess.Popen", side_effect=OSError("no binary")),
        patch(
            "n8n_launcher.platform.browser.webbrowser.open", return_value=True
        ) as webbrowser_open,
    ):
        open_app("http://127.0.0.1:5678", browser)

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_url_falls_back_to_xdg_open() -> None:
    with (
        patch("n8n_launcher.platform.browser.webbrowser.open", return_value=False),
        patch("n8n_launcher.platform.browser.shutil.which", return_value="/usr/bin/xdg-open"),
        patch("n8n_launcher.platform.browser.subprocess.Popen") as popen,
    ):
        open_url("http://127.0.0.1:5678")

    popen.assert_called_once_with(["/usr/bin/xdg-open", "http://127.0.0.1:5678"])


def test_open_url_raises_when_no_handler_available() -> None:
    with (
        patch("n8n_launcher.platform.browser.webbrowser.open", return_value=False),
        patch("n8n_launcher.platform.browser.shutil.which", return_value=None),
    ):
        try:
            open_url("http://127.0.0.1:5678")
        except RuntimeError as exc:
            assert "Could not open URL" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
