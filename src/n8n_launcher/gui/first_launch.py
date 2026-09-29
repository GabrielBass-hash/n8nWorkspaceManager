"""The first-launch wizard, as far as Tk is concerned.

Everything this wizard *decides* — n8n's password policy, the config it builds,
the Docker precondition, the shortcut — lives in
:mod:`n8n_launcher.core.first_launch`, which prompts for nothing. What is left
here is the one thing a headless module cannot do: ask the person. The prompts
collect the same three values and hand them to the very same
:func:`~n8n_launcher.core.first_launch.run_first_launch` a CLI would call, so
there is one first-launch flow, not two that can drift.
"""

from __future__ import annotations

import contextlib
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

from ..core.config import ConfigStore
from ..core.first_launch import (
    SetupWizardError,
    build_initial_config,
    run_first_launch,
    validate_password,
)
from ..core.models import AppConfig
from ..docker.manager import DockerError, DockerManager
from .theme import configure_fonts


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


__all__ = [
    "SetupWizardError",
    "build_initial_config",
    "run_first_launch",
    "run_interactive_first_launch",
    "validate_password",
]
