"""Modal dialogs for the GitHub Actions CI configuration.

Two dialogs live here:

* :func:`prompt_ci_workflows` — an expandable pipeline tree (collapsed by
  default, with per-pipeline counters) letting the user check the *complete*
  pipelines to run in CI. Nodes under each pipeline are shown read-only
  (type, pinned badge); ineligible pipelines are greyed out with an explicit
  reason and cannot be checked. When a ``runs_source`` is provided the dialog
  gains a ``ttk.Notebook`` with two tabs: the same "Sélection" tree and a
  read-only "Déroulement" tab hosting a :class:`RunsPanel` fed by the host.
* :func:`prompt_ci_credentials` — a checkable list of the workspace's n8n
  credentials whose values get copied to the clipboard as JSON, to be pasted
  once as the ``N8N_CI_CREDENTIALS`` GitHub Actions secret. Only the name/type
  metadata is recorded locally afterwards.

Neither dialog reaches the network itself except through the workspace
manager (credentials flow) and the runs tab's host callbacks; eligibility is
computed purely from the export files on disk. The "Déroulement" tab has no
manual refresh button: while the dialog lives, :func:`_schedule_runs_poll`
re-fetches on a timer (5 s while a run is in flight, 30 s otherwise) through
the host's ``runs_refresh`` callback.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from ..core.models import Workspace
from ..workspaces import ci, ci_runs
from ..workspaces.manager import WorkspaceManager
from .ci_runs import RunsPanel
from .layout import ColumnFitter, bind_wraplength
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    configure_fonts,
    text_measure,
)

# Colours used for the tree tags (dark theme).
_COLOR_WARN = "#fcd34d"
_COLOR_DISABLED = TEXT_MUTED

# Pipeline selection tree: the label column holds the export file name and its
# indented node names, "Détails" the node count and the reason a pipeline is
# blocked. Both are sized to their content by the fitter (see ``gui.layout``),
# so a short reason stops reserving the room of a long export name.
_SELECTION_COLUMNS = ("detail",)
_SELECTION_HEADINGS = {"#0": "Pipeline", "detail": "Détails"}
_SELECTION_MINIMUMS = {"#0": 260, "detail": 180}
_SELECTION_MAXIMUMS = {"#0": 480, "detail": 3000}

# Per-pipeline state markers. Plain ASCII on purpose: the box glyphs ☑/☐
# (U+2610/U+2611) and the en dash fallback are absent from the Linux font
# families the theme resolves to (Noto Sans, Liberation Sans, Cantarell), and
# Tk/Xft does not fall back across fonts — they rendered blank on Linux.
_CHECKBOX = "[x]"
_CHECKBOX_EMPTY = "[ ]"
_BLOCKED = "-"

# Auto-refresh cadence of the runs tab: quick while a run is in flight (that's
# the phase the user watches), much slower otherwise to spare the API.
RUNS_POLL_ACTIVE_MS = 5000
RUNS_POLL_IDLE_MS = 30000


def _finish_dialog_setup(dialog: tk.Toplevel, root: tk.Tk) -> None:
    """Center a modal dialog over its parent and make it modal (best-effort)."""
    # Same guarantee as gui/dialogs: a dialog can own its interpreter's first
    # font registration, so make the named UI fonts resolvable on that root.
    with contextlib.suppress(Exception):
        configure_fonts(root)
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


def _schedule_runs_poll(
    dialog: tk.Toplevel,
    panel: RunsPanel,
    runs_refresh: Callable[[RunsPanel], None],
) -> None:
    """Poll the runs panel on a timer while the dialog is open.

    There is no manual refresh button: this replaces it. Each tick delegates
    the actual fetch to the host (which runs it on a background thread) through
    ``runs_refresh``; the cadence adapts to the last snapshot — 5 s while a
    run is in flight, 30 s otherwise — so the GitHub API is not hammered when
    nothing is running. The loop self-terminates once the dialog is destroyed:
    the fake Toplevel drops pending timers on ``destroy``, while the real Tk
    path hits the ``winfo_exists`` TclError guard on the next stale tick.
    """

    def tick() -> None:
        try:
            if not dialog.winfo_exists():
                return
            runs_refresh(panel)
            cadence = RUNS_POLL_ACTIVE_MS if panel.has_active_run else RUNS_POLL_IDLE_MS
            dialog.after(cadence, tick)
        except Exception:
            return

    with contextlib.suppress(Exception):
        dialog.after(RUNS_POLL_ACTIVE_MS, tick)


def prompt_ci_workflows(
    root: tk.Tk,
    workspace: Workspace,
    *,
    runs_source: Callable[[], ci_runs.RunsSnapshot] | None = None,
    runs_refresh: Callable[[RunsPanel], None] | None = None,
    runs_open: Callable[[ci_runs.RunSummary], None] | None = None,
    runs_run: Callable[[RunsPanel], None] | None = None,
) -> tuple[set[str], bool] | None:
    """Let the user pick the pipelines to run in GitHub Actions.

    Returns ``(selected_paths, push_now)`` on save, or ``None`` when
    cancelled. The selection is *not* persisted here — the caller writes it
    through the workspace manager so git operations stay off the main thread.

    When ``runs_source`` is provided, the dialog embeds a two-tab
    ``ttk.Notebook``: "Sélection" holds the usual pipeline tree and
    "Déroulement" a read-only :class:`RunsPanel` rendered from the host's
    cached snapshot. ``runs_source()`` must be a synchronous, I/O-free callable
    returning the latest snapshot; ``runs_refresh`` is invoked once on open and
    then on a timer (:func:`_schedule_runs_poll`) so the host can re-fetch in a
    background thread and call :meth:`RunsPanel.apply` on the main thread —
    there is no manual refresh button. ``runs_open`` is called with a run when
    the user double-clicks its row. ``runs_run``, when provided, adds the
    panel's "Lancer la CI" button and is called with the panel on click so the
    host can dispatch a run off-thread. Omitting ``runs_source`` keeps the
    exact single-pane layout, which is what older callers still get.
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

    # The single-pane dialog packs everything onto the Toplevel; the runs-aware
    # variant nests the same widgets under a Notebook's "Sélection" tab so the
    # pipeline tree's code path is shared verbatim between the two layouts.
    body: tk.Misc = dialog
    runs_tab: tk.Frame | None = None
    if runs_source is not None:
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        selection_tab = tk.Frame(notebook, bg=APP_BACKGROUND)
        notebook.add(selection_tab, text="Sélection")
        runs_tab = tk.Frame(notebook, bg=APP_BACKGROUND)
        notebook.add(runs_tab, text="Déroulement")
        body = selection_tab

    tree = ttk.Treeview(body, columns=("detail",), show="tree headings", height=18)
    for name, heading in _SELECTION_HEADINGS.items():
        tree.heading(name, text=heading)
    for name in ("#0", "detail"):
        # Request the minimum up front: the dialog then opens only as wide as
        # the pipeline names and their reason really need.
        tree.column(
            name,
            width=_SELECTION_MINIMUMS[name],
            minwidth=_SELECTION_MINIMUMS[name],
            stretch=name == "#0",
            anchor="w",
        )
    fitter = ColumnFitter(
        tree,
        columns=("detail",),
        headings=_SELECTION_HEADINGS,
        minimums=_SELECTION_MINIMUMS,
        maximums=_SELECTION_MAXIMUMS,
        flexible="#0",
        measure=text_measure(dialog, FONT_META),
    )
    tree.configure(selectmode="none")
    for tag, color in (
        ("disabled", _COLOR_DISABLED),
        ("warn", _COLOR_WARN),
        ("muted", _COLOR_DISABLED),
    ):
        tree.tag_configure(tag, foreground=color)
    tree.pack(fill="both", expand=True, padx=18, pady=(0, 8))

    counter_label = tk.Label(
        body,
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

    # Every row is inserted: size the columns to them before the dialog is
    # measured, so it opens as wide as the content really needs.
    fitter.rows()

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

    def on_tree_click(event: tk.Event) -> None:
        # Only toggle when clicking the item area itself (not scrollbar gaps).
        try:
            if tree.identify("region", event.x, event.y) not in ("cell", "tree"):
                return
            # The expander triangle lives in the same "tree" region as the row:
            # clicking it must only expand/collapse, never flip the checkbox.
            if tree.identify_element(event.x, event.y) == "Treeitem.indicator":
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
        # Demander le push même pour une sélection vide : sans cela, « Tout
        # décocher » restait local et GitHub continuait d'exécuter l'ancienne
        # sélection au prochain poussage.
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

    actions = tk.Frame(body, bg=APP_BACKGROUND)
    actions.pack(fill="x", padx=18, pady=(0, 14))
    for text, command in (
        ("Tout cocher (éligibles)", select_all),
        ("Tout décocher", clear_all),
        ("Enregistrer", save),
        ("Annuler", cancel),
    ):
        ttk.Button(
            actions,
            text=text,
            style="Surface.TButton" if command is save else "Secondary.TButton",
            cursor="hand2",
            command=command,
        ).pack(side="right", padx=(6, 0))

    hint = tk.Label(
        body,
        text=(
            "En gris : pipeline non testable. Épinglez son déclencheur "
            "webhook/chat dans l'éditeur (pinData) ou renseignez ses "
            "credentials via « Gérer les credentials CI… »."
        ),
        bg=APP_BACKGROUND,
        fg=TEXT_MUTED,
        font=FONT_META,
        anchor="w",
        wraplength=640,
    )
    bind_wraplength(hint, minimum=200, padding=36)
    hint.pack(fill="x", padx=18, pady=(0, 10))

    # Runs view: a pure renderer wired to the host's closure. The panel is fed
    # the cached snapshot synchronously (no I/O) then asked to refresh so the
    # very first open already triggers a background fetch.
    if runs_tab is not None and runs_source is not None:
        panel = RunsPanel(
            runs_tab,
            refresh=lambda: None,
            open_run=lambda _run: None,
            # A placeholder is enough at construction time: the real callback
            # (bound to the panel) is set right after, and the button cannot be
            # clicked before the dialog is shown.
            run=(lambda: None) if runs_run is not None else None,
        )
        panel.pack(fill="both", expand=True)
        panel.apply(runs_source())
        if runs_refresh is not None:
            panel.refresh_cb = lambda: runs_refresh(panel)
            runs_refresh(panel)
            # No manual refresh button: keep the tab in sync on a timer while
            # the dialog is open (fast while a run is in flight, slow otherwise).
            _schedule_runs_poll(dialog, panel, runs_refresh)
        if runs_open is not None:
            panel.open_run_cb = runs_open
        if runs_run is not None:
            panel.run_cb = lambda: runs_run(panel)

    update_caption()
    _finish_dialog_setup(dialog, root)
    dialog.wait_window()
    return result


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
    tk.Label(
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
        wraplength=360,
    ).pack(fill="x", padx=18, pady=(0, 12))

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
            "dépôt (Settings -> Secrets and variables -> Actions) puis collez-y "
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

    dialog.wait_window()
