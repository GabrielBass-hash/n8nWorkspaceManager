"""The CI panel: pipeline selection and run history, on the dashboard.

This is what :func:`n8n_launcher.gui.ci_edit.prompt_ci_workflows` used to be: a
pipeline tree the user ticks to decide what GitHub Actions runs, plus a
read-only view of the runs it produced. The two are stacked in one panel of the
launcher's dashboard (see :mod:`n8n_launcher.gui.board`), split by a sash, so
the workspace list, the selection and the journal are all on screen at once.

The panel is a :class:`~n8n_launcher.gui.pages.Page`, and it differs from a plain
frame in two ways that matter:

* it is *retargeted*. Selecting another workspace in the list reloads
  ``tests.json`` and the export files, so the panel follows the selection rather
  than showing the workspace it was opened for. Unsaved ticks are deliberately
  dropped on the way: the file on disk is the truth, and a silent mix of two
  workspaces' selections would be worse than losing the edits.
* it owns timers. The run history polls GitHub on its own cadence (5 s while a
  run is in flight, 30 s otherwise), and :meth:`CiPage.on_close` stops that loop —
  without it Tk would keep firing against destroyed widgets once the card is
  closed.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from ..core.models import Workspace
from ..workspaces import ci, ci_runs
from .board import Card
from .ci_runs import RunsPanel
from .layout import ColumnFitter, bind_wraplength, ellipsize, wrap_at
from .pages import PageSubject, log_page_event
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    TEXT_MUTED,
    TEXT_PRIMARY,
    text_measure,
)
from .tokens import (
    GUTTER,
    SPACE_2XL,
    SPACE_LG,
    SPACE_MD,
)

# The subject the journal filters on while this card is the focused one: the CI
# setup, its credentials and its runs. Every action taken here is journalled
# through ``log_page_event``, so those events are what this filter finds.
CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))

# Colours used for the tree tags (dark theme).
_COLOR_WARN = "#fcd34d"
_COLOR_DISABLED = TEXT_MUTED

# Pipeline selection tree: the label column holds the export file name and its
# indented node names, "Détails" the node count and the reason a pipeline is
# blocked. Both are sized to their content by the fitter, which fits them to the
# card the dock gives this page (see ``gui.layout``). The label column is the
# flexible one: pipeline names are prose, the node count is a counter.
_SELECTION_COLUMNS = ("detail",)
_SELECTION_HEADINGS = {"#0": "Pipeline", "detail": "Détails"}
_SELECTION_MINIMUMS = {"#0": 260, "detail": 180}
_SELECTION_MAXIMUMS = {"#0": 420, "detail": 520}

# The margin every child of the panel is packed with: the intros, the table and
# the counter share it, so the panel's request is the table's width plus twice
# this and nothing else.
_SELECTION_PADX = GUTTER

# What a bulk button costs around its label (padding, border and the theme's own
# margins), and the gap between two of them. Used to count how many fit on a
# line; the button is never cut, it moves to the next row instead.
_ACTION_BUTTON_CHROME = 34
_ACTION_GAP = SPACE_MD

# Per-pipeline state markers. Plain ASCII on purpose: the box glyphs ☐/☑
# (U+2610/U+2611) and the en dash fallback are absent from the Linux font
# families the theme resolves to (Noto Sans, Liberation Sans, Cantarell), and
# Tk/Xft does not fall back across fonts — they rendered blank on Linux.
_CHECKBOX = "[x]"
_CHECKBOX_EMPTY = "[ ]"
_BLOCKED = "-"

# Auto-refresh cadence of the runs card: quick while a run is in flight (that's
# the phase the user watches), much slower otherwise to spare the API.
NO_REMOTE_NOTE = (
    "Ce workspace n'a pas de dépôt GitHub : les exécutions de la CI ne peuvent pas y être lues."
)

RUNS_POLL_ACTIVE_MS = 5000
RUNS_POLL_IDLE_MS = 30000


class CiSelectionPanel(tk.Frame):
    """The tickable pipeline tree, with its counter and its two bulk buttons.

    A widget rather than a dialog body so the same tree serves the page and
    (until it is retired) the modal dialog. *on_save* receives the ticked paths
    when the user saves; the panel itself never writes ``tests.json`` — that
    stays with the host, so the git write runs off the main thread.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_save: Callable[[Workspace, set[str], bool], None] | None = None,
        on_push_prompt: Callable[[Workspace], bool] | None = None,
    ) -> None:
        """Build the tree (empty) and its widgets; call :meth:`load` to fill it."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self._on_save = on_save
        self._on_push_prompt = on_push_prompt
        self.workspace: Workspace | None = None
        self._exports: dict[str, dict[str, Any]] = {}
        self._eligible: dict[str, bool] = {}
        self._reasons: dict[str, str] = {}
        self._selection: set[str] = set()
        self._fitter: ColumnFitter | None = None
        self._tree: ttk.Treeview | None = None
        self._counter: tk.Label | None = None
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        """Build the tree, its counter and the bulk-selection buttons."""
        # Both intros are prose, so they wrap at the panel's width; the seed is
        # the table's *narrowest* width, which keeps their request under the
        # table's before the first fit and lets ``<Configure>`` take over after.
        narrowest = sum(_SELECTION_MINIMUMS.values())
        for text, colour, pady in (
            (
                "Sélectionnez les pipelines complètes à tester dans GitHub Actions",
                TEXT_PRIMARY,
                (14, 2),
            ),
            (
                "Un clic sur une case sélectionne la pipeline ; les nœuds sont "
                "affichés en lecture seule.",
                TEXT_MUTED,
                (0, 8),
            ),
        ):
            label = tk.Label(
                self,
                text=text,
                bg=APP_BACKGROUND,
                fg=colour,
                font=FONT_META,
                anchor="w",
                wraplength=narrowest,
            )
            wrap_at(label, narrowest, padding=2 * _SELECTION_PADX)
            bind_wraplength(label, minimum=200, padding=2 * _SELECTION_PADX)
            label.pack(fill="x", padx=_SELECTION_PADX, pady=pady)

        tree = ttk.Treeview(self, columns=_SELECTION_COLUMNS, show="tree headings", height=18)
        for name, heading in _SELECTION_HEADINGS.items():
            tree.heading(name, text=heading)
        for name in ("#0", *_SELECTION_COLUMNS):
            # Request the minimum up front: the pane then opens only as wide as
            # the pipeline names and their reason really need.
            tree.column(
                name,
                width=_SELECTION_MINIMUMS[name],
                minwidth=_SELECTION_MINIMUMS[name],
                stretch=False,
                anchor="w",
            )
        self._fitter = ColumnFitter(
            tree,
            columns=_SELECTION_COLUMNS,
            headings=_SELECTION_HEADINGS,
            minimums=_SELECTION_MINIMUMS,
            maximums=_SELECTION_MAXIMUMS,
            measure=text_measure(self, FONT_META),
            container=self,
            available=self._table_room,
            flexible="#0",
        )
        tree.configure(selectmode="none")
        for tag, color in (
            ("disabled", _COLOR_DISABLED),
            ("warn", _COLOR_WARN),
            ("muted", _COLOR_DISABLED),
        ):
            tree.tag_configure(tag, foreground=color)
        tree.pack(fill="y", anchor="nw", expand=True, padx=_SELECTION_PADX, pady=(0, SPACE_MD))
        tree.bind("<Button-1>", self._on_tree_click)
        self._tree = tree

        self._counter_text = ""
        self._counter = tk.Label(
            self,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_META,
            anchor="w",
        )
        self._counter.pack(fill="x", padx=_SELECTION_PADX, pady=(0, SPACE_MD))

        actions = tk.Frame(self, bg=APP_BACKGROUND)
        actions.pack(fill="x", padx=GUTTER, pady=(0, SPACE_2XL))
        # The three buttons reflow instead of overflowing: right-packed in a row
        # they are the one part of the panel that cannot shrink, so a narrow card
        # pushed them out of it. ``grid`` with a column count that follows the
        # room is the responsive form of the same three buttons.
        self._actions = actions
        self._action_buttons: list[ttk.Button] = []
        self._action_labels: list[str] = []
        self._action_columns = 0
        for text, command, style in (
            ("Tout cocher (éligibles)", self.select_all, "Secondary.TButton"),
            ("Tout décocher", self.clear_all, "Secondary.TButton"),
            ("Enregistrer", self.save, "Surface.TButton"),
        ):
            self._action_labels.append(text)
            self._action_buttons.append(
                ttk.Button(actions, text=text, style=style, cursor="hand2", command=command)
            )
        self._relayout_actions()
        actions.bind("<Configure>", lambda _event: self._relayout_actions())

        hint = tk.Label(
            self,
            text=(
                "En gris : pipeline non testable. Épinglez son déclencheur "
                "webhook/chat dans l'éditeur (pinData) ou renseignez ses "
                "credentials via « Gérer les credentials CI… »."
            ),
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        bind_wraplength(hint, minimum=200, padding=36)
        hint.pack(fill="x", padx=GUTTER, pady=(0, SPACE_LG))

    # -------------------------------------------------------------- responsive
    def _table_room(self) -> int:
        """Return the pixels the pipeline tree has, margins deducted.

        The panel is the card the dashboard gives this page, so its mapped width
        is the tree's budget: the dock decides how wide the card is and the
        columns are fitted to that. A panel Tk has not measured yet reports 1,
        which the fitter reads as "no budget yet" and leaves the tree on its
        pure content sizing.
        """
        with contextlib.suppress(Exception):
            return max(int(self.winfo_width()) - 2 * _SELECTION_PADX, 1)
        return 1

    def _action_columns_for(self, room: int) -> int:
        """Return how many of the three bulk buttons fit side by side in *room*.

        A button's own width is measured from its label plus the theme's chrome,
        so the count follows the card instead of a hard-coded assumption about
        how wide a button is. Three columns on a wide card, two then one as it
        narrows — which is the whole reflow, since the buttons themselves are
        never cut.
        """
        measure = text_measure(self, FONT_META)
        widest = (
            max((measure(text) for text in self._action_labels), default=0) + _ACTION_BUTTON_CHROME
        )
        for columns in (3, 2, 1):
            needed = columns * widest + (columns - 1) * _ACTION_GAP
            if needed <= room or columns == 1:
                return columns
        return 1

    def _relayout_actions(self) -> None:
        """Place the bulk buttons on the grid their room allows.

        Only a *change* of column count touches the buttons, so a resize that
        stays on the same layout costs one measurement and nothing else.
        """
        room = 0
        with contextlib.suppress(Exception):
            room = int(self._actions.winfo_width())
        if room <= 1:
            # Tk has not measured the bar yet: the first layout stands, and the
            # ``<Configure>`` that follows gets the real answer.
            return
        columns = self._action_columns_for(room)
        if columns == self._action_columns:
            return
        self._action_columns = columns
        with contextlib.suppress(Exception):
            for index, button in enumerate(self._action_buttons):
                button.grid(
                    row=index // columns,
                    column=index % columns,
                    padx=(0, _ACTION_GAP),
                    pady=(0, _ACTION_GAP),
                    sticky="ew",
                )
            for index in range(columns):
                self._actions.grid_columnconfigure(index, weight=1, uniform="actions")

    def table_widths(self) -> dict[str, int]:
        """Return the column widths the fitter last applied (for the host)."""
        return self._fitter.widths() if self._fitter is not None else {}

    def minimum_table_width(self) -> int:
        """Return the narrowest the tree can be, for the dock's breakpoints."""
        return self._fitter.minimum_total() if self._fitter is not None else 0

    # ------------------------------------------------------------------- load
    def load(self, workspace: Workspace) -> None:
        """Show *workspace*'s exports, its eligibility and its saved selection.

        The tree is rebuilt from scratch, which is what drops the ticks the user
        had not saved: the file on disk is the truth, and mixing two
        workspaces' selections in one tree would be a lie.
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

        self.workspace = workspace
        self._exports = exports
        self._eligible = eligible
        self._reasons = reasons
        self._selection = set(ci.read_selection(workspace.workflows_dir)) & set(exports)
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Insert every pipeline row and size the columns to their content."""
        tree = self._tree
        if tree is None:
            return
        existing = set(tree.get_children())
        for item in existing:
            tree.delete(item)
        for rel in sorted(self._exports):
            export = self._exports[rel]
            if self._eligible.get(rel):
                count = len(export.get("nodes") or [])
                detail = f"{count} nœud(s) · {ci.start_description(export)}"
                marker = self._marker(rel)
                tags: tuple[str, ...] = ()
            else:
                detail = self._reasons.get(rel, "")
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
        # Every row is inserted: size the columns to them before the page is
        # measured, so it opens as wide as the content really needs.
        if self._fitter is not None:
            self._fitter.rows()
        self._update_caption()

    # --------------------------------------------------------------- selection
    def selection(self) -> set[str]:
        """Return the ticked pipeline paths."""
        return set(self._selection)

    def _marker(self, rel: str) -> str:
        return _CHECKBOX if rel in self._selection else _CHECKBOX_EMPTY

    def _update_caption(self) -> None:
        """State the selection, cut to what the table is wide enough to show.

        The counter is a caption *under* the table, so it is bounded by the same
        rule as the journal's: a label wider than the table would widen the page
        that holds it, and the table would stop matching the page's outline.
        """
        if self._counter is None:
            return
        testable = sum(1 for ok in self._eligible.values() if ok)
        self._counter_text = (
            f"{len(self._selection)} pipeline(s) sélectionnée(s) sur {testable} testable(s)"
        )
        self._counter.config(text=self._caption_text())

    def _caption_text(self) -> str:
        """Return the counter as the table's width allows it to be shown."""
        if self._fitter is None:
            return self._counter_text
        return ellipsize(self._counter_text, self._fitter.total(), text_measure(self, FONT_META))

    def _update_row(self, rel: str) -> None:
        if self._eligible.get(rel) and self._tree is not None:
            self._tree.item(rel, text=f"{self._marker(rel)}  {rel}")

    def toggle(self, rel: str) -> None:
        """Tick or untick *rel*, unless it is not testable."""
        if not self._eligible.get(rel):
            return
        if rel in self._selection:
            self._selection.discard(rel)
        else:
            self._selection.add(rel)
        self._update_row(rel)
        self._update_caption()

    def select_all(self) -> None:
        """Tick every testable pipeline."""
        for rel, ok in self._eligible.items():
            if ok:
                self._selection.add(rel)
                self._update_row(rel)
        self._update_caption()

    def clear_all(self) -> None:
        """Untick everything, testable or not."""
        self._selection.clear()
        for rel in self._eligible:
            self._update_row(rel)
        self._update_caption()

    def save(self) -> None:
        """Ask whether to push, then hand the selection to the host.

        The workspace travels with the selection: the panel follows the list
        selection, so the host cannot assume the one it was opened for. Saving
        before :meth:`load` is refused rather than attributed to nobody's
        workspace.
        """
        workspace = self._require_workspace()
        push = self._ask_push()
        log_page_event("enregistrée", CI_SUBJECT, workspace)
        if self._on_save is not None:
            self._on_save(workspace, self.selection(), push)

    def _ask_push(self) -> bool:
        """Ask whether to push the saved selection.

        The host may own the question (``on_push_prompt``); without one the
        panel asks it itself, as the dialog used to. It is asked even for an
        empty selection: "tout décocher" must not stay local, or GitHub keeps
        running the previous selection on the next push.
        """
        if self._on_push_prompt is not None:
            return self._on_push_prompt(self._require_workspace())
        return bool(
            messagebox.askyesno(
                "Pousser maintenant ?",
                "Pousser ces changements vers GitHub maintenant ?\n"
                "Sinon ils seront inclus au prochain poussage (fermeture du workspace).",
                parent=self.winfo_toplevel(),
            )
        )

    def _require_workspace(self) -> Workspace:
        """Return the loaded workspace, refusing to act on an unloaded panel."""
        if self.workspace is None:
            raise RuntimeError("the CI selection panel has no workspace loaded")
        return self.workspace

    def _on_tree_click(self, event: tk.Event) -> None:
        """Toggle the pipeline whose row was clicked, ignoring the expander."""
        tree = self._tree
        if tree is None:
            return
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
            self.toggle(item)

    def testable_count(self) -> int:
        """Return how many pipelines are testable (the caption's denominator)."""
        return sum(1 for ok in self._eligible.values() if ok)


class CiPage(tk.Frame):
    """The CI panel: the selection tree and the runs history, stacked.

    The two views used to be two tabs of a notebook nested in the page; they are
    now two cards of one dashboard panel, split by a sash, so the user sees what
    is ticked *and* what it produced at the same time. That changes what
    "visible" means for a page, so the run history is driven by **focus** (see
    :class:`~n8n_launcher.gui.board.PageDock`): the focused card polls GitHub, the
    other one does not spend the API budget.

    The runs view is fed by the host through four callbacks, all of which take
    the workspace the page is currently showing (a page is retargeted, so they
    cannot be bound to one workspace at construction time):

    ``runs_source``
        returns the latest cached snapshot, synchronously and without I/O — the
        page must never block on the network;
    ``runs_refresh``
        kicks off a background fetch of that workspace's runs and applies the
        result to the panel on the main thread;
    ``runs_open``
        opens a run's GitHub page in the browser;
    ``runs_run``
        dispatches a new run; ``None`` hides the "Lancer la CI" button, which is
        what a workspace without a GitHub remote gets.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_save: Callable[[Workspace, set[str], bool], None],
        on_push_prompt: Callable[[Workspace], bool] | None = None,
        runs_source: Callable[[Workspace], ci_runs.RunsSnapshot] | None = None,
        runs_refresh: Callable[[Workspace, RunsPanel], None] | None = None,
        runs_open: Callable[[ci_runs.RunSummary], None] | None = None,
        runs_run: Callable[[Workspace, RunsPanel], None] | None = None,
        runs_available: Callable[[Workspace], bool] | None = None,
    ) -> None:
        """Build the two cards; call :meth:`retarget` to fill them."""
        super().__init__(parent, bg=APP_BACKGROUND)
        self.subject = CI_SUBJECT
        self.workspace: Workspace | None = None
        # True while this card is the focused one: a card the user is not
        # looking at must not spend the GitHub API budget on polling.
        self._active = False
        self._runs_source = runs_source
        self._runs_refresh = runs_refresh
        # Without a GitHub remote there is nothing to read and nothing to poll:
        # the card still exists (it never appears and disappears under the user)
        # and shows the host's note instead of a failure.
        self._runs_available = runs_available
        self._after_id: str | None = None
        self._closed = False

        header = tk.Frame(self, bg=APP_BACKGROUND)
        header.pack(fill="x", padx=GUTTER, pady=(SPACE_2XL, 0))
        self._title = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_META,
            anchor="w",
        )
        self._title.pack(side="left", fill="x", expand=True)

        # Two cards, one above the other, split by a draggable sash — and no tab
        # bar. The selection tree and the run history used to be two tabs of a
        # nested notebook, so only one of them was ever on screen and switching
        # cost a click; as a dashboard they are both visible and the sash is the
        # user deciding how much of each it wants.
        self._split = ttk.PanedWindow(self, orient="vertical")
        self._split.pack(fill="both", expand=True, padx=GUTTER, pady=(SPACE_MD, SPACE_2XL))
        selection_card = Card(self._split, title="Sélection")
        self._split.add(selection_card, weight=3)
        runs_card = Card(self._split, title="Déroulement")
        self._split.add(runs_card, weight=2)

        self.selection = CiSelectionPanel(
            selection_card.body(),
            on_save=on_save,
            on_push_prompt=on_push_prompt,
        )
        self.selection.pack(fill="both", expand=True)

        self.runs_panel: RunsPanel | None = None
        if runs_source is not None:
            # A placeholder refresh is enough at construction time: the real
            # callback is bound right after, and the panel cannot be clicked
            # before the page is on screen.
            self.runs_panel = RunsPanel(
                runs_card.body(),
                refresh=lambda: None,
                open_run=lambda _run: None,
                run=(lambda: None) if runs_run is not None else None,
            )
            self.runs_panel.pack(fill="both", expand=True)
            if runs_open is not None:
                self.runs_panel.open_run_cb = runs_open
            if runs_run is not None:
                panel = self.runs_panel
                panel.run_cb = lambda: runs_run(self._require_workspace(), panel)
        else:
            # Nothing to show in the lower card without a runs source: the sash
            # would divide the page between a tree and an empty frame.
            self._split.forget(runs_card)

    # ------------------------------------------------------------------- page
    def retarget(self, workspace: Workspace) -> None:
        """Follow the list selection to *workspace*.

        Both cards reload: the tree re-reads ``tests.json`` and the export files,
        and the runs panel is served from the cache of that workspace before any
        fetch is started.
        """
        self.workspace = workspace
        self._title.config(text=f"Tests GitHub Actions — workspace « {workspace.name} »")
        self.selection.load(workspace)
        if self.runs_panel is not None and not self._runs_readable(workspace):
            # No GitHub remote: the card stays, but it shows the note and stops
            # polling rather than asking for runs that can never be there.
            self._cancel_poll()
            self.runs_panel.apply(ci_runs.RunsSnapshot(repo_path="", note=NO_REMOTE_NOTE))
            return
        if self.runs_panel is not None:
            source = self._runs_source
            if source is not None:
                self.runs_panel.apply(source(workspace))
            if self._active:
                # Focused card: the workspace just changed under the user, so the
                # cache shown is stale and a fetch is worth its call.
                self._refresh_runs()

    def on_show(self) -> None:
        """Refresh the runs when this card becomes the focused one."""
        self._active = True
        if self._closed or self.runs_panel is None or self.workspace is None:
            return
        if not self._runs_readable(self.workspace):
            return
        self._refresh_runs()

    def on_hide(self) -> None:
        """Stop polling: nothing the user can see is changing any more."""
        self._active = False
        self._cancel_poll()

    def on_close(self) -> None:
        """Stop the runs poll before the card goes away.

        Without this Tk would keep firing the tick against destroyed widgets
        after the card is closed — the same reason the dialog relied on its
        ``winfo_exists`` guard.
        """
        self._closed = True
        self._active = False
        self._cancel_poll()

    # ----------------------------------------------------------------- saving
    # ------------------------------------------------------------------- runs
    def _require_workspace(self) -> Workspace:
        """Return the workspace shown, refusing to act on no workspace."""
        if self.workspace is None:
            raise RuntimeError("the CI page has no workspace yet")
        return self.workspace

    def _runs_readable(self, workspace: Workspace) -> bool:
        """Return whether *workspace* can have runs at all (GitHub remote)."""
        if self._runs_available is None:
            return True
        try:
            return bool(self._runs_available(workspace))
        except Exception:
            # An unreadable remote is not a reason to crash a card: the poll is
            # simply skipped, and the next retarget decides again.
            return False

    def _refresh_runs(self) -> None:
        """Ask the host to re-fetch the runs, then reschedule the poll."""
        workspace = self.workspace
        if workspace is None or self._closed or self._runs_refresh is None:
            return
        if not self._runs_readable(workspace):
            return
        panel = self.runs_panel
        if panel is None:
            return
        self._runs_refresh(workspace, panel)
        self._schedule_poll(workspace)

    def _schedule_poll(self, workspace: Workspace) -> None:
        """Re-arm the runs poll, fast while a run is in flight."""
        panel = self.runs_panel
        if panel is None or self._closed or not self._active:
            return
        if not self._runs_readable(workspace):
            return
        self._cancel_poll()
        cadence = RUNS_POLL_ACTIVE_MS if panel.has_active_run else RUNS_POLL_IDLE_MS
        with contextlib.suppress(Exception):
            self._after_id = self.after(cadence, lambda: self._tick(workspace))

    def _cancel_poll(self) -> None:
        """Drop the pending runs poll, if any."""
        after_id = self._after_id
        self._after_id = None
        if after_id is None:
            return
        with contextlib.suppress(Exception):
            self.after_cancel(after_id)

    def _tick(self, workspace: Workspace) -> None:
        """One poll tick; a hidden or retargeted page drops the tick it no longer owns."""
        if self._closed or not self._active or self.workspace is not workspace:
            return
        self._after_id = None
        self._refresh_runs()


__all__ = ["CI_SUBJECT", "NO_REMOTE_NOTE", "CiPage", "CiSelectionPanel"]
