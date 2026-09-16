"""Modal dialogs for the GitHub Actions CI configuration.

Two dialogs live here:

* :func:`prompt_ci_workflows` — an expandable pipeline tree (collapsed by
  default, with per-pipeline counters) letting the user check the *complete*
  pipelines to run in CI. Nodes under each pipeline are shown read-only
  (type, pinned badge); ineligible pipelines are greyed out with an explicit
  reason and cannot be checked.
* :func:`prompt_ci_credentials` — a checkable list of the workspace's n8n
  credentials whose values get copied to the clipboard as JSON, to be pasted
  once as the ``N8N_CI_CREDENTIALS`` GitHub Actions secret. Only the name/type
  metadata is recorded locally afterwards.

Neither dialog reaches the network itself except through the workspace
manager (credentials flow); eligibility is computed purely from the export
files on disk.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..core.models import Workspace
from ..workspaces import ci
from ..workspaces.manager import WorkspaceManager
from .theme import (
    APP_BACKGROUND,
    BORDER,
    FONT_META,
    FONT_PILL,
    SURFACE,
    SURFACE_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

# Colours used for the tree tags (dark theme).
_COLOR_WARN = "#fcd34d"
_COLOR_DISABLED = TEXT_MUTED

_CHECKBOX = "\u2611"
_CHECKBOX_EMPTY = "\u2610"
_BLOCKED = "\u2013"


def _finish_dialog_setup(dialog: tk.Toplevel, root: tk.Tk) -> None:
    """Center a modal dialog over its parent and make it modal (best-effort)."""
    try:
        dialog.transient(root)
        dialog.grab_set()
        dialog.update_idletasks()
    except Exception:
        pass


def _copy_text(widget: tk.Misc, text: str) -> None:
    """Copy *text* to the clipboard of *widget* (best-effort for test fakes)."""
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except Exception:
        pass


def prompt_ci_workflows(
    root: tk.Tk, workspace: Workspace
) -> "tuple[set[str], bool] | None":
    """Let the user pick the pipelines to run in GitHub Actions.

    Returns ``(selected_paths, push_now)`` on save, or ``None`` when
    cancelled. The selection is *not* persisted here — the caller writes it
    through the workspace manager so git operations stay off the main thread.
    """
    provided = ci.provided_credentials(workspace.git.ci_credentials)
    exports: dict[str, dict[str, Any]] = {}
    eligible: dict[str, bool] = {}
    reasons: dict[str, str] = {}
    for rel in ci.collect_workflows(workspace.workflows_dir):
        export = ci.load_export(workspace.workflows_dir, rel)
        if export is None:
            exports[rel] = {}
            eligible[rel] = False
            reasons[rel] = "export invalide"
            continue
        exports[rel] = export
        ok, reason = ci.workflow_eligibility(export, provided)
        eligible[rel] = ok
        reasons[rel] = reason

    dialog = tk.Toplevel(root)
    dialog.title("Tests GitHub Actions")
    dialog.configure(bg=APP_BACKGROUND)
    dialog.resizable(True, True)

    result: tuple[set[str], bool] | None = None
    selection = set(ci.read_selection(workspace.workflows_dir))
    selection &= set(exports)

    tk.Label(
        dialog,
        text="Sélectionnez les pipelines complètes à tester dans GitHub Actions",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(14, 2))
    tk.Label(
        dialog,
        text=f"Workspace « {workspace.name} » — un clic sur une case sélectionne la "
        "pipeline. Les nœuds sont affichés en lecture seule.",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=18, pady=(0, 8))

    tree = ttk.Treeview(dialog, columns=("detail",), show="tree headings", height=18)
    tree.heading("#0", text="Pipeline")
    tree.heading("detail", text="Détails")
    tree.column("#0", width=360, stretch=True)
    tree.column("detail", width=460, stretch=True, anchor="w")
    tree.configure(selectmode="none")
    for tag, color in (("disabled", _COLOR_DISABLED), ("warn", _COLOR_WARN), ("muted", _COLOR_DISABLED)):
        tree.tag_configure(tag, foreground=color)
    tree.pack(fill="both", expand=True, padx=18, pady=(0, 8))

    counter_label = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    )
    counter_label.pack(fill="x", padx=18, pady=(0, 8))

    # Build the tree: pipeline rows at the top level, node rows as read-only
    # children. Non-eligible pipelines get a blocked marker and the reason.
    for rel in sorted(exports):
        export = exports[rel]
        if eligible[rel]:
            detail = f"{len(export.get('nodes') or [])} nœud(s) · {ci.start_description(export)}"
            marker = _CHECKBOX if rel in selection else _CHECKBOX_EMPTY
            tags: tuple[str, ...] = ()
        else:
            detail = reasons[rel]
            marker = _BLOCKED
            tags = ("disabled",)
        pipeline_item = tree.insert(
            "", "end", iid=rel, text=f"{marker}  {rel}", values=(detail,), tags=tags
        )
        for node in export.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            subtitle, style = ci.node_detail(export, node)
            tree.insert(
                pipeline_item,
                "end",
                text=f"    {node.get('name')}",
                values=(subtitle,),
                tags=(style,) if style else (),
            )
        tree.item(pipeline_item, open=False)

    def update_caption() -> None:
        testable = sum(1 for ok in eligible.values() if ok)
        counter_label.config(
            text=f"{len(selection)} pipeline(s) sélectionnée(s) sur {testable} testable(s)"
        )

    def update_row(rel: str) -> None:
        if eligible.get(rel):
            marker = _CHECKBOX if rel in selection else _CHECKBOX_EMPTY
            tree.item(rel, text=f"{marker}  {rel}")

    def toggle(rel: str) -> None:
        if not eligible.get(rel):
            return
        if rel in selection:
            selection.discard(rel)
        else:
            selection.add(rel)
        update_row(rel)
        update_caption()

    def on_tree_click(event) -> None:
        # Only toggle when clicking the item area itself (not scrollbar gaps).
        try:
            if tree.identify("region", event.x, event.y) not in ("cell", "tree"):
                return
            item = tree.identify_row(event.y)
        except Exception:
            return
        if item:
            toggle(item)

    tree.bind("<Button-1>", on_tree_click)

    def select_all() -> None:
        for rel in eligible:
            if eligible[rel]:
                selection.add(rel)
                update_row(rel)
        update_caption()

    def clear_all() -> None:
        selection.clear()
        for rel in eligible:
            update_row(rel)
        update_caption()

    def save() -> None:
        push = False
        if selection:
            push = messagebox.askyesno(
                "Pousser maintenant ?",
                "Pousser ces changements vers GitHub maintenant ?\n"
                "Sinon ils seront inclus au prochain poussage (fermeture du workspace).",
                parent=dialog,
            )
        nonlocal result
        result = (set(selection), push)
        dialog.destroy()

    def cancel() -> None:
        nonlocal result
        result = None
        dialog.destroy()

    dialog.bind("<Return>", lambda _event: save())
    dialog.bind("<Escape>", lambda _event: cancel())

    actions = tk.Frame(dialog, bg=APP_BACKGROUND)
    actions.pack(fill="x", padx=18, pady=(0, 14))
    for text, command in (
        ("Tout cocher (éligibles)", select_all),
        ("Tout décocher", clear_all),
        ("Enregistrer", save),
        ("Annuler", cancel),
    ):
        tk.Button(
            actions,
            text=text,
            font=FONT_PILL,
            bg=BORDER if command is not save else SURFACE_HOVER,
            fg=TEXT_PRIMARY,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            activebackground=SURFACE_HOVER,
            cursor="hand2",
            command=command,
        ).pack(side="right", padx=(6, 0))

    hint = tk.Label(
        dialog,
        text=(
            "En gris : pipeline non testable. Épinglez son déclencheur "
            "webhook/chat dans l'éditeur (☉ pinData) ou renseignez ses "
            "credentials via « Gérer les credentials CI… »."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
        wraplength=820,
    )
    hint.pack(fill="x", padx=18, pady=(0, 10))

    update_caption()
    _finish_dialog_setup(dialog, root)
    dialog.wait_window()
    return result


def prompt_ci_credentials(
    root: tk.Tk, manager: WorkspaceManager, workspace: Workspace
) -> None:
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
    _finish_dialog_setup(dialog, root)

    tk.Label(
        dialog,
        text=(
            "Cochez les credentials n8n à inclure dans les tests GitHub Actions. "
            "Leurs valeurs seront copiées en JSON dans le presse-papier."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
        wraplength=760,
    ).pack(fill="x", padx=18, pady=(14, 2))
    tk.Label(
        dialog,
        text=(
            "Créez le secret GitHub Actions « N8N_CI_CREDENTIALS » sur votre "
            "dépôt (Settings → Secrets and variables → Actions) puis collez-y "
            "ce JSON. Le launcher ne conserve que les noms — jamais les valeurs."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
        wraplength=760,
    ).pack(fill="x", padx=18, pady=(0, 8))

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
            {"name": name, "type": ctype}
            for variable, name, ctype in rows
            if variable.get()
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
            {"name": name, "type": ctype}
            for variable, name, ctype in rows
            if variable.get()
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
        tk.Button(
            actions,
            text=text,
            font=FONT_PILL,
            bg=BORDER,
            fg=TEXT_PRIMARY,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            activebackground=SURFACE_HOVER,
            cursor="hand2",
            command=command,
        ).pack(side="right", padx=(6, 0))

    dialog.wait_window()