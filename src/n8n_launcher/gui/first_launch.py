"""The first-launch wizard: collect the owner identity and the work directory.

The *rules* live in :mod:`n8n_launcher.core.first_launch`; this module is the
form around them. On accept it calls :func:`~n8n_launcher.core.first_launch.run_first_launch`,
which checks Docker and writes the config, and it stays open with a message when
that raises — so the user learns Docker is not running before the window closes,
not after.

:func:`prompt_first_launch` is what ``__main__`` calls: it makes sure an
application exists, opens the wizard modal, and answers whether a config was
written.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ConfigStore
from ..core.first_launch import SetupWizardError, run_first_launch
from ..core.models import AppConfig
from ..docker.manager import DockerManager
from .app import GuiUnavailable, display_available, ensure_application


class FirstLaunchWizard(QDialog):
    """Collect the three first-launch values and persist them via the rules."""

    def __init__(
        self,
        store: ConfigStore,
        docker: DockerManager,
        parent: QWidget | None = None,
        *,
        default_dir: Path | None = None,
    ) -> None:
        """Build the form; *default_dir* seeds the work-directory field."""
        super().__init__(parent)
        self._store = store
        self._docker = docker
        self._config: AppConfig | None = None
        self.setWindowTitle("Premier lancement")

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Configurez le launcher : identité du propriétaire n8n et dossier de travail.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._email = QLineEdit(self)
        self._email.setObjectName("email")
        form.addRow("Adresse e-mail", self._email)

        self._password = QLineEdit(self)
        self._password.setObjectName("password")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Mot de passe", self._password)

        directory = Path(default_dir) if default_dir else Path.home() / "n8n-workspaces"
        self._work_dir = QLineEdit(str(directory), self)
        self._work_dir.setObjectName("work_dir")
        browse = QPushButton("Parcourir…", self)
        browse.clicked.connect(self._browse)
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self._work_dir)
        row_layout.addWidget(browse)
        form.addRow("Dossier de travail", row)
        layout.addLayout(form)

        hint = QLabel("8 caractères minimum, dont un chiffre et une majuscule.", self)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        """Let the user pick the work directory instead of typing it."""
        chosen = QFileDialog.getExistingDirectory(self, "Choisir un dossier", self._work_dir.text())
        if chosen:
            self._work_dir.setText(chosen)

    def work_dir(self) -> Path:
        """Return the chosen work directory as an expanded path."""
        return Path(self._work_dir.text()).expanduser()

    def config(self) -> AppConfig | None:
        """Return the config written on accept, or ``None`` if not accepted."""
        return self._config

    def accept(self) -> None:
        """Run the first-launch rules; warn and stay open when they refuse."""
        try:
            self._config = run_first_launch(
                self._store,
                self._docker,
                email=self._email.text(),
                password=self._password.text(),
                work_dir=self.work_dir(),
            )
        except SetupWizardError as exc:
            QMessageBox.warning(self, "Premier lancement", str(exc))
            return
        super().accept()


def prompt_first_launch(
    store: ConfigStore,
    docker: DockerManager,
    parent: QWidget | None = None,
) -> bool:
    """Open the wizard and return whether it wrote a config.

    Raises:
        GuiUnavailable: When there is no display to open the wizard on.
    """
    if not display_available():
        raise GuiUnavailable(
            "Aucun affichage disponible : lancez le launcher dans une session graphique."
        )
    ensure_application()
    wizard = FirstLaunchWizard(store, docker, parent)
    return wizard.exec() == QDialog.DialogCode.Accepted


__all__ = ["FirstLaunchWizard", "prompt_first_launch"]
