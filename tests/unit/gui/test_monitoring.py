"""Unit tests for the monitoring panel (:mod:`n8n_launcher.gui.monitoring`)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from helpers import (
    FakeRoot,
    FakeTk,
    FakeTtk,
    fake_monitoring_panel_bases,
    fake_server_panel_bases,
)

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.gui import monitoring
from n8n_launcher.gui.monitoring import ServerSnapshot
from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.store import EventStore
from n8n_launcher.remote import RemoteExecution, RemoteExecutionStatus, RemoteHealth


@contextmanager
def fake_gui():
    """Build monitoring widgets on the Tk fakes, as the CI runs panel tests do."""
    with (
        fake_monitoring_panel_bases(),
        fake_server_panel_bases(),
        patch("n8n_launcher.gui.monitoring.tk", FakeTk()),
        patch("n8n_launcher.gui.monitoring.ttk", FakeTtk()),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_fake_widgets():
    """Keep the shared fake widget registries from leaking across tests."""
    FakeTtk.Button.instances.clear()
    FakeTtk.Radiobutton.instances.clear()
    FakeTk.Toplevel.instances.clear()
    yield


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


def _walk(node: object) -> list[object]:
    """Return every widget packed under *node*, depth first."""
    collected: list[object] = []
    for child in getattr(node, "children", []):
        collected.append(child)
        collected.extend(_walk(child))
    return collected


# --------------------------------------------------------------- formatting
def test_event_row_renders_time_level_source_message():
    event = make_event(level="ERROR", name="docker.up", message="échec du up")
    time_text, level, source, message = monitoring.event_row(event)
    assert level == "ERROR"
    assert source == "docker.up"
    assert message == "échec du up"
    assert "2026" not in time_text


def test_event_row_uses_local_wall_clock():
    event = make_event()
    expected = event.timestamp.astimezone().strftime("%d/%m %H:%M:%S")
    assert monitoring.event_row(event)[0] == expected


def test_event_detail_without_event_is_a_hint():
    assert "Sélectionnez" in monitoring.event_detail(None)


def test_event_detail_renders_message_context_and_traceback():
    event = make_event(
        level="ERROR",
        context={"workspace": "demo", "port": 5678},
        exception="Traceback:\n  docker up failed",
    )
    detail = monitoring.event_detail(event)
    assert "ERROR · workspace.start" in detail
    assert "démarrage" in detail
    assert '"port": 5678' in detail
    assert "docker up failed" in detail


def test_event_detail_skips_absent_sections():
    detail = monitoring.event_detail(make_event())
    assert "démarrage" in detail
    assert "Traceback" not in detail


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("INFO", monitoring._TAG_INFO),
        ("DEBUG", monitoring._TAG_INFO),
        ("WARNING", monitoring._TAG_WARNING),
        ("WARN", monitoring._TAG_WARNING),
        ("ERROR", monitoring._TAG_ERROR),
        ("critical", monitoring._TAG_CRITICAL),
        ("TRACE", monitoring._TAG_INFO),
    ],
)
def test_level_tag_maps_severity(level, expected):
    assert monitoring.level_tag(make_event(level=level)) == expected


def test_is_critical_only_errors_and_criticals():
    assert monitoring.is_critical(make_event(level="ERROR"))
    assert monitoring.is_critical(make_event(level="critical"))
    assert not monitoring.is_critical(make_event(level="WARNING"))
    assert not monitoring.is_critical(make_event(level="INFO"))


# ------------------------------------------------------------------ summary
def test_summary_text_reports_an_empty_journal(tmp_path):
    store = EventStore(tmp_path)
    summary = monitoring.summary_text([], store=store)
    assert "Tous les workspaces" in summary
    assert "aucun événement" in summary
    assert f"conservation {monitoring.RETENTION_DAYS} j" in summary


def test_summary_text_counts_severities(tmp_path):
    store = EventStore(tmp_path)
    events = [
        make_event(level="INFO"),
        make_event(level="WARNING"),
        make_event(level="ERROR"),
        make_event(level="CRITICAL"),
    ]
    summary = monitoring.summary_text(events, store=store, scope="Demo")
    assert "Demo · 4 événement(s)" in summary
    assert "1 critique(s)" in summary
    assert "1 erreur(s)" in summary
    assert "1 avertissement(s)" in summary


def test_summary_text_without_store():
    assert "journal indisponible" in monitoring.summary_text([make_event()], store=None)


# ------------------------------------------------------------------ filters
def test_filter_events_by_level():
    events = [make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")]
    assert [event.id for event in monitoring.filter_events(events, level="error")] == [2]


def test_filter_events_without_level_keeps_everything():
    events = [make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")]
    assert len(monitoring.filter_events(events)) == 2


def test_filter_events_matches_message_case_insensitively():
    events = [make_event(id=1, message="Docker UP ok"), make_event(id=2, message="rien")]
    assert [event.id for event in monitoring.filter_events(events, query="docker up")] == [1]


def test_filter_events_searches_source_context_and_exception():
    events = [
        make_event(id=1, name="git.push", context={"remote": "github"}),
        make_event(id=2, name="ssh.run", exception="connection refused"),
        make_event(id=3, name="docker.up", message="ok"),
    ]
    assert [event.id for event in monitoring.filter_events(events, query="github")] == [1]
    assert [event.id for event in monitoring.filter_events(events, query="REFUSED")] == [2]
    assert monitoring.filter_events(events, query="   ") == list(events)


def test_filter_events_combines_level_and_query():
    events = [
        make_event(id=1, level="ERROR", message="docker down"),
        make_event(id=2, level="ERROR", message="git push"),
        make_event(id=3, level="INFO", message="docker up"),
    ]
    matches = monitoring.filter_events(events, level="ERROR", query="docker")
    assert [event.id for event in matches] == [1]


def test_filter_options_offer_every_level_plus_all():
    assert any(value == "" for _label, value in monitoring.filter_options())
    values = {value for _label, value in monitoring.filter_options()}
    assert {"CRITICAL", "ERROR", "WARNING", "INFO"} <= values


# ------------------------------------------------------------- critical gate
def test_critical_gate_accepts_first_error():
    gate = monitoring.CriticalGate(clock=FakeClock())
    assert gate.accept(make_event(level="ERROR", name="docker.up"))


def test_critical_gate_ignores_non_critical_events():
    gate = monitoring.CriticalGate(clock=FakeClock())
    assert not gate.accept(make_event(level="WARNING", name="docker.up"))


def test_critical_gate_groups_repeats_of_the_same_failure():
    clock = FakeClock()
    gate = monitoring.CriticalGate(window_seconds=60, clock=clock)
    event = make_event(level="ERROR", name="docker.up")
    assert gate.accept(event)
    assert not gate.accept(event)
    clock.advance(59)
    assert not gate.accept(event)
    clock.advance(2)
    assert gate.accept(event)


def test_critical_gate_lets_a_different_failure_through():
    gate = monitoring.CriticalGate(window_seconds=60, clock=FakeClock())
    assert gate.accept(make_event(level="ERROR", name="docker.up"))
    assert gate.accept(make_event(level="ERROR", name="git.push"))
    assert gate.accept(make_event(level="CRITICAL", name="docker.up"))


def test_critical_gate_forget_clears_a_signature():
    gate = monitoring.CriticalGate(window_seconds=60, clock=FakeClock())
    event = make_event(level="ERROR", name="docker.up")
    assert gate.accept(event)
    assert not gate.accept(event)
    gate.forget("ERROR|docker.up")
    assert gate.accept(event)


def test_critical_gate_default_window_is_a_minute():
    assert monitoring.DEFAULT_GROUP_WINDOW_SECONDS == 60.0


# -------------------------------------------------------------------- panel
def test_panel_apply_inserts_rows_with_severity_tags():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [make_event(id=1, level="INFO"), make_event(id=2, level="CRITICAL", name="publish")],
            store=None,
        )
    assert panel.tree.get_children() == ["event-1", "event-2"]
    assert panel.tree.item("event-2")["tags"] == [monitoring._TAG_CRITICAL]


def test_panel_layout_key_uses_event_id():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
    assert panel.layout_key(make_event(id=7)) == "event-7"
    assert panel.layout_key(make_event(id=None)).startswith("event-unsaved")


def test_panel_apply_replaces_previous_rows():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), make_event(id=2)], store=None)
        panel.apply([make_event(id=3)], store=None)
    assert panel.tree.get_children() == ["event-3"]


def test_panel_apply_deduplicates_identical_ids():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), make_event(id=1)], store=None)
    assert panel.tree.get_children() == ["event-1"]


def test_panel_apply_restores_the_selection_and_detail():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        first = make_event(id=1, message="un")
        second = make_event(id=2, level="ERROR", message="deux", exception="boom")
        panel.apply([first, second], store=None)
        panel.tree.selection_set("event-2")
        panel._on_select()
        assert panel.selected_event() is second
        panel.apply([first, second], store=None)
    assert panel.tree.selection() == ["event-2"]
    assert "boom" in panel._detail.text


def test_panel_apply_ignores_a_snapshot_after_destroy():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.winfo_exists = lambda: 0  # type: ignore[method-assign]
        panel.apply([make_event()], store=None)
    assert panel.tree.get_children() == []


def test_panel_selected_event_is_none_without_selection():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event()], store=None)
    assert panel.selected_event() is None


def test_panel_apply_filters_rows():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        events = [make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")]
        panel.apply(events, store=None, level="ERROR")
    assert panel.tree.get_children() == ["event-2"]


def test_panel_summary_reports_the_store_retention(tmp_path):
    store = EventStore(tmp_path)
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1)], store=store)
    assert "conservation 30 j" in panel._summary.text


def test_panel_summary_reports_a_missing_store():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1)], store=None)
    assert "journal indisponible" in panel._summary.text


def test_panel_summary_describes_the_visible_rows(tmp_path):
    store = EventStore(tmp_path)
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, level="ERROR")], store=store)
    assert "1 erreur(s)" in panel._summary.text


# ------------------------------------------------------------------ server supervision
def _health(*, available: bool = True, healthy: bool = True) -> RemoteHealth:
    return RemoteHealth(
        available=available,
        healthy=healthy,
        services={"n8n": "running"} if available else {},
        health={} if not available else {"n8n": "healthy" if healthy else "unhealthy"},
        error=None if healthy else "Le service n8n n'est pas sain",
    )


def test_server_snapshot_healthy_view():
    snapshot = ServerSnapshot(
        health=_health(),
        logs="n8n ready",
        history=({"sha": "abcdef1234", "status": "ok", "at": "2026-09-25 10:00:00"},),
    )
    text = monitoring.server_snapshot_text(snapshot)
    assert "Santé : ok." in text
    assert "n8n running/healthy" in text
    assert "2026-09-25 10:00:00 · ok · abcdef1" in text
    assert "n8n ready" in text
    assert snapshot.healthy is True


def test_server_snapshot_degraded_view_lists_services():
    snapshot = ServerSnapshot(health=_health(healthy=False))
    text = monitoring.server_snapshot_text(snapshot)
    assert "Santé : dégradée." in text
    assert "Diagnostic : Le service n8n n'est pas sain" in text
    assert "Aucun déploiement enregistré" in text
    assert "Logs n8n (dernières lignes) :\n—" in text


def test_server_snapshot_unavailable_server():
    health = _health(available=False, healthy=False)
    text = monitoring.server_snapshot_text(ServerSnapshot(health=health))
    assert "serveur injoignable" in text
    assert ServerSnapshot(health=health).healthy is False


def test_server_snapshot_reports_a_partial_read():
    text = monitoring.server_snapshot_text(ServerSnapshot(error="ssh: timeout"))
    assert "Lecture partielle : ssh: timeout" in text
    assert "Santé : inconnue." in text


def test_server_snapshot_without_health_is_not_healthy():
    assert ServerSnapshot().healthy is False


def test_server_snapshot_shows_history_newest_first_with_errors():
    snapshot = ServerSnapshot(
        history=(
            {"sha": "aaa1111", "status": "ok", "at": "t1"},
            {"sha": "bbb2222", "status": "error", "at": "t2", "error": "compose failed"},
        )
    )
    text = monitoring.server_snapshot_text(snapshot)
    assert text.index("t2 · error · bbb2222 · compose failed") < text.index("t1 · ok · aaa1111")


def test_server_snapshot_omits_the_executions_section_when_not_read():
    text = monitoring.server_snapshot_text(ServerSnapshot(health=_health()))
    assert "Exécutions n8n" not in text


def test_server_snapshot_lists_executions_newest_first():
    status = RemoteExecutionStatus(
        supported=True,
        executions=(
            RemoteExecution("9001", "error", "Sync", "2026-09-25T10:00:00Z", finished=True),
            RemoteExecution("9002", "success", "Import", "2026-09-25T11:00:00Z", finished=True),
        ),
    )
    text = monitoring.server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    # n8n's own order is not a contract, so the display sorts on startedAt.
    assert text.index("#9002") < text.index("#9001")
    assert "2026-09-25T11:00:00Z · success · Import · #9002 · terminé" in text
    assert "2026-09-25T10:00:00Z · error · Sync · #9001 · terminé" in text


def test_server_snapshot_marks_running_and_unknown_execution_states():
    status = RemoteExecutionStatus(
        supported=True,
        executions=(
            RemoteExecution("9003", "running", "Live", "2026-09-25T12:00:00Z", finished=False),
            RemoteExecution("9004", "success", "Future", "2026-09-25T13:00:00Z", finished=None),
            RemoteExecution("9005", "error", None, None, finished=True),
        ),
    )
    text = monitoring.server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    assert "· #9003 · en cours" in text
    assert "· #9004 · état inconnu" in text
    # A summary without a workflow name or a timestamp still renders.
    assert "· — · #9005 · terminé" in text


def test_server_snapshot_reports_an_unsupported_execution_command():
    status = RemoteExecutionStatus(supported=False, error="status distant non supporté")
    text = monitoring.server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    assert "Exécutions n8n : non supporté par ce déploiement. status distant non supporté" in text


def test_server_snapshot_reports_an_empty_execution_history():
    status = RemoteExecutionStatus(supported=True)
    text = monitoring.server_snapshot_text(ServerSnapshot(health=_health(), executions=status))
    assert "Exécutions n8n : aucune exécution enregistrée." in text


def test_server_panel_renders_a_snapshot():
    with fake_gui():
        panel = monitoring.ServerPanel(FakeTk.Frame(None))
        panel.apply(ServerSnapshot(health=_health(), logs="ready"))
    assert "Santé : ok." in panel._label.text


def test_server_panel_ignores_a_snapshot_after_destroy():
    with fake_gui():
        panel = monitoring.ServerPanel(FakeTk.Frame(None))
        panel.winfo_exists = lambda: 0  # type: ignore[method-assign]
        panel.apply(ServerSnapshot(health=_health()))
    assert "Santé : inconnue." in panel._label.text


def test_prompt_server_supervision_renders_a_synchronous_read():
    statuses: list[str] = []
    with fake_gui():
        window = monitoring.prompt_server_supervision(
            FakeRoot(),
            "Demo",
            "prod.example.test",
            lambda: ServerSnapshot(health=_health(), logs="ready"),
            set_status=statuses.append,
        )
    assert window._geometry == "1000x680"
    assert window.title_text == "Supervision du serveur — Demo"
    assert window.server_panel is not None
    assert any("sain" in status for status in statuses)


def test_prompt_server_supervision_defers_to_an_async_read():
    with fake_gui():
        window = monitoring.prompt_server_supervision(
            FakeRoot(),
            "Demo",
            "prod.example.test",
            lambda: None,
        )
    assert "Santé : inconnue." in window.server_panel._label.text


def test_prompt_server_supervision_renders_a_failing_read():
    statuses: list[str] = []
    with fake_gui():
        window = monitoring.prompt_server_supervision(
            FakeRoot(),
            "Demo",
            "prod.example.test",
            lambda: (_ for _ in ()).throw(OSError("ssh exploded")),
            set_status=statuses.append,
        )
    assert "ssh exploded" in window.server_panel._label.text
    assert any("à vérifier" in status for status in statuses)


def test_prompt_server_supervision_refresh_rereads():
    reads = []

    def read() -> ServerSnapshot:
        reads.append(1)
        return ServerSnapshot(health=_health(), logs=f"run {len(reads)}")

    with fake_gui():
        window = monitoring.prompt_server_supervision(FakeRoot(), "Demo", "host", read)
        button = next(b for b in FakeTtk.Button.instances if b.text == "Actualiser")
        button.command()
    assert len(reads) == 2
    assert "run 2" in window.server_panel._label.text


def test_prompt_server_supervision_binds_escape():
    with fake_gui():
        window = monitoring.prompt_server_supervision(FakeRoot(), "Demo", "host", lambda: None)
        window._bindings["<Escape>"](None)
    assert window.destroyed


# --------------------------------------------------------- workspace detail
def _workspace(tmp_path, **kwargs):
    return Workspace(
        id=kwargs.pop("id", "ws-1"),
        name=kwargs.pop("name", "Demo"),
        workflows_dir=tmp_path / "workflows",
        port=kwargs.pop("port", 5678),
        db=DbConfig(DbMode.NONE),
        **kwargs,
    )


def test_workspace_events_matches_by_context_id(tmp_path):
    workspace = _workspace(tmp_path)
    tagged = make_event(id=1, name="start", context={"workspace_id": "ws-1"})
    other = make_event(id=2, name="start", context={"workspace_id": "ws-2"})
    assert monitoring.workspace_events(workspace, [other, tagged]) == [tagged]


def test_workspace_events_falls_back_to_the_name(tmp_path):
    workspace = _workspace(tmp_path, name="Demo")
    matching = make_event(id=1, message="Start failed for Demo: docker down")
    assert monitoring.workspace_events(workspace, [matching]) == [matching]


def test_workspace_events_ignores_a_prefixed_workspace_name(tmp_path):
    workspace = _workspace(tmp_path, name="Demo")
    other = make_event(id=1, message="Start failed for Demo2: docker down")
    assert monitoring.workspace_events(workspace, [other]) == []


def test_workspace_events_preserve_input_order(tmp_path):
    workspace = _workspace(tmp_path)
    first = make_event(id=1, message="Starting Demo")
    second = make_event(id=2, message="Stopped Demo")
    assert [event.id for event in monitoring.workspace_events(workspace, [first, second])] == [
        1,
        2,
    ]


def test_workspace_events_are_case_insensitive(tmp_path):
    workspace = _workspace(tmp_path, name="Demo")
    event = make_event(id=1, message="Published demo to prod")
    assert monitoring.workspace_events(workspace, [event]) == [event]


def test_monitoring_panel_filters_to_the_selected_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(id=1, name="start", context={"workspace_id": workspace.id}),
                make_event(id=2, name="other", context={"workspace_id": "ws-other"}),
            ],
            workspace=workspace,
        )
    assert panel.tree.get_children() == ["event-1"]
    assert panel._scope.text == "Demo"
    assert "Demo · 1 événement(s)" in panel._summary.text
    assert "1 erreur(s)" not in panel._summary.text


def test_monitoring_panel_can_return_to_global_scope(tmp_path):
    workspace = _workspace(tmp_path)
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [make_event(id=1, context={"workspace_id": workspace.id})],
            workspace=workspace,
        )
        panel.apply(
            [
                make_event(id=1, context={"workspace_id": workspace.id}),
                make_event(id=2, context={"workspace_id": "ws-other"}),
            ],
            workspace=None,
        )
    assert panel.tree.get_children() == ["event-1", "event-2"]
    # Back to the global scope: the header label is emptied because the
    # "Tous les workspaces" button already names that scope.
    assert panel._scope.text == ""
    assert "Tous les workspaces · 2 événement(s)" in panel._summary.text


def test_monitoring_panel_radio_filter_reuses_cached_events(tmp_path):
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")])
        error = next(
            button
            for button in FakeTtk.Radiobutton.instances
            if button.text == "Erreur" and button.value == "ERROR"
        )
        error.select()
        error.command()
    assert panel.tree.get_children() == ["event-2"]


def test_monitoring_panel_search_reuses_cached_events():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, message="docker up"), make_event(id=2, message="git push")])
        entry = next(widget for widget in _walk(panel) if isinstance(widget, FakeTk.Entry))
        entry.insert(0, "git")
        entry._bindings["<KeyRelease>"](None)
    assert panel.tree.get_children() == ["event-2"]


def test_monitoring_panel_clear_callback_is_optional():
    with fake_gui():
        monitoring.MonitoringPanel(FakeTk.Frame(None))
    assert all(button.text != "Tous les workspaces" for button in FakeTtk.Button.instances)
