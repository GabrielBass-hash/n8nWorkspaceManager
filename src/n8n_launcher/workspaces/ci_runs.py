"""Pure models and parsing for GitHub Actions CI runs.

This module contains no I/O and no Tkinter — everything here is a pure
function or dataclass that consumes the payloads returned by
:class:`n8n_launcher.github.api.GitHubClient` and produces the structures the
runs dialog displays. Keeping it dependency-free makes it trivially testable
and lets the GUI run its log-parsing on the worker thread.

Two concerns live here:

* Normalisation of the GitHub REST payloads (:func:`run_summary`,
  :func:`job_summary`) into stable dataclasses.
* :func:`parse_pipeline_lines` — extraction of per-pipeline outcomes from a
  job's raw GitHub Actions log. The parser understands the two shapes the
  generated runner prints:

  * the live progress line ``[runner] <rel> : <status>[<suffix>]``
    (``status`` ∈ success/waiting/failure), and
  * the closing summary block ``Résultats des pipelines :`` with one
    ``  - <mark> <rel> (<label>)`` row per pipeline.

  Both shapes are matched so the dialog can show either the live state or the
  final summarised state without depending on a particular runner version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Status values the generated runner uses inside the CI container.
PIPELINE_SUCCESS = "success"
PIPELINE_WAITING = "waiting"
PIPELINE_FAILURE = "failure"

# Human-readable French labels for the *pipeline* statuses (runner view).
PIPELINE_STATUS_LABELS: dict[str, str] = {
    PIPELINE_SUCCESS: "réussie",
    PIPELINE_WAITING: "en attente",
    PIPELINE_FAILURE: "en échec",
}

# Character drawn in the logs' final summary block before each pipeline row.
_MARK_SUCCESS = "\u2714"  # ✔
_MARK_WAITING = "\u25fb"  # ◻
_MARK_FAILURE = "\u2718"  # ✘

# Framing lines of the runner's closing summary block.
_SUMMARY_HEADER = "Résultats des pipelines :"
_SUMMARY_FOOTER_PREFIX = "réussies :"

# Regexes — anchored so we never match the summary's own rows.
_PROGRESS_LINE = re.compile(
    r"^\[runner\]\s+(?P<rel>.+?)\s*:\s*(?P<status>[a-z]+)"
    r"(?P<suffix>\s+\([^)]*\))?\s*$"
)
_SUMMARY_ROW = re.compile(
    r"^-\s+(?P<mark>\u2714|\u25fb|\u2718)\s+(?P<rel>.+?)\s+\((?P<label>[^)]*)\)$"
)


@dataclass(frozen=True)
class RunSummary:
    """Stable view of a single workflow run row."""

    id: int
    run_number: int
    branch: str
    head_sha: str
    status: str  # raw GitHub status: queued | in_progress | completed
    conclusion: str | None  # success | failure | cancelled | ...
    created_at: str | None
    url: str | None

    @property
    def human_status(self) -> str:
        """End-user French label for the run's raw status."""
        return _RUN_STATUS_LABELS.get(self.status, self.status)

    @property
    def key(self) -> str:
        return f"run-{self.id}"


@dataclass(frozen=True)
class JobSummary:
    """Stable view of one job of a workflow run."""

    id: int
    name: str
    status: str
    conclusion: str | None
    url: str | None
    # Workflow steps, purely decorative (displayed read-only under the job).
    steps: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def human_status(self) -> str:
        """End-user French label for the job's status."""
        concluded = self.conclusion or self.status
        return _JOB_STATUS_LABELS.get(concluded, _JOB_STATUS_LABELS.get(self.status, concluded))

    @property
    def current_step(self) -> tuple[str, str] | None:
        """Return the first step still in flight ``(name, status)``, or ``None``.

        Lets the runs panel highlight exactly what the live job is doing without
        repeating the whole step list. The stored status is a conclusion-or-
        status string, so a finished step reads ``success``/``failure``/… while
        only queued/waiting/in-progress steps count as unfinished. Completed
        jobs return ``None``.
        """
        for name, status in self.steps:
            if status not in ("queued", "waiting", "in_progress", "pending", "requested"):
                continue
            return name, status
        return None


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of a single pipeline parsed from a job's log."""

    rel: str
    status: str  # success | waiting | failure
    detail: str  # human suffix, e.g. a node/last-execution hint
    mark: str

    @property
    def label(self) -> str:
        return PIPELINE_STATUS_LABELS.get(self.status, self.status)


# French labels for the GitHub *run*-level status strings.
_RUN_STATUS_LABELS: dict[str, str] = {
    "success": "réussie",
    "failure": "en échec",
    "cancelled": "annulée",
    "timed_out": "temps écoulé",
    "action_required": "action requise",
    "startup_failure": "échec au démarrage",
    "stale": "obsolète",
    "skipped": "ignorée",
    "completed": "terminé",
    "in_progress": "en cours",
    "queued": "en file d'attente",
    "waiting": "en attente",
    "pending": "en attente de review",
    "requested": "demandée",
}

_JOB_STATUS_LABELS: dict[str, str] = {
    "success": "réussi",
    "failure": "échoué",
    "cancelled": "annulé",
    "timed_out": "temps écoulé",
    "action_required": "action requise",
    "skipped": "ignoré",
    "in_progress": "en cours",
    "queued": "en file d'attente",
    "waiting": "en attente",
    "completed": "terminé",
}

# GitHub run-status values meaning the workflow is still running (or waiting to
# start) and could still change. Anything else is finished and immutable.
RUN_ACTIVE_STATUSES = frozenset({"queued", "waiting", "in_progress", "pending", "requested"})


def run_status_is_active(status: str) -> bool:
    """Return True when a raw GitHub run status means the run is in flight."""
    return status in RUN_ACTIVE_STATUSES


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def run_summary(payload: dict[str, Any]) -> RunSummary:
    """Normalise a ``workflow_runs[]`` payload into a :class:`RunSummary`."""
    return RunSummary(
        id=_as_int(payload.get("id")),
        run_number=_as_int(payload.get("run_number")),
        branch=_as_str(payload.get("head_branch")),
        head_sha=_as_str(payload.get("head_sha")),
        status=_as_str(payload.get("status")),
        conclusion=_as_str(payload.get("conclusion")) or None,
        created_at=_as_str(payload.get("created_at")) or None,
        url=_as_str(payload.get("html_url")) or None,
    )


def job_summary(payload: dict[str, Any]) -> JobSummary:
    """Normalise a ``jobs[]`` payload into a :class:`JobSummary`."""
    raw_steps = payload.get("steps")
    steps: list[tuple[str, str]] = []
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            name = _as_str(step.get("name")) or _as_str(step.get("id"))
            conclusion = _as_str(step.get("conclusion")) or _as_str(step.get("status"))
            if name:
                steps.append((name, conclusion))
    return JobSummary(
        id=_as_int(payload.get("id")),
        name=_as_str(payload.get("name")),
        status=_as_str(payload.get("status")),
        conclusion=_as_str(payload.get("conclusion")) or None,
        url=_as_str(payload.get("html_url")) or None,
        steps=tuple(steps),
    )


def parse_pipeline_lines(log_text: str) -> list[PipelineResult]:
    """Extract per-pipeline outcomes from a job's GitHub Actions log.

    The runner prints one ``[runner] <rel> : <status>`` progress line per
    pipeline as it executes, then a closing summary block. This function
    prefers the final summary (it also carries the failure detail); when the
    block is missing (log truncated mid-run, or an older runner), it falls
    back to the last progress line seen per pipeline. Only lines that match
    one of the two known shapes are considered; anything else is ignored.
    """
    summary_rows: list[PipelineResult] = []
    last_progress: dict[str, PipelineResult] = {}

    in_summary = False
    for raw in log_text.splitlines():
        if raw.startswith(_SUMMARY_HEADER):
            in_summary = True
            continue
        if in_summary:
            if raw.startswith(_SUMMARY_FOOTER_PREFIX) or not raw.lstrip().startswith("-"):
                in_summary = False
                continue
            row = _SUMMARY_ROW.match(raw.lstrip())
            if row is None:
                in_summary = False
                continue
            label = row.group("label") or ""
            status = (
                PIPELINE_SUCCESS
                if row.group("mark") == _MARK_SUCCESS
                else PIPELINE_WAITING
                if row.group("mark") == _MARK_WAITING
                else PIPELINE_FAILURE
            )
            summary_rows.append(
                PipelineResult(
                    rel=row.group("rel").strip(),
                    status=status,
                    detail=label if label not in ("réussie", "en attente", "en échec") else "",
                    mark=row.group("mark"),
                )
            )
            continue

        progress = _PROGRESS_LINE.match(raw)
        if progress is not None:
            status = progress.group("status")
            if status not in (PIPELINE_SUCCESS, PIPELINE_WAITING, PIPELINE_FAILURE):
                continue
            mark = _MARK_MAP[status]
            suffix = progress.group("suffix")
            last_progress[progress.group("rel").strip()] = PipelineResult(
                rel=progress.group("rel").strip(),
                status=status,
                detail=(suffix.strip().strip("()") if suffix else ""),
                mark=mark,
            )

    if summary_rows:
        return summary_rows
    return list(last_progress.values())


_MARK_MAP: dict[str, str] = {
    PIPELINE_SUCCESS: _MARK_SUCCESS,
    PIPELINE_WAITING: _MARK_WAITING,
    PIPELINE_FAILURE: _MARK_FAILURE,
}


@dataclass(frozen=True)
class RunsSnapshot:
    """Everything the runs panel renders, pre-normalised.

    This is the single structure the GUI consumes: a workspace's most recent
    workflow runs with their jobs, and — once a job is finished — the
    per-pipeline outcomes extracted from its log. It is built by
    :func:`compose_snapshot`, which only accepts payloads already fetched by
    the caller (the panel itself never touches the network). When ``error`` is
    set the runs/jobs collections are empty and the panel shows the message
    instead of a partial tree.
    """

    repo_path: str
    runs: tuple[RunSummary, ...] = ()
    jobs: dict[int, tuple[JobSummary, ...]] = field(default_factory=dict)
    pipelines: dict[int, tuple[PipelineResult, ...]] = field(default_factory=dict)
    error: str | None = None
    fetched_at: str | None = None  # ISO timestamp of the last successful poll

    @property
    def has_data(self) -> bool:
        return bool(self.runs) and self.error is None


def compose_snapshot(
    *,
    repo_path: str,
    runs: list[dict[str, Any]],
    pipelines: dict[int, list[PipelineResult]] | None = None,
    raw_jobs: dict[int, list[dict[str, Any]]] | None = None,
    error: str | None = None,
    fetched_at: str | None = None,
) -> RunsSnapshot:
    """Assemble a :class:`RunsSnapshot` from already-fetched API payloads.

    ``runs`` is the ``workflow_runs[]`` list; ``raw_jobs`` maps each run id to
    its ``jobs[]`` payload (already JSON-decoded by the caller) so this helper
    stays free of any I/O. Per-job pipeline outcomes in ``pipelines`` are keyed
    by **job id** and matched back to their run through the job's ``run_id``
    field. Missing payloads yield empty collections, never exceptions.
    """
    run_summaries = tuple(run_summary(run) for run in runs if isinstance(run, dict))
    jobs_by_run: dict[int, tuple[JobSummary, ...]] = {}
    pipelines_by_job: dict[int, tuple[PipelineResult, ...]] = {}
    if raw_jobs:
        for run_id, job_payloads in raw_jobs.items():
            summarized = tuple(job_summary(job) for job in job_payloads if isinstance(job, dict))
            if summarized:
                jobs_by_run[run_id] = summarized
    if pipelines:
        pipelines_by_job = {
            int(job_id): tuple(results) for job_id, results in pipelines.items() if int(job_id) >= 0
        }
    return RunsSnapshot(
        repo_path=repo_path,
        runs=run_summaries,
        jobs=jobs_by_run,
        pipelines=pipelines_by_job,
        error=error,
        fetched_at=fetched_at,
    )
