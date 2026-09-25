"""Read-only tree of a workspace's GitHub Actions workflow runs.

``RunsPanel`` is a *pure renderer*: it holds no token, performs no network
call and spawns no thread. Everything it draws comes from a pre-assembled
:class:`~n8n_launcher.workspaces.ci_runs.RunsSnapshot` handed to
:meth:`RunsPanel.apply`, which the caller builds in a background worker from
already-fetched GitHub payloads (see the ``compose_*`` helpers in
:mod:`n8n_launcher.workspaces.ci_runs`). The panel only records the tree's
expansion state between refreshes and forwards the actions (open a run on
GitHub, launch CI) to the callbacks the host wires up.

There is no manual refresh button: while the runs tab is open, the host
re-polls on a timer (see ``gui/ci_edit.py``) and applies fresh snapshots. The
panel highlights whatever sits at the top of the tree — a run still in flight
is auto-expanded with its live steps and pipeline progress, the "Lancer la CI"
button is disabled while a run is running, and finished runs stay collapsed
single rows unless the user expands them.

Asserts are kept off the main thread by construction: ``refresh`` just asks
the host to trigger a background fetch — the panel never blocks on I/O.
"""

from __future__ import annotations

import contextlib
import tkinter as tk
import weakref
from collections.abc import Callable
from datetime import datetime
from tkinter import ttk
from typing import ClassVar

from ..workspaces import ci_runs
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    SURFACE,
    TEXT_MUTED,
)

# Leading marks for the runs panel. Plain ASCII on purpose: the glyph sets
# (check marks, ballot X, medium square, en dash; U+2714/U+2718/U+25FB/U+2013)
# are missing from the Linux font families the theme resolves to, and Tk/Xft
# renders blank boxes for them, so the panel was unreadable on Linux while
# fine on Windows/macOS.
_MARK_SUCCESS = "+"
_MARK_FAILURE = "!"
_MARK_WAITING = "*"
_MARK_SKIPPED = "-"

_TAG_SUCCESS = SURFACE
_TAG_FAILURE = "#3f1d1d"
_TAG_WAITING = "#3a2f0f"
_TAG_SKIPPED = "#1e293b"
_TAG_SELECTED = "#1e3056"

# Human labels for the live *step* rows shown under an in-progress job.
_STEP_LABELS: dict[str, str] = {
    "success": "terminée",
    "failure": "échouée",
    "timed_out": "temps écoulé",
    "cancelled": "annulée",
    "skipped": "ignorée",
    "in_progress": "en cours",
    "queued": "en attente",
    "waiting": "en attente",
    "pending": "en attente",
}


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


def _step_style(status: str) -> tuple[str, str]:
    """Return (glyph, tag) for one step of an in-progress job."""
    if status == "success":
        return _MARK_SUCCESS, "success"
    if status in ("failure", "timed_out", "action_required"):
        return _MARK_FAILURE, "failure"
    if status == "in_progress":
        return _MARK_WAITING, "waiting"
    return _MARK_SKIPPED, "skipped"


def _step_label(status: str) -> str:
    """Human French label for a step status, with the raw value as fallback."""
    return _STEP_LABELS.get(status, status)


def _format_fetched_at(iso_timestamp: str) -> str:
    """Render an ISO timestamp as ``HH:MM:SS`` (local time); ``""`` when bad."""
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return ""


def _row_of(
    snapshot: ci_runs.RunsSnapshot,
) -> list[tuple[ci_runs.RunSummary, tuple[ci_runs.JobSummary, ...]]]:
    """Flatten ``(run, jobs)`` pairs in the snapshot's stable run order."""
    return [(run, snapshot.jobs.get(run.id, ())) for run in snapshot.runs]


def _run_badge(run: ci_runs.RunSummary) -> str:
    """Short header describing one run row."""
    return f"#{run.run_number} · {run.branch or run.head_sha[:8]}"


def runs_summary_text(snapshot: ci_runs.RunsSnapshot) -> str:
    """Short header describing the whole snapshot.

    Counts finished and in-flight runs and, when the host populated
    ``fetched_at``, appends the time of the last successful poll so the
    auto-refresh is visible without a button.
    """
    if snapshot.error:
        return f"GitHub Actions indisponible : {snapshot.error}"
    if not snapshot.runs:
        return "Aucun run GitHub Actions pour ce workspace."
    completed = sum(1 for run in snapshot.runs if run.conclusion)
    active = sum(1 for run in snapshot.runs if ci_runs.run_status_is_active(run.status))
    parts = [f"{len(snapshot.runs)} run(s)", f"{completed} terminé(s)"]
    if active:
        parts.append(f"{active} en cours")
    text = " · ".join(parts)
    if snapshot.fetched_at:
        text += f" — à jour {_format_fetched_at(snapshot.fetched_at)}"
    return text


class RunsPanel(tk.Frame):
    """Read-only runs → jobs → pipelines tree for one workspace.

    The widget is a plain renderer: it is fed :meth:`apply` with a
    :class:`~n8n_launcher.workspaces.ci_runs.RunsSnapshot` and shows a
    three-level tree whose tag colours encode each outcome. It never fetches
    data itself: the "open on GitHub" action and the "Lancer la CI" button
    delegate to the host callbacks, keeping I/O and threading in the caller.

    The host is expected to poll and :meth:`apply` fresh snapshots on a timer
    while the panel is visible; there is deliberately no manual refresh button.
    A run still in flight is auto-expanded (with its live steps and pipeline
    rows) and disables the "Lancer la CI" button, since launching a second CI
    run while one is running would cancel the live one.
    """

    # Panels alive in this process. Held weakly so a closed dialog does not
    # keep its widget tree (and Tcl interpreter plumbing) alive after the
    # host has dropped its own references.
    instances: ClassVar[weakref.WeakSet[RunsPanel]] = weakref.WeakSet()

    def __init__(
        self,
        parent: tk.Widget,
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
        RunsPanel.instances.add(self)

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

        self.tree = ttk.Treeview(self, columns=("detail",), show="tree headings", height=16)
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
        """Render *snapshot*, preserving the user's job-level expansion.

        The panel can be hosted in a CI dialog that is closed while a
        background fetch is in flight; a stale snapshot landing after the
        dialog's destruction must not touch Tk widgets belonging to a dead
        interpreter path (``TclError: invalid command name …``). The ``apply``
        is re-entered on the main thread only, so checking existence up front
        is enough — no destroy can interleave mid-render.
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._last = snapshot
        self._summary.config(text=runs_summary_text(snapshot))

        for item in self.tree.get_children():
            self.tree.delete(item)

        # While a run is in flight, launching a second one would cancel it
        # (the generated workflow carries ``concurrency: cancel-in-progress``),
        # so button state is derived straight from the snapshot.
        self.set_run_enabled(not self.has_active_run)

        if snapshot.error or not snapshot.runs:
            if snapshot.error:
                self._empty.config(
                    text=f"GitHub Actions indisponible : {snapshot.error}\n"
                    "Le panneau s'actualise automatiquement dès que GitHub répond."
                )
            else:
                self._empty.config(text="Aucun run GitHub Actions pour ce workspace.")
            return
        self._empty.config(text="")

        active_run_ids = {
            run.id for run in snapshot.runs if ci_runs.run_status_is_active(run.status)
        }
        for run, jobs in _row_of(snapshot):
            run_iid = self.layout_key(run)
            active_run = run.id in active_run_ids
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
                open=active_run or run_iid in self._expanded,
            )
            for job in jobs:
                job_iid = f"job-{run.id}-{job.id}"
                # A job of an in-flight run without a conclusion is the live
                # one; everything else is over (or still queued behind it).
                live_job = active_run and job.conclusion is None
                job_tag = {
                    "success": "success",
                    "failure": "failure",
                    "timed_out": "failure",
                    "action_required": "failure",
                    "cancelled": "skipped",
                    "skipped": "skipped",
                }.get(job.conclusion or job.status, "waiting")
                detail = job.human_status
                if live_job and job.current_step is not None:
                    detail += f" — étape : {job.current_step[0]}"
                self.tree.insert(
                    run_iid,
                    "end",
                    iid=job_iid,
                    text=f"    {_job_mark(job)}  {job.name}",
                    values=(detail,),
                    tags=(job_tag,),
                    open=live_job or job_iid in self._expanded,
                )
                if live_job:
                    # The live job's own step list is the closest thing to a
                    # progress meter: finished steps check-marked, the running
                    # one highlighted, the rest blanked out.
                    for name, status in job.steps:
                        mark, step_tag = _step_style(status)
                        self.tree.insert(
                            job_iid,
                            "end",
                            text=f"        {mark}  étape : {name}",
                            values=(_step_label(status),),
                            tags=(step_tag,),
                        )
                for pipeline in snapshot.pipelines.get(job.id, ()):
                    # Map the outcome to the panel's ASCII marks instead of
                    # echoing ``pipeline.mark`` (parsed from the runner log):
                    # that source glyph set (✔/◻/✘) is absent from Linux fonts.
                    pipeline_mark = {
                        "success": _MARK_SUCCESS,
                        "failure": _MARK_FAILURE,
                        "waiting": _MARK_WAITING,
                    }.get(pipeline.status, _MARK_SKIPPED)
                    self.tree.insert(
                        job_iid,
                        "end",
                        text=f"        {pipeline_mark}  {pipeline.rel}",
                        values=(pipeline.detail or pipeline.status,),
                        tags=("muted",) if pipeline.status == "waiting" else (),
                    )
            self.tree.item(run_iid, open=active_run or run_iid in self._expanded)

    @property
    def expanded(self) -> set[str]:
        return set(self._expanded)

    @property
    def has_active_run(self) -> bool:
        """True when the last snapshot holds a run that is still in flight."""
        return any(ci_runs.run_status_is_active(run.status) for run in self._last.runs)

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable or disable the "Lancer la CI" button (no-op without a host)."""
        if not hasattr(self, "_run_btn"):
            return
        with contextlib.suppress(Exception):
            self._run_btn.configure(state="normal" if enabled else "disabled")

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
