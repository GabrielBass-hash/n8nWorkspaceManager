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
from ..gui_utils.text import ellipsize
from ..workspaces import ci
from ..workspaces.manager import WorkspaceManager
from .dialog import Dialog
from .layout import ColumnFitter, bind_wraplength
from .theme import (
    APP_BACKGROUND,
    FONT_META,
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

# The credentials table. ``#0`` carries the tick marker and the name, because the
# marker and the thing it marks are one value to read — a dedicated marker column
# would put a checkbox on one line and its name on the next.
_CREDENTIALS_COLUMNS = ("type",)
_CREDENTIALS_HEADINGS = {"#0": "Credential", "type": "Type"}
_CREDENTIALS_MINIMUMS = {"#0": 240, "type": 140}
_CREDENTIALS_MAXIMUMS = {"#0": 460, "type": 320}
# A dialog's own horizontal padding is the gutter it was packed with; the fitter
# is told about it so the table keeps that margin instead of eating it.
_CREDENTIALS_PADX = GUTTER
# Rows shown at once. Enough to scroll by eye, and short enough that a workspace
# with a dozen credentials does not push the actions out of a small dialog.
_CREDENTIALS_HEIGHT = 10

# Tick markers. Plain ASCII on purpose, and for the same reason as the pipeline
# selection tree: the box glyphs ☐/☑ (U+2610/U+2611) are absent from the Linux
# font families the theme resolves to (Noto Sans, Liberation Sans, Cantarell),
# and Tk/Xft does not fall back across fonts — they rendered blank on Linux.
_CHECKBOX = "[x]"
_CHECKBOX_EMPTY = "[ ]"


def _table_room(dialog: tk.Misc) -> int:
    """Return the pixels a dialog's table has, its own padding deducted.

    The dialog is the room, not the screen: the user resized *this* window, and
    the table has to follow it. A dialog Tk has not measured yet reports 1, which
    the fitter reads as "no budget yet" and leaves the columns on their pure
    content sizing.
    """
    with contextlib.suppress(Exception):
        return max(int(dialog.winfo_width()) - 2 * _CREDENTIALS_PADX, 1)
    return 1


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
    dialog: Dialog[str] = Dialog(root, "Lancer la CI", primary="Lancer")

    tk.Label(
        dialog,
        text=f"Workflow « {workflow_file} » — branche à exécuter :",
        bg=APP_BACKGROUND,
        fg=TEXT_PRIMARY,
        font=FONT_META,
        anchor="w",
    ).pack(fill="x", padx=GUTTER, pady=(SPACE_3XL, SPACE_HAIRLINE))
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
    ref_entry.pack(fill="x", padx=GUTTER, pady=(0, SPACE_SM))
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
    intro.pack(fill="x", padx=GUTTER, pady=(0, SPACE_XL))
    bind_wraplength(intro, minimum=200, padding=2 * GUTTER)

    def submit(_event: tk.Event | None = None) -> None:
        # An empty ref is not an error, it is a cancellation: dispatching the
        # default branch is already what the prefilled value does.
        if ref_var.get().strip():
            dialog.settle(ref_var.get().strip())
        else:
            dialog.cancel()

    dialog.on_submit = submit
    ref_entry.bind("<Return>", submit)
    return dialog.wait(focus=ref_entry)


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

    # All three commands are peers here: "J'ai collé" records the metadata but is
    # not more committing than "Annuler", so the bar does not promote it to the
    # accent, and it keeps the tighter gap this dialog always used.
    dialog: Dialog[None] = Dialog(
        root,
        "Credentials CI",
        primary="J'ai collé",
        primary_style="Secondary.TButton",
        resizable=True,
        bar_pady=(SPACE_LG, SPACE_2XL),
        action_gap=SPACE_SM,
    )

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
    intro.pack(fill="x", padx=GUTTER, pady=(SPACE_2XL, SPACE_HAIRLINE))
    bind_wraplength(intro, minimum=200, padding=2 * GUTTER)
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
    secret_hint.pack(fill="x", padx=GUTTER, pady=(0, SPACE_MD))
    bind_wraplength(secret_hint, minimum=200, padding=2 * GUTTER)

    # A table, not a stack of checkbuttons: the same widget the CI page already
    # uses for the pipeline selection, fitted to its own content, so the dialog
    # opens as wide as the longest credential name and never shows a column the
    # user cannot read. There is no scrollbar — a ttk.Treeview scrolls on its own,
    # and a native one would be the only scrollbar in the app.
    tree = ttk.Treeview(
        dialog,
        columns=_CREDENTIALS_COLUMNS,
        show="tree headings",
        height=_CREDENTIALS_HEIGHT,
        selectmode="none",
    )
    for name, heading in _CREDENTIALS_HEADINGS.items():
        tree.heading(name, text=heading)
    for name in ("#0", *_CREDENTIALS_COLUMNS):
        # Request the minimum up front, exactly as the selection tree does: a
        # declared guess is what a table is never laid out by.
        tree.column(
            name,
            width=_CREDENTIALS_MINIMUMS[name],
            minwidth=_CREDENTIALS_MINIMUMS[name],
            stretch=False,
            anchor="w",
        )
    tree.pack(fill="both", expand=True, padx=_CREDENTIALS_PADX, pady=(0, SPACE_MD))

    # What the row shows is ``[x] Name``; what the dialog hands to the manager is
    # the ``(name, type)`` pair. One entry per line, in the order n8n returned
    # them once sorted, so the two never drift apart.
    entries: list[tuple[str, str, str]] = []
    ticked: dict[str, bool] = {}
    for item in sorted(listed, key=lambda credential: str(credential.get("name") or "")):
        name = str(item.get("name") or "").strip()
        ctype = str(item.get("type") or "").strip()
        if not name or not ctype:
            # A credential with no name or no type cannot be recreated by the
            # generated runner, so it is not offered at all.
            continue
        # The id is the name itself: n8n credential names are unique within a
        # project, and an index-based id would mean a second thing to renumber.
        entries.append((name, name, ctype))
        ticked[name] = True
    fitter = ColumnFitter(
        tree,
        columns=_CREDENTIALS_COLUMNS,
        headings=_CREDENTIALS_HEADINGS,
        minimums=_CREDENTIALS_MINIMUMS,
        maximums=_CREDENTIALS_MAXIMUMS,
        measure=text_measure(dialog, FONT_META),
        container=dialog,
        available=lambda: _table_room(dialog),
        flexible="#0",
    )
    fitter.rows()

    captioned_at = -1

    def render() -> None:
        """Draw the table from the current ticks, one row per credential."""
        for iid in tree.get_children():
            tree.delete(iid)
        for item_id, name, ctype in entries:
            marker = _CHECKBOX if ticked.get(name) else _CHECKBOX_EMPTY
            tree.insert("", "end", iid=item_id, text=f"{marker}  {name}", values=(ctype,))
        fitter.rows()
        # The tree is the widest thing in the dialog, so the captions are bounded
        # by it: a prose label asking for more than the table would widen the
        # dialog past what the table can fill. Their width is the table's, so this
        # only has to happen when the table's own total moved.
        nonlocal captioned_at
        total = fitter.total() or sum(_CREDENTIALS_MINIMUMS.values())
        if total == captioned_at:
            return
        captioned_at = total
        measure = text_measure(dialog, FONT_META)
        intro.config(text=ellipsize(str(intro.cget("text")), total, measure))
        for caption in (intro, secret_hint):
            bind_wraplength(caption, minimum=200, padding=2 * _CREDENTIALS_PADX)

    def selected() -> list[dict[str, str]]:
        """The ticked credentials, as the name/type pairs the manager records.

        Deduped by name, last occurrence winning: a workspace whose n8n returned
        the same credential twice would otherwise hand the runner two entries for
        one secret.
        """
        chosen: dict[str, dict[str, str]] = {}
        for name, label, ctype in entries:
            if ticked.get(name):
                chosen[name] = {"name": label, "type": ctype}
        return list(chosen.values())

    def toggle(item: str) -> None:
        """Flip the credential of one row, and redraw it."""
        for name, _label, _ctype in entries:
            if name == item:
                ticked[name] = not ticked.get(name)
                break
        render()

    def set_all(value: bool) -> None:
        """Tick or untick every credential at once."""
        for name, _label, _ctype in entries:
            ticked[name] = value
        render()

    def on_tree_click(event: tk.Event) -> None:
        """Toggle the credential whose row was clicked, and only that row."""
        try:
            if tree.identify("region", event.x, event.y) not in ("cell", "tree"):
                return
            item = tree.identify_row(event.y)
        except Exception:
            return
        if item:
            toggle(item)

    tree.bind("<Button-1>", on_tree_click)

    status = tk.Label(
        dialog,
        text="",
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
    )
    status.pack(fill="x", padx=GUTTER, pady=(SPACE_TIGHT, 0))

    render()

    def copy_json() -> None:
        picked = selected()
        if not picked:
            messagebox.showwarning(
                "n8n Launcher",
                "Sélectionnez au moins une credential.",
                parent=dialog,
            )
            return
        try:
            payload = manager.ci_credentials_payload(workspace, picked)
        except Exception as exc:
            messagebox.showerror("n8n Launcher", str(exc), parent=dialog)
            return
        _copy_text(dialog, payload)
        status.config(
            text="JSON copié dans le presse-papier — collez-le dans le secret "
            f"« {ci.SECRET_NAME} » puis cliquez « J'ai collé »."
        )

    def confirm_pasted() -> None:
        try:
            manager.set_ci_credentials(workspace, selected())
        except Exception as exc:
            messagebox.showerror(
                "n8n Launcher",
                f"Impossible d'enregistrer les credentials CI : {exc}",
                parent=dialog,
            )
            return
        # No result to hand back: what this dialog commits is the manager call.
        dialog.settle(None)

    dialog.add_action("Tout cocher", lambda: set_all(True))
    dialog.add_action("Tout décocher", lambda: set_all(False))
    dialog.add_action("Copier le JSON", copy_json)
    dialog.on_submit = confirm_pasted
    # Every child is in: the dialog opens as wide as this prose really needs.
    dialog.wait()
