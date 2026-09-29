"""Unit tests for first-launch setup (:mod:`n8n_launcher.core.first_launch`).

The wizard's rules, tested without a wizard: n8n's own password policy, the
work directory, and the order of "check Docker, save the config, install a
shortcut". What a front end does to collect the three inputs is its own concern
and is not exercised here.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.first_launch import (
    SetupWizardError,
    build_initial_config,
    run_first_launch,
    validate_password,
)
from n8n_launcher.core.models import AppConfig

VALID = "Secret123"


def docker_available(available: bool = True) -> MagicMock:
    """Return a DockerManager stub reporting *available*."""
    docker = MagicMock()
    docker.check_available.return_value.available = available
    return docker


def test_validate_password_accepts_n8n_compliant_passwords() -> None:
    for password in (VALID, "Abcdefg1", "a" * 8 + "1A", "Z9" + "z" * 62):
        validate_password(password)


def test_validate_password_enforces_n8ns_own_policy() -> None:
    # n8n 2.33.x: 8-64 chars, at least one digit, at least one uppercase.
    with pytest.raises(SetupWizardError, match="8 to 64"):
        validate_password("short")
    with pytest.raises(SetupWizardError, match="8 to 64"):
        validate_password("A1" + "z" * 63)
    with pytest.raises(SetupWizardError, match="8 to 64"):
        validate_password("")
    with pytest.raises(SetupWizardError, match="number"):
        validate_password("UppercaseOnly")
    with pytest.raises(SetupWizardError, match="uppercase"):
        validate_password("test1234")


def test_build_initial_config_trims_the_email_and_creates_the_work_dir(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "nested" / "work"
    config = build_initial_config("  owner@example.test  ", VALID, work_dir)
    assert config == AppConfig("owner@example.test", VALID, work_dir)
    assert work_dir.is_dir()


def test_build_initial_config_rejects_a_malformed_email(tmp_path: Path) -> None:
    for email in ("", "   ", "not-an-email"):
        with pytest.raises(SetupWizardError, match="email"):
            build_initial_config(email, VALID, tmp_path)


def test_run_first_launch_checks_docker_saves_config_and_installs_shortcut(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    installer = MagicMock(return_value=tmp_path / "shortcut")

    config = run_first_launch(
        store,
        docker_available(),
        email="owner@example.test",
        password=VALID,
        work_dir=tmp_path / "work",
        executable="/tmp/n8n-launcher",
        shortcut_installer=installer,
    )

    assert config == AppConfig("owner@example.test", VALID, tmp_path / "work")
    assert store.load() == config
    installer.assert_called_once_with("/tmp/n8n-launcher")


def test_run_first_launch_refuses_an_unavailable_docker(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    with pytest.raises(SetupWizardError, match="Docker is not ready"):
        run_first_launch(
            store,
            docker_available(False),
            email="owner@example.test",
            password=VALID,
            work_dir=tmp_path / "work",
            executable="launcher",
        )
    # Nothing was written: a launcher without Docker is not usable at all.
    assert not store.path.exists()


def test_run_first_launch_rejects_a_password_n8n_would_refuse(tmp_path: Path) -> None:
    for password, reason in (
        ("short", "8 to 64"),
        ("UppercaseOnly", "number"),
        ("test1234", "uppercase"),
    ):
        with pytest.raises(SetupWizardError, match=reason):
            run_first_launch(
                ConfigStore(tmp_path / "launcher.db"),
                docker_available(),
                email="owner@example.test",
                password=password,
                work_dir=tmp_path / "work",
                executable="launcher",
            )
