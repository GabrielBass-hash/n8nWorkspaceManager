from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from helpers import FakeRoot

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig
from n8n_launcher.gui.first_launch import (
    SetupWizardError,
    run_first_launch,
    run_interactive_first_launch,
)


def test_interactive_first_launch_registers_fonts_on_root() -> None:
    # A caller-supplied root (the app's own) must still get the named UI fonts
    # registered on it; the wizard cancel path exits right after, so nothing
    # else can run on top of a root whose fonts are missing.
    store = ConfigStore(Path("/tmp/unused-config.json"))
    docker = MagicMock()
    root = FakeRoot()

    with (
        patch("n8n_launcher.gui.first_launch.configure_fonts") as fonts,
        patch("n8n_launcher.gui.first_launch.simpledialog.askstring", return_value=None),
    ):
        assert run_interactive_first_launch(store, docker, root=root) is None

    fonts.assert_called_once_with(root)


def test_first_launch_checks_docker_saves_config_and_installs_shortcut(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    docker = MagicMock()
    docker.check_available.return_value.available = True
    installer = MagicMock(return_value=tmp_path / "shortcut")

    config = run_first_launch(
        store,
        docker,
        email="owner@example.test",
        password="Secret123",
        work_dir=tmp_path / "work",
        executable="/tmp/n8n-launcher",
        shortcut_installer=installer,
    )

    assert config == AppConfig("owner@example.test", "Secret123", tmp_path / "work")
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


def test_first_launch_rejects_password_without_number(tmp_path: Path) -> None:
    docker = MagicMock()
    docker.check_available.return_value.available = True

    with pytest.raises(SetupWizardError, match="number"):
        run_first_launch(
            ConfigStore(tmp_path / "config.json"),
            docker,
            email="owner@example.test",
            password="UppercaseOnly",
            work_dir=tmp_path / "work",
            executable="launcher",
        )


def test_first_launch_rejects_password_without_uppercase(tmp_path: Path) -> None:
    docker = MagicMock()
    docker.check_available.return_value.available = True

    with pytest.raises(SetupWizardError, match="uppercase"):
        run_first_launch(
            ConfigStore(tmp_path / "config.json"),
            docker,
            email="owner@example.test",
            password="test1234",
            work_dir=tmp_path / "work",
            executable="launcher",
        )
