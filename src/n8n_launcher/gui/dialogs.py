"""Modal forms for workspace creation and Git configuration."""

from __future__ import annotations

import secrets
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from ..core.models import DbConfig, DbMode
from ..database import has_db_layout
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    APP_BACKGROUND,
    FONT_META,
    FONT_SUBTITLE,
    SURFACE,
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


def _finish_dialog_setup(dialog: tk.Toplevel, root: tk.Tk, *, focus=None) -> None:
    """Center a modal dialog over its parent, grab input and focus a widget."""
    try:
        dialog.transient(root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = root.winfo_rootx() + max(
            (root.winfo_width() - dialog.winfo_reqwidth()) // 2, 0
        )
        y = root.winfo_rooty() + max(
            (root.winfo_height() - dialog.winfo_reqheight()) // 3, 0
        )
        dialog.geometry(f"+{x}+{y}")
        if focus is not None:
            focus.focus_set()
    except Exception:
        # Positioning is best-effort; the dialog must still open on exotic WMs.
        pass


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
    name_entry = tk.Entry(
        dialog,
        textvariable=name_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    name_entry.pack(fill="x", padx=18, pady=(0, 6))
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
    ttk.Button(
        buttons,
        text="Annuler",
        style="Secondary.TButton",
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

    name_entry.bind("<Return>", lambda _event: submit())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    _finish_dialog_setup(dialog, root, focus=name_entry)

    ttk.Button(
        buttons,
        text="Créer",
        style="Accent.TButton",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    dialog.wait_window()
    return result


def prompt_ask_string(
    root: tk.Tk,
    title: str,
    prompt: str,
    *,
    initial: str = "",
) -> str | None:
    """Ask for a single string in a themed dark dialog; ``None`` on cancel."""
    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: str | None = None

    tk.Label(
        dialog,
        text=prompt,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
        wraplength=380,
    ).pack(fill="x", padx=18, pady=(16, 8))
    value_var = tk.StringVar(value=initial)
    entry = tk.Entry(
        dialog,
        textvariable=value_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    entry.pack(fill="x", padx=18, pady=(0, 14))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event=None) -> None:
        nonlocal result
        result = value_var.get()
        dialog.destroy()

    def cancel(_event=None) -> None:
        dialog.destroy()

    ttk.Button(
        buttons,
        text="Annuler",
        style="Secondary.TButton",
        command=cancel,
    ).pack(side="right")
    ttk.Button(
        buttons,
        text="Valider",
        style="Accent.TButton",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=entry)
    dialog.wait_window()
    return result


def prompt_db_config(root: tk.Tk, current: DbConfig) -> DbConfig | None:
    """Let the user review/edit the workspace database configuration.

    Returns the chosen :class:`DbConfig` or ``None`` when cancelled. For a
    database already in ``MANAGED`` mode the identity fields are locked: the
    Postgres role and the n8n credential match the running values, so changing
    them would break both. Switch to "Aucune base" then back to "Locale" to
    regenerate everything. Password fields left empty are regenerated by the
    manager on save.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Base de données")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: DbConfig | None = None
    locked = current.mode is DbMode.MANAGED

    mode_var = tk.StringVar(value="managed" if current.mode is DbMode.MANAGED else "none")
    tk.Label(
        dialog,
        text="Mode de base de données",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(14, 2))
    tk.Radiobutton(
        dialog,
        text="Locale (PostgreSQL géré par le launcher)",
        variable=mode_var,
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
        variable=mode_var,
        value="none",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 10))

    def field(label: str, value: str, *, enabled: bool = True) -> tuple[tk.StringVar, tk.Entry]:
        tk.Label(
            dialog,
            text=label,
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_SUBTITLE,
            anchor="w",
        ).pack(fill="x", padx=18)
        var = tk.StringVar(value=value)
        entry = tk.Entry(
            dialog,
            textvariable=var,
            bg=SURFACE,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief="flat",
            font=FONT_META,
            state="normal" if enabled else "disabled",
        )
        entry.pack(fill="x", padx=18, pady=(0, 6))
        return var, entry

    if locked:
        tk.Label(
            dialog,
            text="Paramètres figés pour une base déjà en service : basculez sur "
            "« Aucune base » puis « Locale » pour les régénérer.",
            bg=APP_BACKGROUND,
            fg=ACCENT,
            font=FONT_SUBTITLE,
            anchor="w",
            wraplength=420,
            justify="left",
        ).pack(fill="x", padx=18, pady=(0, 6))

    db_var, db_name_entry = field(
        "Base de données :", current.database_name or "data", enabled=not locked
    )
    user_var, _ = field("Utilisateur :", current.username or "n8ndata", enabled=not locked)
    pass_var, _ = field("Mot de passe :", current.password or "", enabled=not locked)

    def generate_password() -> None:
        pass_var.set(secrets.token_hex(16))

    ttk.Button(
        dialog,
        text="Régénérer le mot de passe",
        style="Secondary.TButton",
        state="disabled" if locked else "normal",
        command=generate_password,
    ).pack(anchor="w", padx=18, pady=(0, 12))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event=None) -> None:
        nonlocal result
        if mode_var.get() == "managed":
            result = DbConfig(
                DbMode.MANAGED,
                database_name=db_var.get().strip() or None,
                username=user_var.get().strip() or None,
                password=pass_var.get() or None,
            )
        else:
            result = DbConfig(DbMode.NONE)
        dialog.destroy()

    def cancel(_event=None) -> None:
        dialog.destroy()

    ttk.Button(
        buttons,
        text="Annuler",
        style="Secondary.TButton",
        command=cancel,
    ).pack(side="right")
    ttk.Button(
        buttons,
        text="Valider",
        style="Accent.TButton",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    db_name_entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=db_name_entry)
    dialog.wait_window()
    return result


def prompt_git_remote(
    root: tk.Tk, workspace_name: str, current_remote: str | None
) -> str | None:
    """Ask for a new remote URL; ``None`` means the user cancelled."""
    message = (
        f"URL du dépôt distant pour « {workspace_name} »"
        f"{(f' (actuelle : {current_remote})' if current_remote else '')}\n"
        "Laisser vide pour un dépôt local uniquement :"
    )
    return prompt_ask_string(
        root,
        "Configurer Git",
        message,
        initial=current_remote or "",
    )