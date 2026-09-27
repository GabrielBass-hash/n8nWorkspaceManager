"""Modal forms for workspace creation, Git configuration and GitHub setup."""

from __future__ import annotations

import contextlib
import re
import secrets
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .. import git
from ..core.models import DbConfig, DbMode, ServerConfig
from ..database import has_db_layout
from ..github import auth
from .dialog import Dialog
from .layout import ColumnFitter, WindowFitter, bind_wraplength, wrap_at
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    APP_BACKGROUND,
    FONT_META,
    FONT_SUBTITLE,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    text_measure,
)
from .tokens import (
    GUTTER,
    SPACE_2XL,
    SPACE_3XL,
    SPACE_HAIRLINE,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_TIGHT,
    SPACE_XL,
)

# Repository picker: the label column holds ``owner/repo`` and the other three
# hold "public"/"privé", a branch name and a date. The fitter sizes each to what
# it actually contains (see ``gui.layout``) instead of reserving a fixed 280px
# for the name while 90px sit on a five-letter word. The name is the flexible
# column — it is the only one that reads better wide — and the four maximums add
# up to a dialog that still fits a display.
_REPO_COLUMNS = ("visibility", "branch", "updated")
_REPO_HEADINGS = {
    "#0": "Dépôt",
    "visibility": "Visibilité",
    "branch": "Branche par défaut",
    "updated": "Mis à jour",
}
_REPO_MINIMUMS = {"#0": 200, "visibility": 80, "branch": 90, "updated": 90}
_REPO_MAXIMUMS = {"#0": 520, "visibility": 130, "branch": 180, "updated": 150}


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
    dialog: Dialog[CreatePlan] = Dialog(root, "Nouveau workspace", primary="Créer")

    tk.Label(
        dialog,
        text="Nom du workspace",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_2XL, SPACE_HAIRLINE))
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
    name_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
    tk.Label(
        dialog,
        text=str(workflows_dir),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

    tk.Label(
        dialog,
        text="Base de données",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_HAIRLINE))
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
    ).pack(fill="x", padx=GUTTER)
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
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

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
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_TIGHT, SPACE_HAIRLINE))
    tk.Label(
        dialog,
        text="URL du dépôt distant (optionnel) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=GUTTER)
    url_var = tk.StringVar(value="")
    tk.Entry(
        dialog,
        textvariable=url_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_TIGHT))
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
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_XL))

    def submit() -> None:
        db = fresh_managed_db_config() if db_var.get() == "managed" else DbConfig(DbMode.NONE)
        dialog.settle(
            CreatePlan(
                name=name_var.get().strip() or workflows_dir.name,
                db=db,
                git_enabled=git_var.get() or github_var.get(),
                git_url=url_var.get().strip() or None,
                github_create=github_var.get(),
            )
        )

    dialog.on_submit = submit
    name_entry.bind("<Return>", lambda _event: submit())
    return dialog.wait(focus=name_entry)


def prompt_ask_string(
    root: tk.Tk,
    title: str,
    prompt: str,
    *,
    initial: str = "",
) -> str | None:
    """Ask for a single string in a themed dark dialog; ``None`` on cancel."""
    dialog: Dialog[str] = Dialog(root, title)

    prompt_label = tk.Label(
        dialog,
        text=prompt,
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    prompt_label.pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_MD))
    bind_wraplength(prompt_label, minimum=200, padding=2 * GUTTER)
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
    entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_2XL))

    def submit(_event: tk.Event | None = None) -> None:
        dialog.settle(value_var.get())

    dialog.on_submit = submit
    entry.bind("<Return>", submit)
    return dialog.wait(focus=entry)


def prompt_server_config(
    root: tk.Tk,
    workspace_name: str,
    current: ServerConfig,
    default_base: str,
) -> ServerConfig | None:
    """Ask for the SSH/server deployment settings for a workspace.

    Returns an enabled :class:`ServerConfig` when saved, ``None`` when
    cancelled. Only the key *path* is captured — never key material — and the
    base directory defaults to ``n8n-launcher/<workspace id>``.
    """
    dialog: Dialog[ServerConfig] = Dialog(root, "Serveur de production", primary="Enregistrer")

    intro = tk.Label(
        dialog,
        text=f"Déploiement serveur pour « {workspace_name} »\n"
        "Le serveur reçoit vos pipelines via Git (branche « main ») — "
        "une clé SSH est requise :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    intro.pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_MD))
    bind_wraplength(intro, minimum=200, padding=2 * GUTTER)

    def field(label: str, value: str) -> tuple[tk.StringVar, tk.Entry]:
        tk.Label(
            dialog,
            text=label,
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_SUBTITLE,
            anchor="w",
        ).pack(fill="x", padx=GUTTER)
        var = tk.StringVar(value=value)
        entry = tk.Entry(
            dialog,
            textvariable=var,
            bg=SURFACE,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief="flat",
            font=FONT_META,
        )
        entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
        return var, entry

    host_var, _ = field("Hôte :", current.host)
    ssh_var, _ = field("Port SSH :", str(current.ssh_port))
    user_var, _ = field("Utilisateur :", current.user)
    base_var, _ = field("Dossier de base (distant) :", current.base_dir or default_base)
    port_var, _ = field("Port n8n (sur le serveur) :", str(current.n8n_port))

    key_var = tk.StringVar(value=current.key_path or "")
    key_label = tk.Label(
        dialog,
        text="Clé SSH (chemin) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    )
    key_label.pack(fill="x", padx=GUTTER)
    key_row = tk.Frame(dialog, bg=APP_BACKGROUND)
    key_row.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
    key_entry = tk.Entry(
        key_row,
        textvariable=key_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    key_entry.pack(side="left", fill="x", expand=True)

    def browse_key() -> None:
        chosen = filedialog.askopenfilename(parent=dialog, title="Choisir la clé privée SSH")
        if chosen:
            key_var.set(chosen)

    ttk.Button(
        key_row,
        text="Parcourir…",
        style="Secondary.TButton",
        command=browse_key,
    ).pack(side="right", padx=(SPACE_MD, 0))

    def submit(_event: tk.Event | None = None) -> None:
        host = host_var.get().strip()
        user = user_var.get().strip()
        if not host or not user:
            messagebox.showwarning(
                "n8n Launcher",
                "L'hôte et l'utilisateur sont obligatoires.",
                parent=dialog,
            )
            return
        dialog.settle(
            ServerConfig(
                enabled=True,
                host=host,
                ssh_port=_int_or(ssh_var.get(), 22),
                user=user,
                key_path=key_var.get().strip() or None,
                base_dir=base_var.get().strip(),
                n8n_port=_int_or(port_var.get(), 5678),
            )
        )

    dialog.on_submit = submit
    key_entry.bind("<Return>", submit)
    return dialog.wait(focus=key_entry)


def _int_or(value: str, fallback: int) -> int:
    """Parse a config port from a dialog field, falling back for junk input."""
    try:
        return int(value.strip())
    except ValueError:
        return fallback


def prompt_db_config(root: tk.Tk, current: DbConfig) -> DbConfig | None:
    """Let the user review/edit the workspace database configuration.

    Returns the chosen :class:`DbConfig` or ``None`` when cancelled. For a
    database already in ``MANAGED`` mode the identity fields are locked: the
    Postgres role and the n8n credential match the running values, so changing
    them would break both. Switch to "Aucune base" then back to "Locale" to
    regenerate everything. Password fields left empty are regenerated by the
    manager on save.
    """
    dialog: Dialog[DbConfig] = Dialog(root, "Base de données")
    locked = current.mode is DbMode.MANAGED

    mode_var = tk.StringVar(value="managed" if current.mode is DbMode.MANAGED else "none")
    tk.Label(
        dialog,
        text="Mode de base de données",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_2XL, SPACE_HAIRLINE))
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
    ).pack(fill="x", padx=GUTTER)
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
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

    def field(label: str, value: str, *, enabled: bool = True) -> tuple[tk.StringVar, tk.Entry]:
        tk.Label(
            dialog,
            text=label,
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_SUBTITLE,
            anchor="w",
        ).pack(fill="x", padx=GUTTER)
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
        entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
        return var, entry

    if locked:
        locked_hint = tk.Label(
            dialog,
            text="Paramètres figés pour une base déjà en service : basculez sur "
            "« Aucune base » puis « Locale » pour les régénérer.",
            bg=APP_BACKGROUND,
            fg=ACCENT,
            font=FONT_SUBTITLE,
            anchor="w",
            justify="left",
        )
        locked_hint.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
        bind_wraplength(locked_hint, minimum=200, padding=2 * GUTTER)

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
    ).pack(anchor="w", padx=GUTTER, pady=(0, SPACE_XL))

    def submit(_event: tk.Event | None = None) -> None:
        if mode_var.get() == "managed":
            dialog.settle(
                DbConfig(
                    DbMode.MANAGED,
                    database_name=db_var.get().strip() or None,
                    username=user_var.get().strip() or None,
                    password=pass_var.get() or None,
                )
            )
        else:
            dialog.settle(DbConfig(DbMode.NONE))

    dialog.on_submit = submit
    db_name_entry.bind("<Return>", submit)
    return dialog.wait(focus=db_name_entry)


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
    dialog: Dialog[GitConfigChoice] = Dialog(root, "Configurer Git")

    url_intro = tk.Label(
        dialog,
        text=f"URL du dépôt distant pour « {workspace_name} »\n"
        "Laisser vide pour un dépôt local uniquement :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    url_intro.pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_MD))
    bind_wraplength(url_intro, minimum=200, padding=2 * GUTTER)
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
    url_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_3XL))

    def create_github() -> None:
        dialog.settle(GitConfigChoice(create_github=True))

    def submit(_event: tk.Event | None = None) -> None:
        dialog.settle(GitConfigChoice(remote_url=url_var.get().strip() or None))

    dialog.add_action("Créer sur GitHub…", create_github, side="left")
    dialog.on_submit = submit
    return dialog.wait(focus=url_entry)


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
    dialog: Dialog[GitHubCreatePlan] = Dialog(root, "Nouveau dépôt GitHub", primary="Créer")

    tk.Label(
        dialog,
        text="Nom du dépôt sur GitHub :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_HAIRLINE))
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
    name_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

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
    ).pack(fill="x", padx=GUTTER)
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
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

    token_hint = tk.Label(
        dialog,
        text="Token GitHub (PAT « repo » ou fine-grained avec Administration) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        justify="left",
    )
    token_hint.pack(fill="x", padx=GUTTER)
    bind_wraplength(token_hint, minimum=200, padding=2 * GUTTER)
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
    token_entry.pack(fill="x", padx=GUTTER, pady=(SPACE_TIGHT, SPACE_SM))
    ttk.Button(
        dialog,
        text="Détecter via gh CLI",
        style="Secondary.TButton",
        command=lambda: token_var.set(auth.token_from_gh_cli() or ""),
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_2XL))

    def submit(_event: tk.Event | None = None) -> None:
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
        dialog.settle(GitHubCreatePlan(name=name, private=visibility_var.get(), token=token))

    dialog.on_submit = submit
    name_entry.bind("<Return>", submit)
    return dialog.wait(focus=name_entry)


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
    dialog: Dialog[GitHubTokenPlan] = Dialog(root, "Token GitHub")

    tk.Label(
        dialog,
        text="Token GitHub (lecture des runs Actions) :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_HAIRLINE))
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
    token_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
    ttk.Button(
        dialog,
        text="Coller depuis le presse-papiers",
        style="Secondary.TButton",
        command=lambda: token_var.set(_clipboard_text(dialog)),
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_TIGHT))
    ttk.Button(
        dialog,
        text="Détecter via gh CLI",
        style="Secondary.TButton",
        command=lambda: token_var.set(auth.token_from_gh_cli() or ""),
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_MD))

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
    ).pack(fill="x", padx=GUTTER)
    remember_hint = tk.Label(
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
    )
    remember_hint.pack(fill="x", padx=GUTTER, pady=(SPACE_TIGHT, SPACE_XL))
    bind_wraplength(remember_hint, minimum=200, padding=2 * GUTTER)

    def submit(_event: tk.Event | None = None) -> None:
        token = token_var.get().strip()
        # An empty token is not an error: the caller simply has no token to show
        # runs with, which is the same answer as cancelling.
        if token:
            dialog.settle(GitHubTokenPlan(token=token, remember=remember_var.get()))
        else:
            dialog.cancel()

    dialog.on_submit = submit
    token_entry.bind("<Return>", submit)
    return dialog.wait(focus=token_entry)


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
    dialog: Dialog[str] = Dialog(root, "Nouveau workspace", primary="Continuer")
    choice = tk.StringVar(value="local")

    tk.Label(
        dialog,
        text="Source des workflows :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_TIGHT))
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
    ).pack(fill="x", padx=GUTTER)
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
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_XL))

    def submit() -> None:
        dialog.settle(choice.get())

    dialog.on_submit = submit
    return dialog.wait()


def prompt_clone_plan(root: tk.Tk) -> GitClonePlan | None:
    """Demande l'URL du dépôt et une branche optionnelle.

    Le bouton « Charger les branches » utilise
    :func:`n8n_launcher.git.git_list_remote_branches` pour peupler le champ :
    l'utilisateur peut alors choisir parmi les dépôts/branches réellement
    disponibles côté remote. Un échec réseau reste non bloquant (l'URL reste
    éditable à la main).
    """

    dialog: Dialog[GitClonePlan] = Dialog(root, "Cloner un dépôt Git", primary="Cloner")
    url_var = tk.StringVar(value="")
    branch_var = tk.StringVar(value="")

    tk.Label(
        dialog,
        text="URL du dépôt (HTTPS ou SSH) :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_HAIRLINE))
    url_entry = tk.Entry(
        dialog,
        textvariable=url_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    url_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))

    tk.Label(
        dialog,
        text="Branche (optionnel — laisser vide pour la branche par défaut) :",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
    ).pack(fill="x", padx=GUTTER)
    branch_entry = tk.Entry(
        dialog,
        textvariable=branch_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    branch_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        justify="left",
    )
    # The branch list echoes GitHub's answer verbatim, errors included: it wraps
    # at the dialog's real width rather than at a fixed number of pixels.
    bind_wraplength(status, minimum=200, padding=2 * GUTTER)
    status.pack(fill="x", padx=GUTTER)

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
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_SM, SPACE_2XL))

    def submit(_event: tk.Event | None = None) -> None:
        url = url_var.get().strip()
        if not url:
            messagebox.showwarning(
                "Cloner un dépôt Git", "L'URL du dépôt est requise.", parent=dialog
            )
            return
        dialog.settle(GitClonePlan(url=url, branch=branch_var.get().strip() or None))

    dialog.on_submit = submit
    # <Return> on the URL field loads branches rather than submitting: the user
    # is still typing an address at that point, and the branch list is the only
    # thing that answer can improve.
    url_entry.bind("<Return>", lambda _e: load_branches())
    branch_entry.bind("<Return>", submit)
    return dialog.wait(focus=url_entry)


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


def _dialog_room(dialog: tk.Toplevel, padding: int = 2 * GUTTER) -> int:
    """Return the pixels a dialog's table has, its own padding deducted."""
    with contextlib.suppress(Exception):
        return max(int(dialog.winfo_width()) - padding, 1)
    return 1


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
    dialog: Dialog[GitHubRepoPick] = Dialog(
        root,
        "Cloner depuis GitHub",
        primary="Cloner",
        resizable=True,
        # The bar tucks under a 14-row table rather than sitting at the window's
        # foot, so it keeps the tighter bottom padding this dialog always had.
        bar_pady=(SPACE_SM, SPACE_2XL),
    )
    selected: dict[str, Any] = {}
    branch_var = tk.StringVar(value="")

    tk.Label(
        dialog,
        text="Choisissez un dépôt du compte GitHub lié :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_2XL, SPACE_TIGHT))

    tree = ttk.Treeview(
        dialog,
        columns=("visibility", "branch", "updated"),
        show="tree headings",
        height=14,
    )
    for name, heading in _REPO_HEADINGS.items():
        tree.heading(name, text=heading)
    for name in ("#0", *_REPO_COLUMNS):
        # Request the minimum up front: the dialog opens as wide as "owner/repo"
        # plus its three short fields really need, not as wide as a 280px guess.
        tree.column(
            name,
            width=_REPO_MINIMUMS[name],
            minwidth=_REPO_MINIMUMS[name],
            stretch=False,
            anchor="w",
        )
    # The room is the dialog's own width, so the table follows the window the
    # user dragged it to: narrower, the columns are compressed down together
    # instead of the last one being the one Tk cuts; wider, the name takes the
    # slack. Unmapped (width 1) it stays on its pure content sizing, which is
    # what makes ``WindowFitter`` below open the dialog at that content width.
    fitter = ColumnFitter(
        tree,
        columns=_REPO_COLUMNS,
        headings=_REPO_HEADINGS,
        minimums=_REPO_MINIMUMS,
        maximums=_REPO_MAXIMUMS,
        measure=text_measure(dialog, FONT_META),
        container=dialog,
        available=lambda: _dialog_room(dialog),
        flexible="#0",
    )
    # The dialog opens as wide as "owner/repo" plus its three fields really need,
    # bounded by the screen, and grows if a later page of repositories is wider.
    window_fitter = WindowFitter(dialog, parent=root)
    # No horizontal fill: ``winfo_reqwidth`` above is the width of the table, so
    # the dialog opens exactly as wide as the columns it holds.
    tree.pack(fill="y", anchor="nw", expand=True, padx=GUTTER, pady=(0, SPACE_SM))

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

    # The list is filled: let each column take the width of what it holds, so a
    # repository name is never cut to make room for "public" or a date.
    fitter.rows()

    tk.Label(
        dialog,
        text="Branche :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(0, SPACE_HAIRLINE))
    branch_entry = tk.Entry(
        dialog,
        textvariable=branch_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    branch_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_TIGHT))

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_SUBTITLE,
        anchor="w",
        wraplength=sum(_REPO_MINIMUMS.values()),
        justify="left",
    )
    # The branch list echoes GitHub's answer verbatim, errors included: it wraps
    # at the dialog's real width instead of a hard-coded 520 pixels. The seed is
    # the table's narrowest width and the first pass uses the width it has just
    # been given, so the note never widens the dialog past the table it explains
    # — the dialog opens at the width of its content.
    bind_wraplength(status, minimum=200, padding=2 * GUTTER)
    wrap_at(status, fitter.total(), minimum=200, padding=2 * GUTTER)
    status.pack(fill="x", padx=GUTTER)

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
        dialog.settle(
            GitHubRepoPick(
                clone_url=clone_url,
                branch=branch_var.get().strip() or None,
            )
        )

    dialog.on_submit = submit
    # <Return> validates here, both on the window and on the branch field (as in
    # prompt_clone_plan); picking from the list already prefills the branch
    # through « pick ».
    dialog.bind("<Return>", submit)
    branch_entry.bind("<Return>", submit)
    return dialog.wait(focus=branch_entry, fitter=window_fitter)
