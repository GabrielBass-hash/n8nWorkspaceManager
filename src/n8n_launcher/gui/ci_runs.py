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
from .layout import ColumnFitter, bind_wraplength
from .theme import (
    APP_BACKGROUND,
    FONT_META,
    FONT_ROWS,
    SURFACE,
    TEXT_MUTED,
    text_measure,
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

# Runs tree: the label column carries the run, job, step and pipeline names —
# deeply indented, so it needs real room — while "Détails" holds a short status
# or timestamp. Both are sized to their content by the fitter; see
# ``gui.layout``. These two tables share a dialog, so their maximums are sized to
# add up to a dialog that fits a display.
_RUNS_COLUMNS = ("detail",)
_RUNS_HEADINGS = {"#0": "Run / job / pipeline", "detail": "Détails"}
_RUNS_MINIMUMS = {"#0": 240, "detail": 160}
_RUNS_MAXIMUMS = {"#0": 420, "detail": 520}

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


def _format_timestamp(iso_timestamp: str | None) -> str:
    """Render an ISO timestamp in local time; ``""`` when it is invalid."""
    if not iso_timestamp:
        return ""
    try:
        value = iso_timestamp[:-1] + "+00:00" if iso_timestamp.endswith("Z") else iso_timestamp
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%d/%m/%Y %H:%M")


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
    """Short header describing one run row, including its creation time."""
    badge = f"#{run.run_number} · {run.branch or run.head_sha[:8]}"
    created_at = _format_timestamp(run.created_at)
    return f"{badge} · {created_at}" if created_at else badge


def _snapshot_messages(snapshot: ci_runs.RunsSnapshot) -> tuple[str, ...]:
    """Return partial and top-level messages in display order."""
    return snapshot.messages


def runs_summary_text(snapshot: ci_runs.RunsSnapshot) -> str:
    """Short header describing the whole snapshot and any partial failures.

    Counts finished and in-flight runs and, when the host populated
    ``fetched_at``, appends the time of the last successful poll so the
    auto-refresh is visible without a button. Warnings and partial errors are
    appended without suppressing the run tree.
    """
    if snapshot.error and not snapshot.runs:
        return f"GitHub Actions indisponible : {snapshot.error}"
    if not snapshot.runs:
        text = "Aucun run GitHub Actions pour ce workspace."
        if snapshot.messages:
            return f"{text}\n" + " · ".join(snapshot.messages)
        return text
    completed = sum(1 for run in snapshot.runs if run.conclusion)
    active = sum(1 for run in snapshot.runs if ci_runs.run_status_is_active(run.status))
    parts = [f"{len(snapshot.runs)} run(s)", f"{completed} terminé(s)"]
    if active:
        parts.append(f"{active} en cours")
    text = " · ".join(parts)
    if snapshot.fetched_at:
        text += f" — à jour {_format_fetched_at(snapshot.fetched_at)}"
    if snapshot.messages:
        text += " — " + " · ".join(f"Avertissement : {message}" for message in snapshot.messages)
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
        # Set by the host when the panel's width drives a window geometry. The
        # runs tree is fed asynchronously, so a snapshot can arrive that is wider
        # than the dialog that opened around it; the panel says so through this
        # callback rather than reaching for the window manager itself. Same
        # late-binding as the other callbacks: it can only be set once the panel
        # exists, which is after the dialog it fits.
        self.fitted_cb: Callable[[], None] | None = None
        self._expanded: set[str] = set()
        self._active: set[str] = set()
        self._row_runs: dict[str, ci_runs.RunSummary] = {}
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

        self.tree = ttk.Treeview(self, columns=_RUNS_COLUMNS, show="tree headings", height=16)
        for name, heading in _RUNS_HEADINGS.items():
            self.tree.heading(name, text=heading)
        for name in ("#0", *_RUNS_COLUMNS):
            # Request the minimum up front: the dialog opens only as wide as
            # the labels need; the fitter re-fits on the first real render.
            self.tree.column(
                name,
                width=_RUNS_MINIMUMS[name],
                minwidth=_RUNS_MINIMUMS[name],
                stretch=False,
                anchor="w",
            )
        self._fitter = ColumnFitter(
            self.tree,
            columns=_RUNS_COLUMNS,
            headings=_RUNS_HEADINGS,
            minimums=_RUNS_MINIMUMS,
            maximums=_RUNS_MAXIMUMS,
            measure=text_measure(self, FONT_META),
        )
        for tag, color in (
            ("success", _TAG_SUCCESS),
            ("failure", _TAG_FAILURE),
            ("waiting", _TAG_WAITING),
            ("skipped", _TAG_SKIPPED),
        ):
            self.tree.tag_configure(tag, background=color)
        self.tree.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_close)

        self._empty = tk.Label(
            self,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_ROWS,
            anchor="w",
            justify="left",
            wraplength=640,
        )
        # The message under the tree repeats a GitHub error verbatim: it wraps
        # at the panel's real width rather than a hard-coded 900 pixels.
        bind_wraplength(self._empty, minimum=200, padding=28)
        self._empty.pack(fill="x", padx=14, pady=(0, 10))

    # ------------------------------------------------------------------ API

    def layout_key(self, run: ci_runs.RunSummary) -> str:
        """Stable iid for a run row, also used to persist its expansion state."""
        return f"run-{run.id}"

    def apply(self, snapshot: ci_runs.RunsSnapshot) -> None:
        """Render *snapshot*, preserving selection and expansion state.

        A snapshot may contain usable runs together with enrichment failures.
        Such a snapshot is rendered as a partial tree and its messages are
        shown below the tree instead of replacing the data. A stale snapshot
        landing after the dialog's destruction is ignored.
        """
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        selected = self.tree.selection()
        self._last = snapshot
        self._summary.config(text=runs_summary_text(snapshot))

        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_runs.clear()
        # While a run is in flight, launching a second one would cancel it
        # (the generated workflow carries ``concurrency: cancel-in-progress``),
        # so button state is derived straight from the snapshot.
        self.set_run_enabled(not self.has_active_run)

        if not snapshot.runs:
            if snapshot.error:
                self._empty.config(
                    text=f"GitHub Actions indisponible : {snapshot.error}\n"
                    "Le panneau s'actualise automatiquement dès que GitHub répond."
                )
            else:
                text = "Aucun run GitHub Actions pour ce workspace."
                if snapshot.messages:
                    text += "\n" + " · ".join(snapshot.messages)
                self._empty.config(text=text)
            self._restore_selection(selected)
            self._refit()
            return

        if snapshot.messages:
            self._empty.config(text="\n".join(snapshot.messages))
        else:
            self._empty.config(text="")

        active_run_ids = {
            run.id for run in snapshot.runs if ci_runs.run_status_is_active(run.status)
        }
        for run, jobs in _row_of(snapshot):
            run_iid = self.layout_key(run)
            self._row_runs[run_iid] = run
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
                self._row_runs[job_iid] = run
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
                job_timestamp = _format_timestamp(job.completed_at or job.started_at)
                if job_timestamp:
                    detail += f" — {job_timestamp}"
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
                for index, pipeline in enumerate(snapshot.pipelines.get(job.id, ())):
                    pipeline_iid = f"pipeline-{run.id}-{job.id}-{index}"
                    self._row_runs[pipeline_iid] = run
                    # Map the outcome to the panel's ASCII marks instead of
                    # echoing ``pipeline.mark`` (parsed from the runner log):
                    # that source glyph set (✔/◻/✘) is absent from Linux fonts.
                    pipeline_mark = {
                        "success": _MARK_SUCCESS,
                        "failure": _MARK_FAILURE,
                        "waiting": _MARK_WAITING,
                    }.get(pipeline.status, _MARK_SKIPPED)
                    pipeline_text = f"        {pipeline_mark}  {pipeline.rel} ({pipeline.label})"
                    pipeline_detail = pipeline.detail or pipeline.label
                    pipeline_timestamp = _format_timestamp(pipeline.timestamp)
                    if pipeline_timestamp:
                        pipeline_detail = f"{pipeline_detail} · {pipeline_timestamp}"
                    pipeline_tag = {
                        "success": "success",
                        "failure": "failure",
                        "waiting": "muted",
                    }.get(pipeline.status)
                    self.tree.insert(
                        job_iid,
                        "end",
                        iid=pipeline_iid,
                        text=pipeline_text,
                        values=(pipeline_detail,),
                        tags=(pipeline_tag,) if pipeline_tag else (),
                    )
            self.tree.item(run_iid, open=active_run or run_iid in self._expanded)
        self._restore_selection(selected)
        self._refit()

    def _refit(self) -> None:
        """Size the columns to the rows just rendered, then let the host resize.

        Every label is in by the time this runs, so the nested step and pipeline
        rows get the room their (indented) names ask for.
        """
        self._fitter.rows()
        if self.fitted_cb is not None:
            self.fitted_cb()

    def _restore_selection(self, selected: tuple[str, ...] | list[str]) -> None:
        """Restore a selected row when its stable id survived the refresh."""
        iid = next((candidate for candidate in selected if candidate in self._row_runs), None)
        if iid is None:
            return
        with contextlib.suppress(Exception):
            self.tree.selection_set(iid)

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
        if not iid:
            return
        if is_open:
            self._expanded.add(iid)
        else:
            self._expanded.discard(iid)

    def _focused_iid(self) -> str | None:
        """Return the focused row id when the Tk tree provides one."""
        with contextlib.suppress(Exception):
            iid = self.tree.focus()
            if isinstance(iid, str) and iid:
                return iid
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _on_tree_open(self, _event: tk.Event) -> None:
        """Remember a row opened by the user."""
        iid = self._focused_iid()
        if iid is not None:
            self.remember_expansion(iid, True)

    def _on_tree_close(self, _event: tk.Event) -> None:
        """Remember a row closed by the user."""
        iid = self._focused_iid()
        if iid is not None:
            self.remember_expansion(iid, False)

    def refresh(self) -> None:
        """Ask the host to fetch fresh data (the panel never fetches itself)."""
        self.refresh_cb()

    def run(self) -> None:
        """Ask the host to trigger a CI run (no-op when no host callback)."""
        if self.run_cb is not None:
            self.run_cb()

    def _open_selected(self) -> None:
        """Open the GitHub page for the run owning the selected row."""
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        run = self._row_runs.get(iid)
        if run is not None:
            self.open_run_cb(run)
            return
        for candidate, _jobs in _row_of(self._last):
            if iid == self.layout_key(candidate):
                self.open_run_cb(candidate)
                return
