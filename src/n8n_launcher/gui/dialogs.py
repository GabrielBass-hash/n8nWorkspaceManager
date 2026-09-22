"""Modal forms for workspace creation, Git configuration and GitHub setup."""

from __future__ import annotations

import re
import secrets
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .. import git
from ..core.models import DbConfig, DbMode
from ..database import has_db_layout
from ..github import auth
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
    github_create: bool = False


@dataclass(frozen=True)
class GitConfigChoice:
    """Outcome of the git configuration dialog."""

    create_github: bool = False
    remote_url: str | None = None


@dataclass(frozen=True)
class GitHubTokenPlan:
    """Token entered for the GitHub API, plus whether to persist it once."""

    token: str
    remember: bool = False


@dataclass(frozen=True)
class GitHubCreatePlan:
    """Choices for creating a new repository on GitHub."""

    name: str
    private: bool = True
    token: str = ""


def _finish_dialog_setup(
    dialog: tk.Toplevel, root: tk.Tk, *, focus: tk.Widget | None = None
) -> None:
    """Center a modal dialog over its parent, grab input and focus a widget."""
    try:
        dialog.transient(root)
        dialog.grab_set()
        dialog.update_idletasks()
        x = root.winfo_rootx() + max((root.winfo_width() - dialog.winfo_reqwidth()) // 2, 0)
        y = root.winfo_rooty() + max((root.winfo_height() - dialog.winfo_reqheight()) // 3, 0)
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


def prompt_create_plan(root: tk.Tk, workflows_dir: Path, default_db: DbConfig) -> CreatePlan | None:
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
    ).pack(fill="x", padx=18, pady=(0, 4))
    github_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        dialog,
        text="Créer le dépôt distant sur GitHub (jeton demandé ensuite)",
        variable=github_var,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        highlightthickness=0,
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
        db = fresh_managed_db_config() if db_var.get() == "managed" else DbConfig(DbMode.NONE)
        result = CreatePlan(
            name=name_var.get().strip() or workflows_dir.name,
            db=db,
            git_enabled=git_var.get() or github_var.get(),
            git_url=url_var.get().strip() or None,
            github_create=github_var.get(),
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

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        result = value_var.get()
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
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

    def submit(_event: tk.Event | None = None) -> None:
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

    def cancel(_event: tk.Event | None = None) -> None:
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


def prompt_git_remote(root: tk.Tk, workspace_name: str, current_remote: str | None) -> str | None:
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


def prompt_git_config(root: tk.Tk, workspace_name: str) -> GitConfigChoice | None:
    """Ask how to wire git for a workspace that has no remote yet.

    Returns ``None`` when cancelled, a :class:`GitConfigChoice` carrying the
    entered URL (or ``None`` for a purely local repo), or one with
    ``create_github=True`` when the user asks to create the remote repository
    from the app.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Configurer Git")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: GitConfigChoice | None = None

    tk.Label(
        dialog,
        text=f"URL du dépôt distant pour « {workspace_name} »\n"
        "Laisser vide pour un dépôt local uniquement :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
        wraplength=380,
    ).pack(fill="x", padx=18, pady=(16, 8))
    url_var = tk.StringVar(value="")
    url_entry = tk.Entry(
        dialog,
        textvariable=url_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    url_entry.pack(fill="x", padx=18, pady=(0, 16))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def create_github() -> None:
        nonlocal result
        result = GitConfigChoice(create_github=True)
        dialog.destroy()

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        result = GitConfigChoice(remote_url=url_var.get().strip() or None)
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        dialog.destroy()

    ttk.Button(
        buttons,
        text="Créer sur GitHub…",
        style="Secondary.TButton",
        command=create_github,
    ).pack(side="left")
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

    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=url_entry)
    dialog.wait_window()
    return result


def prompt_github_create(
    root: tk.Tk, workspace_name: str, *, token: str | None = None
) -> GitHubCreatePlan | None:
    """Ask for a GitHub repository name, visibility and a personal access token.

    Returns ``None`` when cancelled. *token* prefills the field with the token
    already resolved for the same GitHub account (git credential/``gh``), so
    the user can normally just confirm. The token is used once for the API call
    and one seed push, then discarded; a "Détecter via gh CLI" button refills
    it from ``gh auth token``.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Nouveau dépôt GitHub")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: GitHubCreatePlan | None = None

    tk.Label(
        dialog,
        text="Nom du dépôt sur GitHub :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(16, 2))
    name_var = tk.StringVar(value=_repo_name_from(workspace_name))
    name_entry = tk.Entry(
        dialog,
        textvariable=name_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    name_entry.pack(fill="x", padx=18, pady=(0, 10))

    visibility_var = tk.BooleanVar(value=True)
    tk.Radiobutton(
        dialog,
        text="Privé",
        variable=visibility_var,
        value=True,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        font=FONT_META,
    ).pack(fill="x", padx=18)
    tk.Radiobutton(
        dialog,
        text="Public",
        variable=visibility_var,
        value=False,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        activebackground=APP_BACKGROUND,
        activeforeground=ACCENT_HOVER,
        selectcolor=SURFACE,
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 10))

    tk.Label(
        dialog,
        text="Token GitHub (PAT « repo » ou fine-grained avec Administration) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        justify="left",
        wraplength=380,
    ).pack(fill="x", padx=18)
    token_var = tk.StringVar(value=token or "")
    token_entry = tk.Entry(
        dialog,
        textvariable=token_var,
        show="*",
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    token_entry.pack(fill="x", padx=18, pady=(4, 6))
    ttk.Button(
        dialog,
        text="Détecter via gh CLI",
        style="Secondary.TButton",
        command=lambda: token_var.set(auth.token_from_gh_cli() or ""),
    ).pack(fill="x", padx=18, pady=(0, 14))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        name = name_var.get().strip()
        token = token_var.get().strip()
        if not name:
            messagebox.showwarning(
                "Nouveau dépôt GitHub", "Le nom du dépôt ne peut pas être vide.", parent=dialog
            )
            return
        if not token:
            messagebox.showwarning(
                "Nouveau dépôt GitHub",
                "Le token d'accès GitHub est requis (bouton « Détecter via gh CLI » si "
                "vous avez gh d'installé).",
                parent=dialog,
            )
            return
        result = GitHubCreatePlan(name=name, private=visibility_var.get(), token=token)
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        dialog.destroy()

    ttk.Button(
        buttons,
        text="Annuler",
        style="Secondary.TButton",
        command=cancel,
    ).pack(side="right")
    ttk.Button(
        buttons,
        text="Créer",
        style="Accent.TButton",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    name_entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=name_entry)
    dialog.wait_window()
    return result


def _repo_name_from(workspace_name: str) -> str:
    """Derive a GitHub-usable repository name from a workspace name."""
    lowered = workspace_name.lower().strip()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", lowered)
    return cleaned.strip(".-_ ") or "workspace"


def prompt_github_token(root: tk.Tk) -> GitHubTokenPlan | None:
    """Ask for a GitHub personal access token to display Actions runs.

    Returns a :class:`GitHubTokenPlan` (token plus whether to remember it), or
    ``None`` when cancelled. The token is only persisted when the user ticks
    "Se souvenir" — by default the caller keeps it in memory. A "Coller"
    button fills the field from the clipboard so the token is never typed or
    logged, and "Détecter via gh CLI" reuses an existing ``gh`` login.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Token GitHub")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: GitHubTokenPlan | None = None

    tk.Label(
        dialog,
        text="Token GitHub (lecture des runs Actions) :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(16, 2))
    token_var = tk.StringVar(value="")
    token_entry = tk.Entry(
        dialog,
        textvariable=token_var,
        show="*",
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    token_entry.pack(fill="x", padx=18, pady=(0, 6))
    ttk.Button(
        dialog,
        text="Coller depuis le presse-papiers",
        style="Secondary.TButton",
        command=lambda: token_var.set(_clipboard_text(dialog)),
    ).pack(fill="x", padx=18, pady=(0, 4))
    ttk.Button(
        dialog,
        text="Détecter via gh CLI",
        style="Secondary.TButton",
        command=lambda: token_var.set(auth.token_from_gh_cli() or ""),
    ).pack(fill="x", padx=18, pady=(0, 8))

    remember_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        dialog,
        text="Se souvenir de ce token (enregistré dans la configuration)",
        variable=remember_var,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        selectcolor=SURFACE,
        activebackground=APP_BACKGROUND,
        activeforeground=TEXT_PRIMARY,
        anchor="w",
        highlightthickness=0,
        font=FONT_SUBTITLE,
    ).pack(fill="x", padx=18)
    tk.Label(
        dialog,
        text=(
            "Le token sert à lire vos runs GitHub Actions (permission « Actions »). "
            "Sans « Se souvenir », il n'est gardé qu'en mémoire pour cette session."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        justify="left",
        wraplength=380,
    ).pack(fill="x", padx=18, pady=(4, 12))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        token = token_var.get().strip()
        if token:
            result = GitHubTokenPlan(token=token, remember=remember_var.get())
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
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

    token_entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=token_entry)
    dialog.wait_window()
    return result


def _clipboard_text(widget: tk.Misc) -> str:
    """Return the widget's clipboard contents (best-effort for test fakes)."""
    try:
        return widget.clipboard_get()
    except Exception:
        return ""


@dataclass(frozen=True)
class GitClonePlan:
    """URL et branche optionnelle pour un clonage."""

    url: str
    branch: str | None = None


def prompt_create_source(root: tk.Tk) -> str | None:
    """Demande d'où vient le nouveau workspace : ``"local"`` ou ``"clone"``.

    Retourne ``None`` quand l'utilisateur annule.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Nouveau workspace")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: str | None = None
    choice = tk.StringVar(value="local")

    tk.Label(
        dialog,
        text="Source des workflows :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(16, 4))
    tk.Radiobutton(
        dialog,
        text="Dossier local existant",
        variable=choice,
        value="local",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        selectcolor=SURFACE,
        activebackground=APP_BACKGROUND,
        font=FONT_META,
    ).pack(fill="x", padx=18)
    tk.Radiobutton(
        dialog,
        text="Cloner un dépôt Git (URL)",
        variable=choice,
        value="clone",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        selectcolor=SURFACE,
        activebackground=APP_BACKGROUND,
        font=FONT_META,
    ).pack(fill="x", padx=18, pady=(0, 12))

    def submit() -> None:
        nonlocal result
        result = choice.get()
        dialog.destroy()

    # Les boutons d'action sont packés directement sur le dialogue (sans
    # frame intermédiaire) : les appelants — tests inclus — inspectent
    # ``dialog.children`` pour les retrouver, et le frame n'était que de la
    # plomberie sans autre rôle.
    ttk.Button(dialog, text="Annuler", style="Secondary.TButton", command=dialog.destroy).pack(
        side="right", padx=(0, 18), pady=(0, 16)
    )
    ttk.Button(dialog, text="Continuer", style="Accent.TButton", command=submit).pack(
        side="right", padx=(8, 0), pady=(0, 16)
    )

    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    _finish_dialog_setup(dialog, root)
    dialog.wait_window()
    return result


def prompt_clone_plan(root: tk.Tk) -> GitClonePlan | None:
    """Demande l'URL du dépôt et une branche optionnelle.

    Le bouton « Charger les branches » utilise
    :func:`n8n_launcher.git.git_list_remote_branches` pour peupler le champ :
    l'utilisateur peut alors choisir parmi les dépôts/branches réellement
    disponibles côté remote. Un échec réseau reste non bloquant (l'URL reste
    éditable à la main).
    """

    dialog = tk.Toplevel(root)
    dialog.title("Cloner un dépôt Git")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: GitClonePlan | None = None
    url_var = tk.StringVar(value="")
    branch_var = tk.StringVar(value="")

    tk.Label(
        dialog,
        text="URL du dépôt (HTTPS ou SSH) :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(16, 2))
    url_entry = tk.Entry(
        dialog,
        textvariable=url_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    url_entry.pack(fill="x", padx=18, pady=(0, 6))

    tk.Label(
        dialog,
        text="Branche (optionnel — laisser vide pour la branche par défaut) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=18)
    branch_entry = tk.Entry(
        dialog,
        textvariable=branch_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    branch_entry.pack(fill="x", padx=18, pady=(0, 6))

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        justify="left",
        wraplength=380,
    )
    status.pack(fill="x", padx=18)

    def load_branches() -> None:
        url = url_var.get().strip()
        if not url:
            return
        try:
            branches = git.git_list_remote_branches(url)
        except git.GitError as exc:
            status.config(text=f"Impossible de lister les branches : {exc}")
            return
        if not branches:
            status.config(text="Aucune branche trouvée à cette URL.")
            return
        status.config(text="Branches : " + ", ".join(branches))
        if not branch_var.get():
            branch_var.set("main" if "main" in branches else branches[0])

    ttk.Button(
        dialog, text="Charger les branches", style="Secondary.TButton", command=load_branches
    ).pack(fill="x", padx=18, pady=(6, 14))

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        url = url_var.get().strip()
        if not url:
            messagebox.showwarning(
                "Cloner un dépôt Git", "L'URL du dépôt est requise.", parent=dialog
            )
            return
        result = GitClonePlan(url=url, branch=branch_var.get().strip() or None)
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        dialog.destroy()

    ttk.Button(buttons, text="Annuler", style="Secondary.TButton", command=cancel).pack(
        side="right"
    )
    ttk.Button(buttons, text="Cloner", style="Accent.TButton", command=submit).pack(
        side="right", padx=(8, 0)
    )

    url_entry.bind("<Return>", lambda _e: load_branches())
    branch_entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=url_entry)
    dialog.wait_window()
    return result


@dataclass(frozen=True)
class GitHubRepoPick:
    """Repository selected from the linked account, plus optional branch."""

    clone_url: str
    branch: str | None = None


def prompt_clone_dest(root: tk.Tk, repo_name: str) -> Path | None:
    """Ask for a parent folder; the repo is cloned into ``<parent>/<name>``.

    The folder name is sanitized the same way a created workspace's name would
    be (``_repo_name_from``), so "Mon Repo!*" clones into ``mon-repo``.
    """
    parent = filedialog.askdirectory(
        title=f"Dossier parent où cloner « {repo_name} »",
        parent=root,
    )
    if not parent:
        return None
    return Path(parent) / _repo_name_from(repo_name)


def prompt_github_repo_picker(
    root: tk.Tk,
    *,
    repos: list[dict[str, Any]],
    branches_loader: Callable[[str], list[str]] | None = None,
) -> GitHubRepoPick | None:
    """Let the user pick a repository from the linked GitHub account.

    *repos* is the already-fetched ``GET /user/repos`` payload (the caller
    performs the network call off the Tk thread, so this dialog is pure UI).
    *branches_loader*, when provided, is called with an ``owner/repo`` path
    to fill the branch status; failures are displayed inline and never block
    validation. Returns the clone URL plus the typed/preselected branch, or
    ``None`` on cancel.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Cloner depuis GitHub")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(True, True)

    result: GitHubRepoPick | None = None
    selected: dict[str, Any] = {}
    branch_var = tk.StringVar(value="")

    tk.Label(
        dialog,
        text="Choisissez un dépôt du compte GitHub lié :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(14, 4))

    tree = ttk.Treeview(
        dialog,
        columns=("visibility", "branch", "updated"),
        show="tree headings",
        height=14,
    )
    tree.heading("#0", text="Dépôt")
    tree.heading("visibility", text="Visibilité")
    tree.heading("branch", text="Branche par défaut")
    tree.heading("updated", text="Mis à jour")
    tree.column("#0", width=280, stretch=True)
    tree.column("visibility", width=90, anchor="w")
    tree.column("branch", width=150, anchor="w")
    tree.column("updated", width=120, anchor="w")
    tree.pack(fill="both", expand=True, padx=18, pady=(0, 6))

    # Sorted most-recently-updated first: matches the GitHub web default.
    for repo in sorted(
        repos,
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    ):
        full_name = repo.get("full_name")
        if not isinstance(full_name, str) or not full_name:
            continue
        tree.insert(
            "",
            "end",
            iid=full_name,
            text=full_name,
            values=(
                "privé" if repo.get("private") else "public",
                str(repo.get("default_branch") or "main"),
                str(repo.get("updated_at") or "")[:10],
            ),
        )

    tk.Label(
        dialog,
        text="Branche :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(0, 2))
    branch_entry = tk.Entry(
        dialog,
        textvariable=branch_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    branch_entry.pack(fill="x", padx=18, pady=(0, 4))

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        wraplength=520,
        justify="left",
    )
    status.pack(fill="x", padx=18)

    def pick(full_name: str) -> None:
        repo = next((item for item in repos if item.get("full_name") == full_name), None)
        if repo is None:
            return
        selected.clear()
        selected.update(repo)
        branch_var.set(str(repo.get("default_branch") or "main"))
        if branches_loader is not None:
            try:
                branches = branches_loader(full_name)
            except Exception as exc:
                status.config(text=f"Impossible de lister les branches : {exc}")
            else:
                label = ", ".join(branches) if branches else "Aucune branche."
                status.config(text=f"Branches : {label}")

    def on_select(_event: tk.Event | None = None) -> None:
        selection = tree.selection()
        if selection:
            pick(selection[0])

    tree.bind("<<TreeviewSelect>>", on_select)
    tree.bind("<Double-1>", on_select)

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        if not selected:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning(
                    "Cloner depuis GitHub", "Sélectionnez un dépôt.", parent=dialog
                )
                return
            pick(selection[0])
            if not selected:
                return
        clone_url = selected.get("clone_url")
        if not isinstance(clone_url, str) or not clone_url:
            messagebox.showwarning(
                "Cloner depuis GitHub",
                "Ce dépôt n'expose pas d'URL de clone HTTPS.",
                parent=dialog,
            )
            return
        result = GitHubRepoPick(
            clone_url=clone_url,
            branch=branch_var.get().strip() or None,
        )
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        dialog.destroy()

    # <Return> sur le champ branche valide (comme dans prompt_clone_plan) ;
    # la sélection de la liste pré-remplit déjà la branche via « pick ».
    branch_entry.bind("<Return>", submit)

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(6, 14))
    ttk.Button(buttons, text="Annuler", style="Secondary.TButton", command=cancel).pack(
        side="right"
    )
    ttk.Button(buttons, text="Cloner", style="Accent.TButton", command=submit).pack(
        side="right", padx=(8, 0)
    )

    dialog.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root, focus=branch_entry)
    dialog.wait_window()
    return result
