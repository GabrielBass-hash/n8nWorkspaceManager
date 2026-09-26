"""The modal dialogs the GitHub Actions CI setup still needs.

The pipeline selection itself is no longer a dialog: it is the "Sélection" tab
of the CI page (:mod:`n8n_launcher.gui.ci_page`), which lives in the main window
next to the workspace list and the journal. What is left here are the two
questions a page cannot answer on its own:

* :func:`prompt_run_ci` — which ref to dispatch the workflow on. A page cannot
  ask a question of its own without stealing the focus from the tab, so this
  stays a small modal prompt.
* :func:`prompt_ci_credentials` — a checkable list of the workspace's n8n
  credentials whose values get copied to the clipboard as JSON, to be pasted
  once as the ``N8N_CI_CREDENTIALS`` GitHub Actions secret. Only the name/type
  metadata is recorded locally afterwards.

Neither dialog reaches the network itself except through the workspace manager
(credentials flow); eligibility and selection are computed purely from the
export files on disk.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from tkinter import messagebox, ttk

from ..core.models import Workspace
from ..workspaces import ci
from ..workspaces.manager import WorkspaceManager
from .layout import WindowFitter, bind_wraplength
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    configure_fonts,
)


def _finish_dialog_setup(
    dialog: tk.Toplevel, root: tk.Tk, *, fitter: WindowFitter | None = None
) -> None:
    """Center a modal dialog over its parent and make it modal (best-effort).

    The width is the one the content asks for, bounded by the screen; a dialog
    fed asynchronously hands in the fitter it will keep asking to grow.
    """
    # Same guarantee as gui/dialogs: a dialog can own its interpreter's first
    # font registration, so make the named UI fonts resolvable on that root.
    with contextlib.suppress(Exception):
        configure_fonts(root)
    try:
        dialog.transient(root)
        dialog.grab_set()
        dialog.update_idletasks()
        if fitter is None:
            fitter = WindowFitter(dialog, parent=root)
        fitter.fit()
    except Exception:
        pass


def _copy_text(widget: tk.Misc, text: str) -> None:
    """Copy *text* to the clipboard of *widget* (best-effort for test fakes)."""
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except Exception:
        pass


def prompt_run_ci(root: tk.Tk, workflow_file: str, default_ref: str) -> str | None:
    """Ask which branch/ref to run the CI workflow on.

    Returns the trimmed ref, or ``None`` when cancelled or left empty. The
    dialog is purely local: the caller performs the ``workflow_dispatch`` call
    on a background thread once a ref is returned. *default_ref* is prefilled
    so the common case is a single click.
    """
    dialog = tk.Toplevel(root)
    dialog.title("Lancer la CI")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(False, False)

    result: str | None = None

    tk.Label(
        dialog,
        text=f"Workflow « {workflow_file} » — branche à exécuter :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(16, 2))
    ref_var = tk.StringVar(value=default_ref)
    ref_entry = tk.Entry(
        dialog,
        textvariable=ref_var,
        bg=SURFACE,
        fg=TEXT_PRIMARY,
        insertbackground=TEXT_PRIMARY,
        relief="flat",
        font=FONT_META,
    )
    ref_entry.pack(fill="x", padx=18, pady=(0, 6))
    intro = tk.Label(
        dialog,
        text=(
            "GitHub Actions exécute alors les pipelines sélectionnées sur cette "
            "branche (fichier .n8n-tests/tests.json du dépôt)."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    intro.pack(fill="x", padx=18, pady=(0, 12))
    bind_wraplength(intro, minimum=200, padding=36)

    buttons = tk.Frame(dialog, bg=APP_BACKGROUND)
    buttons.pack(fill="x", padx=18, pady=(0, 16))

    def submit(_event: tk.Event | None = None) -> None:
        nonlocal result
        ref = ref_var.get().strip()
        if ref:
            result = ref
        dialog.destroy()

    def cancel(_event: tk.Event | None = None) -> None:
        dialog.destroy()

    ttk.Button(buttons, text="Annuler", style="Secondary.TButton", command=cancel).pack(
        side="right"
    )
    ttk.Button(
        buttons,
        text="Lancer",
        style="Accent.TButton",
        cursor="hand2",
        command=submit,
    ).pack(side="right", padx=(8, 0))

    ref_entry.bind("<Return>", submit)
    dialog.bind("<Escape>", cancel)
    _finish_dialog_setup(dialog, root)
    with contextlib.suppress(Exception):
        ref_entry.focus_set()
    dialog.wait_window()
    return result


def prompt_ci_credentials(root: tk.Tk, manager: WorkspaceManager, workspace: Workspace) -> None:
    """Let the user pick the credentials to include in the CI secret.

    Requires a running workspace (the values are read live from its n8n
    instance). The chosen credentials are copied to the clipboard as a JSON
    document to paste into the ``N8N_CI_CREDENTIALS`` GitHub Actions secret;
    when the user confirms pasting, only the name/type metadata is recorded
    through :meth:`WorkspaceManager.set_ci_credentials`.
    """
    if not workspace.api_key:
        messagebox.showwarning(
            "n8n Launcher",
            "Démarrez ce workspace une fois avant de configurer les "
            "credentials CI (le secret est lu dans son instance n8n).",
            parent=root,
        )
        return
    try:
        listed = manager.api_factory(workspace, workspace.api_key).list_credentials()
    except Exception as exc:
        messagebox.showerror("n8n Launcher", str(exc), parent=root)
        return

    dialog = tk.Toplevel(root)
    dialog.title("Credentials CI")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(True, True)

    intro = tk.Label(
        dialog,
        text=(
            "Cochez les credentials n8n à inclure dans les tests GitHub Actions. "
            "Leurs valeurs seront copiées en JSON dans le presse-papier."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    intro.pack(fill="x", padx=18, pady=(14, 2))
    bind_wraplength(intro, minimum=200, padding=36)
    secret_hint = tk.Label(
        dialog,
        text=(
            "Créez le secret GitHub Actions « N8N_CI_CREDENTIALS » sur votre "
            "dépôt (Settings -> Secrets and variables -> Actions) puis collez-y "
            "ce JSON. Le launcher ne conserve que les noms — jamais les valeurs."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
        justify="left",
    )
    secret_hint.pack(fill="x", padx=18, pady=(0, 8))
    bind_wraplength(secret_hint, minimum=200, padding=36)

    rows: list[tuple[tk.IntVar, str, str]] = []
    for item in sorted(listed, key=lambda credential: str(credential.get("name") or "")):
        name = str(item.get("name") or "").strip()
        ctype = str(item.get("type") or "").strip()
        if not name or not ctype:
            continue
        variable = tk.IntVar(value=1)
        rows.append((variable, name, ctype))
        tk.Checkbutton(
            dialog,
            text=f"{name}  ({ctype})",
            variable=variable,
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            selectcolor=SURFACE,
            activebackground=APP_BACKGROUND,
            activeforeground=TEXT_PRIMARY,
            anchor="w",
            highlightthickness=0,
        ).pack(fill="x", padx=18, pady=1)

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
    )
    status.pack(fill="x", padx=18, pady=(4, 0))

    def copy_json() -> None:
        selected = [
            {"name": name, "type": ctype} for variable, name, ctype in rows if variable.get()
        ]
        if not selected:
            messagebox.showwarning(
                "n8n Launcher",
                "Sélectionnez au moins une credential.",
                parent=dialog,
            )
            return
        try:
            payload = manager.ci_credentials_payload(workspace, selected)
        except Exception as exc:
            messagebox.showerror("n8n Launcher", str(exc), parent=dialog)
            return
        _copy_text(dialog, payload)
        status.config(
            text="JSON copié dans le presse-papier — collez-le dans le secret "
            f"« {ci.SECRET_NAME} » puis cliquez « J'ai collé »."
        )

    def confirm_pasted() -> None:
        selected = [
            {"name": name, "type": ctype} for variable, name, ctype in rows if variable.get()
        ]
        try:
            manager.set_ci_credentials(workspace, selected)
        except Exception as exc:
            messagebox.showerror(
                "n8n Launcher",
                f"Impossible d'enregistrer les credentials CI : {exc}",
                parent=dialog,
            )
            return
        dialog.destroy()

    actions = tk.Frame(dialog, bg=APP_BACKGROUND)
    actions.pack(fill="x", padx=18, pady=(10, 14))
    for text, command in (
        ("Copier le JSON", copy_json),
        ("J'ai collé", confirm_pasted),
        ("Annuler", lambda: dialog.destroy()),
    ):
        ttk.Button(
            actions,
            text=text,
            style="Secondary.TButton",
            cursor="hand2",
            command=command,
        ).pack(side="right", padx=(6, 0))

    # Every child is in: the dialog opens as wide as this prose really needs.
    _finish_dialog_setup(dialog, root)
    dialog.wait_window()
