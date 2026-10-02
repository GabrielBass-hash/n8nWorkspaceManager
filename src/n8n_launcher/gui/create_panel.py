"""The creation form: collect a name, a folder and a database choice.

The form is thin on purpose. What it collects is a
:class:`~n8n_launcher.workspaces.dialogs.CreatePlan` — a domain value — and the
rule that turns a name and a checkbox into that plan is a plain function
(:func:`plan_from_fields`) with a plain test. The dialog only draws it, validates
it on ``accept`` and hands it back.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from ..core.models import DbConfig, DbMode
from ..workspaces.dialogs import CreatePlan, fresh_managed_db_config


def plan_from_fields(name: str, managed: bool) -> CreatePlan:
    """Build the creation plan from the form's raw values.

    A managed database carries a freshly generated password, so the plan is
    ready to persist as soon as the dialog is accepted.

    Raises:
        ValueError: When the workspace name is empty.
    """
    if not name.strip():
        raise ValueError("Le nom du workspace est requis")
    db = fresh_managed_db_config() if managed else DbConfig(mode=DbMode.NONE)
    return CreatePlan(name=name.strip(), db=db)


class CreateWorkspaceDialog(QDialog):
    """Ask for what a new workspace needs: a name, a folder and a DB mode."""

    def __init__(self, parent: QWidget | None = None, *, default_dir: Path | None = None) -> None:
        """Build the form; *default_dir* seeds the folder field."""
        super().__init__(parent)
        self.setWindowTitle("Nouveau workspace")

        form = QFormLayout(self)
        self._name = QLineEdit(self)
        self._name.setObjectName("name")
        self._name.setPlaceholderText("Mon workspace")
        form.addRow("Nom", self._name)

        directory = Path(default_dir) if default_dir else Path.home() / "n8n-workspaces"
        self._dir = QLineEdit(str(directory), self)
        self._dir.setObjectName("directory")
        browse = QPushButton("Parcourir…", self)
        browse.clicked.connect(self._browse)
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self._dir)
        row_layout.addWidget(browse)
        form.addRow("Dossier", row)

        self._managed = QCheckBox("Base Postgres managée", self)
        self._managed.setObjectName("managed")
        form.addRow("", self._managed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        """Let the user pick the folder instead of typing it."""
        chosen = QFileDialog.getExistingDirectory(self, "Choisir un dossier", self._dir.text())
        if chosen:
            self._dir.setText(chosen)

    def workflow_dir(self) -> Path:
        """Return the chosen folder as an expanded path."""
        return Path(self._dir.text()).expanduser()

    def plan(self) -> CreatePlan:
        """Return the plan collected by the form.

        Raises:
            ValueError: When the name is empty.
        """
        return plan_from_fields(self._name.text(), self._managed.isChecked())

    def accept(self) -> None:
        """Accept only a valid form; warn and stay open otherwise."""
        try:
            self.plan()
        except ValueError as exc:
            QMessageBox.warning(self, "Création", str(exc))
            return
        super().accept()


__all__ = ["CreateWorkspaceDialog", "plan_from_fields"]
