"""First-launch configuration: the wizard's rules, without its dialogs.

The launcher is useless before it knows the owner e-mail, the owner password and
where workspaces live, so a first launch has to collect those three. This module
holds that as plain functions over plain values — it prompts for nothing and
draws nothing — because the prompt is a decision belonging to whatever front end
is in use, while the *rules* (n8n's own password policy, the work directory) are
the same whoever asks.

:func:`run_first_launch` is the whole non-interactive flow: check Docker, build
and save the config. The PySide6 wizard in :mod:`n8n_launcher.gui.first_launch`
collects the three values and calls it; a CLI or a test would do the same.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..docker.manager import DockerManager
from .config import ConfigStore
from .models import AppConfig


class SetupWizardError(RuntimeError):
    """Raised when first-launch setup cannot complete."""


def validate_password(password: str) -> None:
    """Enforce n8n's own password policy (8-64 chars, one number, one uppercase).

    Mirrors the policy n8n enforces on the owner account; a password this
    accepts is one n8n will accept when the owner is created.
    """
    if not password or not 8 <= len(password) <= 64:
        raise SetupWizardError("An owner password of 8 to 64 characters is required")
    if not re.search(r"\d", password):
        raise SetupWizardError("An owner password must contain at least one number")
    if not re.search(r"[A-Z]", password):
        raise SetupWizardError("An owner password must contain at least one uppercase letter")


def build_initial_config(email: str, password: str, work_dir: Path) -> AppConfig:
    """Validate the first-launch inputs and return a new :class:`AppConfig`.

    The work directory is created so the very next call that writes a workspace
    into it does not have to. ``work_dir`` must be truthy: an empty path would
    silently mean the current directory.

    Raises:
        SetupWizardError: When the email, the password or the directory is bad.
    """
    if not email.strip() or "@" not in email:
        raise SetupWizardError("A valid owner email is required")
    validate_password(password)
    if not work_dir:
        raise SetupWizardError("A work directory is required")
    work_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(email.strip(), password, work_dir)


def run_first_launch(
    store: ConfigStore,
    docker: DockerManager,
    *,
    email: str,
    password: str,
    work_dir: Path,
) -> AppConfig:
    """Check Docker, build and save the initial config, and return it.

    Docker is checked first: a launcher without a Docker daemon cannot run a
    workspace, so refusing before anything is written keeps the machine on a
    true first launch instead of half-configured.

    Raises:
        SetupWizardError: When Docker is not ready or the inputs are invalid.
    """
    status = docker.check_available()
    if not status.available:
        raise SetupWizardError(f"Docker is not ready: {status.message}")
    config = build_initial_config(email, password, work_dir)
    store.save(config)
    return config


__all__ = [
    "SetupWizardError",
    "build_initial_config",
    "run_first_launch",
    "validate_password",
]
