"""First-launch configuration workflow."""

from __future__ import annotations

import contextlib
import re
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, simpledialog

from ..core.config import ConfigStore
from ..core.models import AppConfig
from ..docker.manager import DockerError, DockerManager
from ..platform.shortcuts import install_desktop_shortcut
from .theme import configure_fonts


class SetupWizardError(RuntimeError):
    """Raised when first-launch setup cannot complete."""


def validate_password(password: str) -> None:
    """Enforce n8n's own password policy (8-64 chars, one number, one uppercase)."""
    if not password or not 8 <= len(password) <= 64:
        raise SetupWizardError("An owner password of 8 to 64 characters is required")
    if not re.search(r"\d", password):
        raise SetupWizardError("An owner password must contain at least one number")
    if not re.search(r"[A-Z]", password):
        raise SetupWizardError("An owner password must contain at least one uppercase letter")


def build_initial_config(email: str, password: str, work_dir: Path) -> AppConfig:
    """Validate the first-launch inputs and return a new :class:`AppConfig`."""
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
    executable: str | Path,
    shortcut_installer: Callable[..., Path] = install_desktop_shortcut,
) -> AppConfig:
    """Check Docker, build and save the initial config, and install a shortcut."""
    status = docker.check_available()
    if not status.available:
        raise SetupWizardError(f"Docker is not ready: {status.message}")
    config = build_initial_config(email, password, work_dir)
    store.save(config)
    shortcut_installer(executable)
    return config


def run_interactive_first_launch(
    store: ConfigStore,
    docker: DockerManager,
    *,
    root: tk.Tk | None = None,
) -> AppConfig | None:
    """Prompt the user interactively for owner details; None when cancelled."""
    owns_root = root is None
    root = root or tk.Tk()
    # The wizard can own a fresh interpreter (tk.Tk() created just above), so
    # the named UI fonts must be registered on it for the prompts to be styled.
    with contextlib.suppress(Exception):
        configure_fonts(root)
    try:
        email = simpledialog.askstring("n8n Launcher", "Owner email:", parent=root)
        password = simpledialog.askstring("n8n Launcher", "Owner password:", show="*", parent=root)
        if email is None or password is None:
            return None
        try:
            return run_first_launch(
                store,
                docker,
                email=email,
                password=password,
                work_dir=Path.home(),
                executable=Path(sys.argv[0]).resolve(),
            )
        except (SetupWizardError, DockerError) as exc:
            # DockerError is a belt-and-braces guard: check_available never
            # raises, but a future docker call in this wizard must degrade to
            # a dialog too, never an uncaught startup traceback.
            messagebox.showerror("n8n Launcher setup", str(exc), parent=root)
            return None
    finally:
        if owns_root:
            root.destroy()
