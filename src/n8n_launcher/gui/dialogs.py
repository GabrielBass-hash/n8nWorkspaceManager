"""Modal forms for workspace creation and Git configuration."""

from __future__ import annotations

import secrets
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, simpledialog

from ..core.models import DbConfig, DbMode
from ..database import has_db_layout
from .theme import (
    ACCENT,
    ACCENT_ACTIVE,
    ACCENT_HOVER,
    APP_BACKGROUND,
    BORDER,
    FONT_META,
    FONT_SUBTITLE,
    SURFACE,
    SURFACE_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


@dataclass
class CreatePlan:
    """Chosen options for creating a new workspace (single-dialog workflow)."""

    name: str
    db: DbConfig
    git_enabled: bool = False
    git_url: str | None = None


def prompt_create_dir(root: tk.Tk) -> Path | None:
    """Let the user pick a workspace folder; create it when it does not exist."""
    directory = filedialog.askdirectory(
        title="Dossier du workspace (workflows JSON dans n8nPipelines/)",
        parent=root,
    )
    if not directory:
        return None
    workflows_dir = Path(directory)
    workflows_dir.mkdir(parents=True, exist_ok=True)
    return workflows_dir


def fresh_managed_db_config() -> DbConfig:
    """Return a MANAGED DB config with a freshly generated password."""
    return DbConfig(
        mode=DbMode.MANAGED,
        database_name="data",
        username="n8ndata",
        password=secrets.token_hex(16),
    )


def default_creation_db(workflows_dir: Path) -> DbConfig:
    """Pick the creation-dialog DB default: managed when a DB layout exists."""
    if has_db_layout(workflows_dir):
        return fresh_managed_db_config()
    return DbConfig(DbMode.NONE)


def prompt_create_plan(
    root: tk.Tk, workflows_dir: Path, default_db: DbConfig
) -> CreatePlan | None:
    """Show the single creation form; returns a plan or None when cancelled."""
    dialog = tk.Toplevel(root)
    dialog.title("Nouveau workspace")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)
    try:
        dialog.transient(root)
    except Exception:
        pass

    result: CreatePlan | None = None

    tk.Label(
        dialog,
        text="Nom du workspace",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(14, 2))
    name_var = tk.StringVar(value=workflows_dir.name)
    tk.Entry(
        dialog,
        textvariable=name_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 6))
    tk.Label(
        dialog,
        text=str(workflows_dir),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(0, 10))

    tk.Label(
        dialog,
        text="Base de données",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(0, 2))
    db_var = tk.StringVar(value="managed" if default_db.mode is DbMode.MANAGED else "none")
    tk.Radiobutton(
        dialog,
        text="Locale (PostgreSQL, schéma et migrations gérés)",
        variable=db_var,
        value="managed",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        font=FONT_META,
    ).pack(fill="x", padx=18)
    tk.Radiobutton(
        dialog,
        text="Aucune base",
        variable=db_var,
        value="none",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 10))

    git_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        dialog,
        text="Activer Git (sauvegarde des workflows)",
        variable=git_var,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        highlightthickness=0,
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(4, 2))
    tk.Label(
        dialog,
        text="URL du dépôt distant (optionnel) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=18)
    url_var = tk.StringVar(value="")
    tk.Entry(
        dialog,
        textvariable=url_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 12))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))
    tk.Button(
        buttons,
        text="Annuler",
        bg=BORDER,
        fg=TEXT_PRIMARY,
        activebackground=SURFACE_HOVER,
        activeforeground=TEXT_PRIMARY,
        relief="flat",
        borderwidth=0,
        padx=14,
        pady=6,
        cursor="hand2",
        command=dialog.destroy,
    ).pack(side="right")

    def submit() -> None:
        nonlocal result
        db = (
            fresh_managed_db_config()
            if db_var.get() == "managed"
            else DbConfig(DbMode.NONE)
        )
        result = CreatePlan(
            name=name_var.get().strip() or workflows_dir.name,
            db=db,
            git_enabled=git_var.get(),
            git_url=url_var.get().strip() or None,
        )
        dialog.destroy()

    tk.Button(
        buttons,
        text="Créer",
        bg=ACCENT,
        fg="#ffffff",
        activebackground=ACCENT_ACTIVE,
        activeforeground="#ffffff",
        relief="flat",
        borderwidth=0,
        padx=16,
        pady=6,
        cursor="hand2",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    dialog.wait_window()
    return result


def prompt_git_remote(
    root: tk.Tk, workspace_name: str, current_remote: str | None
) -> str | None:
    """Ask for a new remote URL; ``None`` means the user cancelled."""
    return simpledialog.askstring(
        "Configurer Git",
        f"URL du dépôt distant pour « {workspace_name} »"
        f"{(f' (actuelle : {current_remote})' if current_remote else '')}\n"
        "Laisser vide pour un dépôt local uniquement :",
        parent=root,
    )