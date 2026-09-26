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
from collections.abc import Iterable
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
    "error": "en échec",
    "failed": "en échec",
    "crashed": "crash",
    "timeout": "délai dépassé",
    "cancelled": "annulée",
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
    r"^\[runner\]\s+(?P<rel>.+?)\s*:\s*(?P<status>[A-Za-z][A-Za-z_ -]*?)"
    r"(?:\s+\((?P<detail>.*)\))?\s*$"
)
_SUMMARY_ROW = re.compile(
    r"^-\s+(?P<mark>[\u2714\u25fb\u2718+!*])\s+(?P<rel>.+?)\s+\((?P<label>.*)\)\s*$"
)
_FAILURE_DETAIL = re.compile(r"^échec\s*:\s*(?P<rel>.+?)\s+\((?P<detail>.*)\)\s*$", re.IGNORECASE)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TIMESTAMP_PREFIX = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
)
_FAILURE_STATUSES = frozenset(
    {
        PIPELINE_FAILURE,
        "error",
        "failed",
        "crashed",
        "timeout",
        "timed_out",
        "cancelled",
    }
)
_DEFAULT_PIPELINE_DETAILS = frozenset(
    {
        "réussie",
        "réussi",
        "succès",
        "en attente",
        "en échec",
        "échec",
        "échoué",
        "erreur",
        "crash",
        "délai dépassé",
        "annulée",
    }
)
_SUMMARY_MARK_STATUS: dict[str, str] = {
    _MARK_SUCCESS: PIPELINE_SUCCESS,
    _MARK_WAITING: PIPELINE_WAITING,
    _MARK_FAILURE: PIPELINE_FAILURE,
    "+": PIPELINE_SUCCESS,
    "*": PIPELINE_WAITING,
    "!": PIPELINE_FAILURE,
}


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _as_optional_int(value: object) -> int | None:
    if value is None:
        return None
    parsed = _as_int(value, -1)
    return parsed if parsed >= 0 else None


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_str(value: object) -> str | None:
    text = _as_str(value).strip()
    return text or None


def _normalise_messages(values: Iterable[str] | str | None) -> tuple[str, ...]:
    """Return non-empty, de-duplicated user-facing messages in stable order."""
    if values is None:
        return ()
    raw_values: Iterable[object] = (values,) if isinstance(values, str) else values
    messages: list[str] = []
    for value in raw_values:
        text = str(value).strip()
        if text and text not in messages:
            messages.append(text)
    return tuple(messages)


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
    updated_at: str | None = None
    run_started_at: str | None = None

    @property
    def human_status(self) -> str:
        """End-user French label for the run's raw status."""
        return _RUN_STATUS_LABELS.get(self.status, self.status)

    @property
    def started_at(self) -> str | None:
        """Return the runner start timestamp when GitHub provides one."""
        return self.run_started_at

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
    run_id: int | None = None
    started_at: str | None = None
    completed_at: str | None = None

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
    timestamp: str | None = None

    @property
    def label(self) -> str:
        return PIPELINE_STATUS_LABELS.get(self.status, self.status)

    @property
    def human_status(self) -> str:
        """Return the same human label through a status-oriented name."""
        return self.label

    @property
    def failure_detail(self) -> str:
        """Return the failure explanation, if this result is a failure."""
        return self.detail if self.status == PIPELINE_FAILURE else ""

    @property
    def display_detail(self) -> str:
        """Return a label and optional detail suitable for a details column."""
        return f"{self.label} — {self.detail}" if self.detail else self.label


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


def run_summary(payload: dict[str, Any]) -> RunSummary:
    """Normalise a ``workflow_runs[]`` payload into a :class:`RunSummary`."""
    return RunSummary(
        id=_as_int(payload.get("id")),
        run_number=_as_int(payload.get("run_number")),
        branch=_as_str(payload.get("head_branch")),
        head_sha=_as_str(payload.get("head_sha")),
        status=_as_str(payload.get("status")),
        conclusion=_as_str(payload.get("conclusion")) or None,
        created_at=_optional_str(payload.get("created_at")),
        url=_optional_str(payload.get("html_url")),
        updated_at=_optional_str(payload.get("updated_at")),
        run_started_at=_optional_str(payload.get("run_started_at")),
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
        url=_optional_str(payload.get("html_url")),
        steps=tuple(steps),
        run_id=_as_optional_int(payload.get("run_id")),
        started_at=_optional_str(payload.get("started_at")),
        completed_at=_optional_str(payload.get("completed_at")),
    )


def _clean_log_line(raw: str) -> tuple[str, str | None]:
    """Remove transport noise and return ``(line, optional timestamp)``."""
    line = _ANSI_ESCAPE.sub("", raw).rstrip("\r").lstrip()
    match = _TIMESTAMP_PREFIX.match(line)
    if match is None:
        return line, None
    return line[match.end() :].lstrip(), match.group("timestamp")


def _normalise_pipeline_status(value: str) -> str | None:
    """Map runner status variants to the three display states."""
    status = value.strip().casefold().replace("-", "_")
    if status == PIPELINE_SUCCESS:
        return PIPELINE_SUCCESS
    if status == PIPELINE_WAITING:
        return PIPELINE_WAITING
    if status in _FAILURE_STATUSES:
        return PIPELINE_FAILURE
    return None


def _pipeline_detail(value: str) -> str:
    """Discard a generic status label while preserving explanatory details."""
    detail = value.strip()
    return "" if detail.casefold() in _DEFAULT_PIPELINE_DETAILS else detail


def parse_pipeline_lines(log_text: str) -> list[PipelineResult]:
    """Extract per-pipeline outcomes from a job's GitHub Actions log.

    The runner prints one ``[runner] <rel> : <status>`` progress line per
    pipeline as it executes, then a closing summary block. This function
    prefers the final summary, but merges its row with the last progress line
    for the same pipeline so a failure explanation is not replaced by the
    summary's generic ``en échec`` label. When the block is missing (log
    truncated mid-run, or an older runner), it falls back to the last progress
    line seen per pipeline. Only lines that match one of the two known shapes
    are considered; anything else is ignored.
    """
    summary_rows: list[PipelineResult] = []
    last_progress: dict[str, PipelineResult] = {}
    failure_details: dict[str, str] = {}
    in_summary = False

    for raw in log_text.splitlines():
        line, timestamp = _clean_log_line(raw)
        if line.startswith(_SUMMARY_HEADER):
            in_summary = True
            continue
        if in_summary:
            if line.startswith(_SUMMARY_FOOTER_PREFIX) or not line.lstrip().startswith("-"):
                in_summary = False
                continue
            row = _SUMMARY_ROW.match(line.lstrip())
            if row is None:
                in_summary = False
                continue
            status = _SUMMARY_MARK_STATUS.get(row.group("mark"))
            if status is None:
                in_summary = False
                continue
            rel = row.group("rel").strip()
            detail = _pipeline_detail(row.group("label"))
            previous = last_progress.get(rel)
            if not detail and previous is not None:
                detail = previous.detail
            if not detail:
                detail = failure_details.get(rel, "")
            summary_rows.append(
                PipelineResult(
                    rel=rel,
                    status=status,
                    detail=detail,
                    mark=row.group("mark"),
                    timestamp=previous.timestamp if previous is not None else timestamp,
                )
            )
            continue

        failure_line = _FAILURE_DETAIL.match(line)
        if failure_line is not None:
            rel = failure_line.group("rel").strip()
            detail = _pipeline_detail(failure_line.group("detail"))
            if detail:
                failure_details[rel] = detail
            previous = last_progress.get(rel)
            if previous is None or previous.status != PIPELINE_FAILURE or not previous.detail:
                last_progress[rel] = PipelineResult(
                    rel=rel,
                    status=PIPELINE_FAILURE,
                    detail=detail or (previous.detail if previous is not None else ""),
                    mark=_MARK_FAILURE,
                    timestamp=previous.timestamp if previous is not None else timestamp,
                )
            continue

        progress = _PROGRESS_LINE.match(line)
        if progress is not None:
            status = _normalise_pipeline_status(progress.group("status"))
            if status is None:
                continue
            rel = progress.group("rel").strip()
            detail = _pipeline_detail(progress.group("detail") or "")
            if not detail:
                detail = failure_details.get(rel, "")
            last_progress[rel] = PipelineResult(
                rel=rel,
                status=status,
                detail=detail,
                mark=_MARK_MAP[status],
                timestamp=timestamp,
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
    the caller (the panel itself never touches the network). A top-level
    ``error`` means that the run list could not be obtained. ``warnings`` and
    ``partial_errors`` describe failures that happened while enriching an
    otherwise usable run list, so the panel can render the partial tree rather
    than silently dropping it. A ``note`` is the opposite of an ``error``: the
    read was skipped on purpose (no GitHub remote, say), so the panel shows
    why the tab is empty instead of reporting a failure.
    """

    repo_path: str
    runs: tuple[RunSummary, ...] = ()
    jobs: dict[int, tuple[JobSummary, ...]] = field(default_factory=dict)
    pipelines: dict[int, tuple[PipelineResult, ...]] = field(default_factory=dict)
    error: str | None = None
    fetched_at: str | None = None  # ISO timestamp of the last successful poll
    warnings: tuple[str, ...] = ()
    partial_errors: tuple[str, ...] = ()
    note: str | None = None  # why there is nothing to show (never a failure)

    def __post_init__(self) -> None:
        """Normalise the message collections and the note for direct construction."""
        object.__setattr__(self, "warnings", _normalise_messages(self.warnings))
        object.__setattr__(self, "partial_errors", _normalise_messages(self.partial_errors))
        note = self.note.strip() if self.note else ""
        object.__setattr__(self, "note", note or None)

    @property
    def has_data(self) -> bool:
        """Return whether the snapshot contains a renderable run tree."""
        return bool(self.runs)

    @property
    def is_partial(self) -> bool:
        """Return whether usable runs are accompanied by a partial failure."""
        return self.has_data and bool(self.messages)

    @property
    def messages(self) -> tuple[str, ...]:
        """Return all user-facing partial or top-level messages in order."""
        values: list[str] = []
        for message in (*self.warnings, *self.partial_errors):
            if message not in values:
                values.append(message)
        if self.error and self.error not in values:
            values.append(self.error)
        return tuple(values)

    @property
    def has_partial_data(self) -> bool:
        """Compatibility alias for :attr:`is_partial`."""
        return self.is_partial


def compose_snapshot(
    *,
    repo_path: str,
    runs: list[dict[str, Any]],
    pipelines: dict[int, list[PipelineResult]] | None = None,
    raw_jobs: dict[int, list[dict[str, Any]]] | None = None,
    error: str | None = None,
    fetched_at: str | None = None,
    warnings: Iterable[str] | str | None = None,
    partial_errors: Iterable[str] | str | None = None,
    errors: Iterable[str] | str | None = None,
    note: str | None = None,
) -> RunsSnapshot:
    """Assemble a :class:`RunsSnapshot` from already-fetched API payloads.

    ``runs`` is the ``workflow_runs[]`` list; ``raw_jobs`` maps each run id to
    its ``jobs[]`` payload (already JSON-decoded by the caller) so this helper
    stays free of any I/O. Per-job pipeline outcomes in ``pipelines`` are keyed
    by **job id** and matched back to their run through the job's ``run_id``
    field. Missing payloads yield empty collections, never exceptions. A
    non-empty ``error`` is preserved alongside any runs that were fetched, and
    ``warnings``/``partial_errors`` make enrichment failures explicit.
    ``errors`` is accepted as a compatibility spelling for partial errors.
    ``note`` explains an intentionally empty result (no GitHub remote) and is
    not a failure.
    """
    run_summaries = tuple(run_summary(run) for run in runs if isinstance(run, dict))
    jobs_by_run: dict[int, tuple[JobSummary, ...]] = {}
    pipelines_by_job: dict[int, tuple[PipelineResult, ...]] = {}
    if raw_jobs:
        for raw_run_id, job_payloads in raw_jobs.items():
            run_id = _as_int(raw_run_id, -1)
            if run_id < 0 or not isinstance(job_payloads, list):
                continue
            summarized = tuple(job_summary(job) for job in job_payloads if isinstance(job, dict))
            if summarized:
                jobs_by_run[run_id] = summarized
    if pipelines:
        for raw_job_id, results in pipelines.items():
            job_id = _as_int(raw_job_id, -1)
            if job_id < 0 or not isinstance(results, (list, tuple)):
                continue
            valid_results = tuple(
                result for result in results if isinstance(result, PipelineResult)
            )
            if valid_results:
                pipelines_by_job[job_id] = valid_results
    combined_errors = (*_normalise_messages(partial_errors), *_normalise_messages(errors))
    return RunsSnapshot(
        repo_path=repo_path,
        runs=run_summaries,
        jobs=jobs_by_run,
        pipelines=pipelines_by_job,
        error=error,
        fetched_at=fetched_at,
        warnings=_normalise_messages(warnings),
        partial_errors=_normalise_messages(combined_errors),
        note=note,
    )
