from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from n8n_launcher.platform.browser import (
    Browser,
    _linux_candidate,
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


def test_find_browser_prefers_first_found_chromium() -> None:
    def which(name: str) -> str:
        if name == "google-chrome-stable":
            return "/usr/bin/unexpected"
        return "/usr/bin/edge" if name == "microsoft-edge" else None

    with _path_only_discovery(), patch(
        "n8n_launcher.platform.browser.shutil.which", side_effect=which
    ):
        assert find_browser() == Browser("google-chrome-stable", "/usr/bin/unexpected")


def test_find_browser_falls_back_to_firefox() -> None:
    def which(name: str) -> str:
        return "/usr/bin/firefox" if name == "firefox" else None

    with _path_only_discovery(), patch(
        "n8n_launcher.platform.browser.shutil.which", side_effect=which
    ):
        browser = find_browser()

    assert browser == Browser("firefox", "/usr/bin/firefox", app_mode=False)


def test_find_browser_returns_none_when_no_supported_browser() -> None:
    with _path_only_discovery(), patch(
        "n8n_launcher.platform.browser.shutil.which", return_value=None
    ):
        assert find_browser() is None


def test_find_browser_prefers_chromium_over_firefox() -> None:
    def which(name: str) -> str:
        if name == "chromium-browser":
            return "/usr/bin/chromium-browser"
        if name == "firefox":
            return "/usr/bin/firefox"
        return None

    with _path_only_discovery(), patch(
        "n8n_launcher.platform.browser.shutil.which", side_effect=which
    ):
        browser = find_browser()

    assert browser == Browser("chromium-browser", "/usr/bin/chromium-browser")


def test_open_app_uses_isolated_profile_and_app_flag() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")
    profile = Path("/tmp/n8n-profile")

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser, profile_dir=profile)

    popen.assert_called_once_with([
        "/usr/bin/google-chrome",
        f"--user-data-dir={profile}",
        "--app=http://127.0.0.1:5678",
        "--no-first-run",
    ])


def test_open_app_omits_profile_when_none_given() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser)

    popen.assert_called_once_with([
        "/usr/bin/google-chrome",
        "--app=http://127.0.0.1:5678",
        "--no-first-run",
    ])


def test_open_app_uses_new_window_flag_for_firefox() -> None:
    browser = Browser("firefox", "/usr/bin/firefox", app_mode=False)

    with patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser, profile_dir=Path("/tmp/p"))

    popen.assert_called_once_with([
        "/usr/bin/firefox",
        "--new-window",
        "http://127.0.0.1:5678",
    ])


def test_open_app_falls_back_to_webbrowser_when_none_found() -> None:
    with patch("n8n_launcher.platform.browser.find_browser", return_value=None), patch(
        "n8n_launcher.platform.browser.webbrowser.open", return_value=True
    ) as webbrowser_open:
        open_app("http://127.0.0.1:5678")

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_app_falls_back_when_popen_fails() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch(
        "n8n_launcher.platform.browser.subprocess.Popen", side_effect=OSError("no binary")
    ), patch(
        "n8n_launcher.platform.browser.webbrowser.open", return_value=True
    ) as webbrowser_open:
        open_app("http://127.0.0.1:5678", browser)

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_url_falls_back_to_xdg_open() -> None:
    with patch("n8n_launcher.platform.browser.webbrowser.open", return_value=False), patch(
        "n8n_launcher.platform.browser.shutil.which", return_value="/usr/bin/xdg-open"
    ), patch("n8n_launcher.platform.browser.subprocess.Popen") as popen:
        open_url("http://127.0.0.1:5678")

    popen.assert_called_once_with(["/usr/bin/xdg-open", "http://127.0.0.1:5678"])


def test_open_url_raises_when_no_handler_available() -> None:
    with patch("n8n_launcher.platform.browser.webbrowser.open", return_value=False), patch(
        "n8n_launcher.platform.browser.shutil.which", return_value=None
    ):
        try:
            open_url("http://127.0.0.1:5678")
        except RuntimeError as exc:
            assert "Could not open URL" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")