"""Unit tests for event presentation (:mod:`n8n_launcher.monitoring.present`).

The decisions that must not drift between renderers: how an event is described,
what a filter matches, and when a critical incident is worth surfacing. None of
them needs a display, which is why these live outside the GUI test suite — they
run on a CPython built without Tk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.core.subjects import PageSubject
from n8n_launcher.monitoring import present
from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.store import RETENTION_DAYS, EventStore

CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))
SERVER_SUBJECT = PageSubject("Serveur", ("Published", "server"))


def make_event(**kwargs) -> Event:
    """Build an :class:`Event` with test-friendly defaults."""
    defaults: dict[str, object] = {
        "id": 1,
        "timestamp": datetime(2026, 9, 25, 10, 0, 0, tzinfo=UTC),
        "level": "INFO",
        "name": "workspace.start",
        "message": "démarrage",
        "context": {},
        "exception": None,
    }
    return Event(**{**defaults, **kwargs})


class FakeClock:
    """Monotonic test clock so :class:`CriticalGate` windows are deterministic."""

    def __init__(self) -> None:
        """Start the clock at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += seconds


def _workspace(tmp_path: Path, **kwargs) -> Workspace:
    return Workspace(
        id=kwargs.pop("id", "ws-1"),
        name=kwargs.pop("name", "Demo"),
        workflows_dir=tmp_path / "workflows",
        port=kwargs.pop("port", 5678),
        db=DbConfig(DbMode.NONE),
        **kwargs,
    )


# --------------------------------------------------------------- formatting
def test_event_row_renders_time_level_source_message():
    event = make_event(level="ERROR", name="docker.up", message="échec du up")
    time_text, level, source, message = present.event_row(event)
    assert level == "ERROR"
    assert source == "docker.up"
    assert message == "échec du up"
    assert "2026" not in time_text


def test_event_row_uses_local_wall_clock():
    event = make_event()
    expected = event.timestamp.astimezone().strftime("%d/%m %H:%M:%S")
    assert present.event_row(event)[0] == expected


def test_event_detail_without_event_is_a_hint():
    assert "Sélectionnez" in present.event_detail(None)


def test_event_detail_renders_message_context_and_traceback():
    event = make_event(
        level="ERROR",
        context={"workspace": "demo", "port": 5678},
        exception="Traceback:\n  docker up failed",
    )
    detail = present.event_detail(event)
    assert "ERROR · workspace.start" in detail
    assert "démarrage" in detail
    assert '"port": 5678' in detail
    assert "docker up failed" in detail


def test_event_detail_skips_absent_sections():
    detail = present.event_detail(make_event())
    assert "démarrage" in detail
    assert "Traceback" not in detail


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        # A *semantic* name, never a colour: the palette belongs to the renderer,
        # which is what keeps this module importable without a toolkit.
        ("INFO", "info"),
        ("DEBUG", "info"),
        ("WARNING", "warning"),
        ("WARN", "warning"),
        ("ERROR", "error"),
        ("critical", "critical"),
        ("TRACE", "info"),
    ],
)
def test_level_tag_maps_severity(level, expected):
    assert present.level_tag(make_event(level=level)) == expected


def test_is_critical_only_errors_and_criticals():
    assert present.is_critical(make_event(level="ERROR"))
    assert present.is_critical(make_event(level="critical"))
    assert not present.is_critical(make_event(level="WARNING"))
    assert not present.is_critical(make_event(level="INFO"))


# ------------------------------------------------------------------ summary
def test_summary_text_reports_an_empty_journal(tmp_path: Path):
    summary = present.summary_text([], store=EventStore(tmp_path))
    assert "Tous les workspaces" in summary
    assert "aucun événement" in summary
    assert f"conservation {RETENTION_DAYS} j" in summary


def test_summary_text_counts_severities(tmp_path: Path):
    events = [
        make_event(level="INFO"),
        make_event(level="WARNING"),
        make_event(level="ERROR"),
        make_event(level="CRITICAL"),
    ]
    summary = present.summary_text(events, store=EventStore(tmp_path), scope="Demo")
    assert "Demo · 4 événement(s)" in summary
    assert "1 critique(s)" in summary
    assert "1 erreur(s)" in summary
    assert "1 avertissement(s)" in summary


def test_summary_text_without_store():
    assert "journal indisponible" in present.summary_text([make_event()], store=None)


def test_summary_text_names_the_subject():
    assert "sujet : Tests CI" in present.summary_text(
        [make_event()], store=None, subject=CI_SUBJECT.label
    )
    assert "sujet" not in present.summary_text([make_event()], store=None)


# ------------------------------------------------------------------ filters
def test_filter_events_without_query_keeps_everything():
    events = [make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")]
    assert present.filter_events(events) == events


def test_filter_events_matches_message_case_insensitively():
    events = [make_event(id=1, message="Docker UP ok"), make_event(id=2, message="rien")]
    assert [event.id for event in present.filter_events(events, query="docker up")] == [1]


def test_filter_events_searches_source_context_and_exception():
    events = [
        make_event(id=1, name="git.push", context={"remote": "github"}),
        make_event(id=2, name="ssh.run", exception="connection refused"),
        make_event(id=3, name="docker.up", message="ok"),
    ]
    assert [event.id for event in present.filter_events(events, query="github")] == [1]
    assert [event.id for event in present.filter_events(events, query="REFUSED")] == [2]
    assert present.filter_events(events, query="   ") == list(events)


def test_filter_events_matches_the_level():
    # No level control exists: the severity is simply part of the searched
    # text, so typing its name narrows the rows to that severity.
    events = [
        make_event(id=1, level="INFO", message="ok"),
        make_event(id=2, level="ERROR", message="ko"),
        make_event(id=3, level="WARNING", message="meh"),
    ]
    assert [event.id for event in present.filter_events(events, query="error")] == [2]
    assert [event.id for event in present.filter_events(events, query="warning")] == [3]


# ------------------------------------------------------------ subject filter
def test_filter_events_narrows_on_the_subject():
    events = [
        make_event(id=1, name="workspaces.manager", message="Enabled CI for Demo"),
        make_event(id=2, name="workspaces.manager", message="Published Demo to srv"),
        make_event(id=3, name="workspaces.manager", message="Recorded 2 CI credential(s)"),
    ]
    assert [event.id for event in present.filter_events(events, tokens=CI_SUBJECT.tokens)] == [1, 3]
    assert [event.id for event in present.filter_events(events, tokens=SERVER_SUBJECT.tokens)] == [
        2
    ]


def test_filter_events_tokens_ignore_case_on_purpose():
    # A case-insensitive match on a two-letter token would sweep in every event
    # carrying "credential" or "spécifique", so the subject match is exact.
    events = [make_event(id=1, message="ciblé sur un credential"), make_event(id=2, message="CI")]
    assert [event.id for event in present.filter_events(events, tokens=("CI",))] == [2]


def test_filter_events_searches_the_whole_event_for_tokens():
    event = make_event(id=1, name="n8n_launcher.remote.ssh", context={"host": "srv-ci-01"})
    assert present.filter_events([event], tokens=("srv-ci",)) == [event]
    # No token means no narrowing at all.
    assert present.matches_tokens(event, ()) is True


def test_filter_events_combines_the_query_and_the_subject():
    events = [
        make_event(id=1, message="Enabled CI for Demo"),
        make_event(id=2, message="Disabled CI for Demo"),
        make_event(id=3, message="Enabled CI for other"),
    ]
    selected = present.filter_events(events, query="demo", tokens=CI_SUBJECT.tokens)
    assert [event.id for event in selected] == [1, 2]


# ------------------------------------------------------------- critical gate
def test_critical_gate_accepts_first_error():
    assert present.CriticalGate(clock=FakeClock()).accept(
        make_event(level="ERROR", name="docker.up")
    )


def test_critical_gate_ignores_non_critical_events():
    assert not present.CriticalGate(clock=FakeClock()).accept(
        make_event(level="WARNING", name="docker.up")
    )


def test_critical_gate_groups_repeats_of_the_same_failure():
    clock = FakeClock()
    gate = present.CriticalGate(window_seconds=60, clock=clock)
    event = make_event(level="ERROR", name="docker.up")
    assert gate.accept(event)
    assert not gate.accept(event)
    clock.advance(59)
    assert not gate.accept(event)
    clock.advance(2)
    assert gate.accept(event)


def test_critical_gate_lets_a_different_failure_through():
    gate = present.CriticalGate(window_seconds=60, clock=FakeClock())
    assert gate.accept(make_event(level="ERROR", name="docker.up"))
    assert gate.accept(make_event(level="ERROR", name="git.push"))
    # A different level on the same logger is a different signature.
    assert gate.accept(make_event(level="CRITICAL", name="docker.up"))


def test_critical_gate_forget_clears_a_signature():
    gate = present.CriticalGate(window_seconds=60, clock=FakeClock())
    event = make_event(level="ERROR", name="docker.up")
    assert gate.accept(event)
    assert not gate.accept(event)
    gate.forget("ERROR|docker.up")
    assert gate.accept(event)


def test_critical_gate_default_window_is_a_minute():
    assert present.DEFAULT_GROUP_WINDOW_SECONDS == 60.0
    assert frozenset({"ERROR", "CRITICAL"}) == present.CRITICAL_LEVELS


# --------------------------------------------------------- workspace scoping
def test_workspace_events_matches_by_context_id(tmp_path: Path):
    workspace = _workspace(tmp_path)
    tagged = make_event(id=1, name="start", context={"workspace_id": "ws-1"})
    other = make_event(id=2, name="start", context={"workspace_id": "ws-2"})
    assert present.workspace_events(workspace, [other, tagged]) == [tagged]


def test_workspace_events_falls_back_to_the_name(tmp_path: Path):
    workspace = _workspace(tmp_path, name="Demo")
    matching = make_event(id=1, message="Start failed for Demo: docker down")
    assert present.workspace_events(workspace, [matching]) == [matching]


def test_workspace_events_ignores_a_prefixed_workspace_name(tmp_path: Path):
    workspace = _workspace(tmp_path, name="Demo")
    other = make_event(id=1, message="Start failed for Demo2: docker down")
    assert present.workspace_events(workspace, [other]) == []


def test_workspace_events_preserve_input_order(tmp_path: Path):
    workspace = _workspace(tmp_path)
    first = make_event(id=1, message="Starting Demo")
    second = make_event(id=2, message="Stopped Demo")
    assert [event.id for event in present.workspace_events(workspace, [first, second])] == [1, 2]


def test_workspace_events_are_case_insensitive(tmp_path: Path):
    workspace = _workspace(tmp_path, name="Demo")
    event = make_event(id=1, message="Published demo to prod")
    assert present.workspace_events(workspace, [event]) == [event]
