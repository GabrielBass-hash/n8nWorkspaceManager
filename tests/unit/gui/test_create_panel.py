"""The creation form: the pure plan rule and the dialog that wraps it."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QMessageBox

from n8n_launcher.core.models import DbMode
from n8n_launcher.gui.create_panel import CreateWorkspaceDialog, plan_from_fields


def test_a_blank_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="requis"):
        plan_from_fields("   ", managed=False)


def test_a_dbless_plan_has_no_database() -> None:
    plan = plan_from_fields("  Mon ws  ", managed=False)
    assert plan.name == "Mon ws"
    assert plan.db.mode is DbMode.NONE


def test_a_managed_plan_carries_a_fresh_password() -> None:
    first = plan_from_fields("ws", managed=True)
    second = plan_from_fields("ws", managed=True)
    assert first.db.mode is DbMode.MANAGED
    assert first.db.password
    assert first.db.password != second.db.password


def test_the_dialog_reports_its_folder_and_plan(qt_app, tmp_path: Path) -> None:
    dialog = CreateWorkspaceDialog(default_dir=tmp_path)
    assert dialog.workflow_dir() == tmp_path

    name = dialog.findChild(QLineEdit, "name")
    assert name is not None
    name.setText("Mon ws")
    managed = dialog.findChild(QCheckBox, "managed")
    assert managed is not None
    managed.setChecked(True)

    plan = dialog.plan()
    assert plan.name == "Mon ws"
    assert plan.db.mode is DbMode.MANAGED


def test_accepting_a_nameless_dialog_keeps_it_open(qt_app, monkeypatch) -> None:
    dialog = CreateWorkspaceDialog()
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )

    dialog.accept()

    # Rejected is the untouched result; the form never accepted, so it stays open.
    assert dialog.result() == QDialog.DialogCode.Rejected
