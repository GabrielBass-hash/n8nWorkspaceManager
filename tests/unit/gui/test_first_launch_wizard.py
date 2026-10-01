"""The first-launch wizard: it writes the config and refuses to close on errors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.gui import first_launch as fl
from n8n_launcher.gui.app import GuiUnavailable
from n8n_launcher.gui.first_launch import FirstLaunchWizard, prompt_first_launch

VALID = "Secret123"


def _docker(available: bool = True) -> MagicMock:
    docker = MagicMock()
    docker.check_available.return_value.available = available
    docker.check_available.return_value.message = "daemon down"
    return docker


def _fill(wizard: FirstLaunchWizard, *, password: str = VALID) -> None:
    email = wizard.findChild(QLineEdit, "email")
    secret = wizard.findChild(QLineEdit, "password")
    assert email is not None and secret is not None
    email.setText("owner@example.test")
    secret.setText(password)


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """Never let a warning box block an offscreen test."""
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )


def test_accept_writes_the_config(qt_app, tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    wizard = FirstLaunchWizard(store, _docker(), default_dir=tmp_path / "work")
    _fill(wizard)

    wizard.accept()

    assert wizard.result() == QDialog.DialogCode.Accepted
    assert wizard.config() is not None
    assert store.load().owner_email == "owner@example.test"
    assert (tmp_path / "work").is_dir()


def test_accept_blocks_on_a_bad_password(qt_app, tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    wizard = FirstLaunchWizard(store, _docker(), default_dir=tmp_path / "work")
    _fill(wizard, password="short")

    wizard.accept()

    assert wizard.result() == QDialog.DialogCode.Rejected
    assert not store.path.exists()


def test_accept_blocks_when_docker_is_unavailable(qt_app, tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    wizard = FirstLaunchWizard(store, _docker(False), default_dir=tmp_path / "work")
    _fill(wizard)

    wizard.accept()

    assert wizard.result() == QDialog.DialogCode.Rejected
    assert not store.path.exists()


def test_prompt_refuses_without_a_display(qt_app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fl, "display_available", lambda: False)
    with pytest.raises(GuiUnavailable):
        prompt_first_launch(ConfigStore(tmp_path / "launcher.db"), _docker())


def test_prompt_reports_a_cancelled_wizard(qt_app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fl.FirstLaunchWizard, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert prompt_first_launch(ConfigStore(tmp_path / "launcher.db"), _docker()) is False


def test_prompt_reports_an_accepted_wizard(qt_app, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fl.FirstLaunchWizard, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert prompt_first_launch(ConfigStore(tmp_path / "launcher.db"), _docker()) is True
