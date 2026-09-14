from unittest.mock import patch

from n8n_launcher.browser import Browser, find_browser, open_app
from n8n_launcher.ports import suggest_port


def test_suggest_port_skips_reserved_and_busy_ports() -> None:
    with patch("n8n_launcher.ports.is_port_available", side_effect=[False, True]):
        assert suggest_port(5678, reserved={5679}, limit=3) == 5680


def test_find_browser_uses_supported_order() -> None:
    with patch("n8n_launcher.browser.shutil.which", side_effect=[None, "/usr/bin/edge"]):
        assert find_browser() == Browser("microsoft-edge", "/usr/bin/edge")


def test_open_app_starts_browser_without_shell() -> None:
    browser = Browser("chrome", "/usr/bin/google-chrome")

    with patch("n8n_launcher.browser.subprocess.Popen") as popen:
        open_app("http://127.0.0.1:5678", browser)

    popen.assert_called_once_with([
        "/usr/bin/google-chrome",
        "--app",
        "http://127.0.0.1:5678",
    ])
