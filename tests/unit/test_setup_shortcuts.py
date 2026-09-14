import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from n8n_launcher.config import ConfigStore
from n8n_launcher.models import AppConfig
from n8n_launcher.setup_wizard import SetupWizardError, run_first_launch
from n8n_launcher.shortcuts import install_desktop_shortcut


def test_first_launch_checks_docker_saves_config_and_installs_shortcut(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    docker = MagicMock()
    docker.check_available.return_value.available = True
    installer = MagicMock(return_value=tmp_path / "shortcut")

    config = run_first_launch(
        store,
        docker,
        email="owner@example.test",
        password="secret123",
        work_dir=tmp_path / "work",
        executable="/tmp/n8n-launcher",
        shortcut_installer=installer,
    )

    assert config == AppConfig("owner@example.test", "secret123", tmp_path / "work")
    assert store.load() == config
    installer.assert_called_once_with("/tmp/n8n-launcher")


def test_first_launch_rejects_unavailable_docker(tmp_path: Path) -> None:
    docker = MagicMock()
    docker.check_available.return_value.available = False

    with pytest.raises(SetupWizardError, match="Docker is not ready"):
        run_first_launch(
            ConfigStore(tmp_path / "config.json"),
            docker,
            email="owner@example.test",
            password="secret",
            work_dir=tmp_path / "work",
            executable="launcher",
        )


def test_first_launch_rejects_short_password(tmp_path: Path) -> None:
    docker = MagicMock()
    docker.check_available.return_value.available = True

    with pytest.raises(SetupWizardError, match="8 to 64"):
        run_first_launch(
            ConfigStore(tmp_path / "config.json"),
            docker,
            email="owner@example.test",
            password="short",
            work_dir=tmp_path / "work",
            executable="launcher",
        )


def test_linux_shortcut_is_executable_desktop_entry(tmp_path: Path) -> None:
    with patch("n8n_launcher.shortcuts.sys.platform", "linux"):
        shortcut = install_desktop_shortcut("/opt/n8n-launcher", target_dir=tmp_path)

    assert shortcut.suffix == ".desktop"
    assert "Exec=\"/opt/n8n-launcher\"" in shortcut.read_text(encoding="utf-8")
    if os.name != "nt":
        assert shortcut.stat().st_mode & 0o111
