from unittest.mock import patch

from n8n_launcher.browser import Browser, find_browser, open_app, open_url
from n8n_launcher.ports import suggest_port


def test_suggest_port_skips_reserved_and_busy_ports() -> None:
    with patch("n8n_launcher.ports.is_port_available", side_effect=[False, True]):
        assert suggest_port(5678, reserved={5679}, limit=3) == 5680


def test_find_browser_uses_supported_order() -> None:
    with (
        patch("n8n_launcher.browser.os.path.isfile", return_value=False),
        patch("n8n_launcher.browser.shutil.which", side_effect=[None, "/usr/bin/edge"]),
    ):
        assert find_browser() == Browser("microsoft-edge", "/usr/bin/edge")


def test_find_browser_falls_back_to_firefox() -> None:
    with (
        patch("n8n_launcher.browser.os.path.isfile", return_value=False),
        patch(
            "n8n_launcher.browser.shutil.which",
            side_effect=[None] * 5 + ["/usr/bin/firefox"],
        ),
    ):
        browser = find_browser()

    assert browser == Browser("firefox", "/usr/bin/firefox", "--new-window")


def test_find_browser_returns_none_when_no_supported_browser() -> None:
    with (
        patch("n8n_launcher.browser.os.path.isfile", return_value=False),
        patch("n8n_launcher.browser.shutil.which", return_value=None),
    ):
        assert find_browser() is None


def test_open_app_starts_browser_without_shell() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch("n8n_launcher.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser)

    popen.assert_called_once_with([
        "/usr/bin/google-chrome",
        "--app",
        "http://127.0.0.1:5678",
    ])


def test_open_app_falls_back_to_webbrowser_when_none_found() -> None:
    with patch("n8n_launcher.browser.find_browser", return_value=None), patch(
        "n8n_launcher.browser.webbrowser.open", return_value=True
    ) as webbrowser_open:
        open_app("http://127.0.0.1:5678")

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_app_falls_back_when_popen_fails() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch(
        "n8n_launcher.browser.subprocess.Popen", side_effect=OSError("no binary")
    ), patch("n8n_launcher.browser.webbrowser.open", return_value=True) as webbrowser_open:
        open_app("http://127.0.0.1:5678", browser)

    webbrowser_open.assert_called_once_with("http://127.0.0.1:5678")


def test_open_url_falls_back_to_xdg_open() -> None:
    with patch("n8n_launcher.browser.webbrowser.open", return_value=False), patch(
        "n8n_launcher.browser.shutil.which", return_value="/usr/bin/xdg-open"
    ), patch("n8n_launcher.browser.subprocess.Popen") as popen:
        open_url("http://127.0.0.1:5678")

    popen.assert_called_once_with(["/usr/bin/xdg-open", "http://127.0.0.1:5678"])


def test_open_url_raises_when_no_handler_available() -> None:
    with patch("n8n_launcher.browser.webbrowser.open", return_value=False), patch(
        "n8n_launcher.browser.shutil.which", return_value=None
    ):
        try:
            open_url("http://127.0.0.1:5678")
        except RuntimeError as exc:
            assert "Could not open URL" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
