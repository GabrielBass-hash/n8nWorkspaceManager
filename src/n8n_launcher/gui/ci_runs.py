"""Read-only tree of a workspace's GitHub Actions workflow runs.

``RunsPanel`` is a *pure renderer*: it holds no token, performs no network
call and spawns no thread. Everything it draws comes from a pre-assembled
:class:`~n8n_launcher.workspaces.ci_runs.RunsSnapshot` handed to
:meth:`RunsPanel.apply`, which the caller builds in a background worker from
already-fetched GitHub payloads (see the ``compose_*`` helpers in
:mod:`n8n_launcher.workspaces.ci_runs`). The panel only records the tree's
expansion state between refreshes and forwards the actions (open a run on
GitHub, refresh) to the callbacks the host wires up.

Asserts are kept off the main thread by construction: ``refresh`` just asks
the host to trigger a background fetch — the panel never blocks on I/O.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..workspaces import ci_runs
from .theme import (
    ACCENT,
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)

_MARK_SUCCESS = "✔"
_MARK_FAILURE = "✘"
_MARK_WAITING = "◻"
_MARK_SKIPPED = "–"

_TAG_SUCCESS = SURFACE
_TAG_FAILURE = "#3f1d1d"
_TAG_WAITING = "#3a2f0f"
_TAG_SKIPPED = "#1e293b"
_TAG_SELECTED = "#1e3056"


def _run_mark(run: ci_runs.RunSummary) -> str:
    """Leading glyph for a run row based on its conclusion."""
    concluded = run.conclusion or run.status
    if concluded == "success":
        return _MARK_SUCCESS
    if concluded in ("failure", "cancelled", "timed_out"):
        return _MARK_FAILURE
    if concluded in ("skipped",):
        return _MARK_SKIPPED
    return _MARK_WAITING


def _job_mark(job: ci_runs.JobSummary) -> str:
    """Leading glyph for a job row based on its outcome."""
    concluded = job.conclusion or job.status
    if concluded == "success":
        return _MARK_SUCCESS
    if concluded in ("failure", "timed_out", "action_required"):
        return _MARK_FAILURE
    if concluded in ("cancelled", "skipped"):
        return _MARK_SKIPPED
    return _MARK_WAITING


def _row_of(
    snapshot: ci_runs.RunsSnapshot,
) -> list[tuple[ci_runs.RunSummary, tuple[ci_runs.JobSummary, ...]]]:
    """Flatten ``(run, jobs)`` pairs in the snapshot's stable run order."""
    return [(run, snapshot.jobs.get(run.id, ())) for run in snapshot.runs]


def _run_badge(run: ci_runs.RunSummary) -> str:
    """Short header describing one run row."""
    return f"#{run.run_number} · {run.branch or run.head_sha[:8]}"


def runs_summary_text(snapshot: ci_runs.RunsSnapshot) -> str:
    """Short header describing the whole snapshot."""
    if snapshot.error:
        return f"GitHub Actions indisponible : {snapshot.error}"
    if not snapshot.runs:
        return "Aucun run GitHub Actions pour ce workspace."
    completed = sum(1 for run in snapshot.runs if run.conclusion)
    return f"{len(snapshot.runs)} run(s) · {completed} terminé(s)"


class RunsPanel(tk.Frame):
    """Read-only runs → jobs → pipelines tree for one workspace.

    The widget is a plain renderer: it is fed :meth:`apply` with a
    :class:`~n8n_launcher.workspaces.ci_runs.RunsSnapshot` and shows a
    three-level tree whose tag colours encode each outcome. It never fetches
    data itself: both the refresh button and the "open on GitHub" action
    delegate to the host callbacks, keeping I/O and threading in the caller.
    """

    instances: list["RunsPanel"] = []

    def __init__(
        self,
        parent,
        *,
        refresh: Callable[[], None],
        open_run: Callable[[ci_runs.RunSummary], None],
        run: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=APP_BACKGROUND)
        self.refresh_cb = refresh
        self.open_run_cb = open_run
        self.run_cb = run
        self._expanded: set[str] = set()
        self._active: set[str] = set()
        self._last: ci_runs.RunsSnapshot = ci_runs.RunsSnapshot("")
        RunsPanel.instances.append(self)

        header = tk.Frame(self, bg=APP_BACKGROUND)
        header.pack(fill="x", padx=14, pady=(10, 4))
        self._summary = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_META,
            anchor="w",
        )
        self._summary.pack(side="left", expand=True, fill="x")
        self._refresh_btn = ttk.Button(
            header, text="Actualiser", style="Secondary.TButton", command=self.refresh
        )
        self._refresh_btn.pack(side="right", padx=(6, 0))
        self._open_btn = ttk.Button(
            header,
            text="Ouvrir sur GitHub",
            style="Secondary.TButton",
            command=self._open_selected,
        )
        self._open_btn.pack(side="right", padx=(6, 0))
        # The "run" button only exists when the host can trigger a dispatch;
        # the panel stays a pure renderer and delegates the work to run_cb.
        if run is not None:
            self._run_btn = ttk.Button(
                header,
                text="Lancer la CI",
                style="Accent.TButton",
                command=self.run,
            )
            self._run_btn.pack(side="right", padx=(6, 0))

        self.tree = ttk.Treeview(
            self, columns=("detail",), show="tree headings", height=16
        )
        self.tree.heading("#0", text="Run / job / pipeline")
        self.tree.heading("detail", text="Détails")
        self.tree.column("#0", width=540, stretch=True)
        self.tree.column("detail", width=520, stretch=True, anchor="w")
        for tag, color in (
            ("success", _TAG_SUCCESS),
            ("failure", _TAG_FAILURE),
            ("waiting", _TAG_WAITING),
            ("skipped", _TAG_SKIPPED),
        ):
            self.tree.tag_configure(tag, background=color)
        self.tree.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())

        self._empty = tk.Label(
            self,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            anchor="w",
            justify="left",
            wraplength=900,
        )
        self._empty.pack(fill="x", padx=14, pady=(0, 10))

    # ------------------------------------------------------------------ API

    def layout_key(self, run: ci_runs.RunSummary) -> str:
        """Stable iid for a run row, also used to persist its expansion state."""
        return f"run-{run.id}"

    def apply(self, snapshot: ci_runs.RunsSnapshot) -> None:
        """Render *snapshot*, preserving the user's job-level expansion."""
        self._last = snapshot
        self._summary.config(text=runs_summary_text(snapshot))

        for item in self.tree.get_children():
            self.tree.delete(item)

        if snapshot.error or not snapshot.runs:
            if snapshot.error:
                self._empty.config(
                    text=f"GitHub Actions indisponible : {snapshot.error}\n"
                    "Vérifiez la connexion puis « Actualiser »."
                )
            else:
                self._empty.config(text="Aucun run GitHub Actions pour ce workspace.")
            return
        self._empty.config(text="")

        for run, jobs in _row_of(snapshot):
            run_iid = self.layout_key(run)
            conclusion = run.conclusion or run.status
            tag = {
                "success": "success",
                "failure": "failure",
                "cancelled": "failure",
                "timed_out": "failure",
            }.get(conclusion, "waiting")
            self.tree.insert(
                "",
                "end",
                iid=run_iid,
                text=f"{_run_mark(run)}  {_run_badge(run)}  ({run.human_status})",
                values=(conclusion,),
                tags=(tag,),
                open=run_iid in self._expanded,
            )
            for job in jobs:
                job_iid = f"job-{run.id}-{job.id}"
                job_tag = {
                    "success": "success",
                    "failure": "failure",
                    "timed_out": "failure",
                    "action_required": "failure",
                    "cancelled": "skipped",
                    "skipped": "skipped",
                }.get(job.conclusion or job.status, "waiting")
                self.tree.insert(
                    run_iid,
                    "end",
                    iid=job_iid,
                    text=f"    {_job_mark(job)}  {job.name}",
                    values=(job.human_status,),
                    tags=(job_tag,),
                    open=job_iid in self._expanded,
                )
                for pipeline in snapshot.pipelines.get(job.id, ()):
                    self.tree.insert(
                        job_iid,
                        "end",
                        text=f"        {pipeline.mark}  {pipeline.rel}",
                        values=(pipeline.detail or pipeline.status,),
                        tags=("muted",) if pipeline.status == "waiting" else (),
                    )
            self.tree.item(run_iid, open=run_iid in self._expanded)

    @property
    def expanded(self) -> set[str]:
        return set(self._expanded)

    def remember_expansion(self, iid: str, is_open: bool) -> None:
        """Record an open/closed state so a later :meth:`apply` keeps it."""
        if is_open:
            self._expanded.add(iid)
        else:
            self._expanded.discard(iid)

    def refresh(self) -> None:
        """Ask the host to fetch fresh data (the panel never fetches itself)."""
        self.refresh_cb()

    def run(self) -> None:
        """Ask the host to trigger a CI run (no-op when no host callback)."""
        if self.run_cb is not None:
            self.run_cb()

    def _open_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        for run, _jobs in _row_of(self._last):
            if iid == self.layout_key(run):
                self.open_run_cb(run)
                return
