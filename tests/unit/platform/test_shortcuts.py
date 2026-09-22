import os
from unittest.mock import patch

from n8n_launcher.platform.shortcuts import install_desktop_shortcut


def test_linux_shortcut_is_executable_desktop_entry(tmp_path) -> None:
    with patch("n8n_launcher.platform.shortcuts.sys.platform", "linux"):
        shortcut = install_desktop_shortcut("/opt/n8n-launcher", target_dir=tmp_path)

    assert shortcut.suffix == ".desktop"
    assert 'Exec="/opt/n8n-launcher"' in shortcut.read_text(encoding="utf-8")
    if os.name != "nt":
        assert shortcut.stat().st_mode & 0o111
