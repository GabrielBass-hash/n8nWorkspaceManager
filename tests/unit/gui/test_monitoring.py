"""Widget-level tests for the monitoring panel (:mod:`n8n_launcher.gui.monitoring`).

What an event *means* — the row values, the detail text, the filters, the
critical-incident gate, the server snapshot — is pure logic and is tested
headless, in :mod:`tests.unit.monitoring.test_present` and
:mod:`tests.unit.workspaces.test_server_snapshot`. What is left here is the
widget contract: rows, tags, the detail pane, the rail, the keyboard copy.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from helpers import (
    FakeTk,
    FakeTtk,
    fake_monitoring_panel_bases,
    fake_server_panel_bases,
    fire,
)

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.core.subjects import PageSubject
from n8n_launcher.gui import monitoring, theme
from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.store import EventStore
from n8n_launcher.remote import RemoteHealth
from n8n_launcher.workspaces.server_snapshot import ServerSnapshot


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
    FakeTk.Canvas.instances.clear()
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


CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))
SERVER_SUBJECT = PageSubject("Serveur", ("Published", "server"))


def test_panel_narrows_the_table_to_the_active_page():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(id=1, name="workspaces.manager", message="Enabled CI for Demo"),
                make_event(id=2, name="workspaces.manager", message="Published Demo to srv"),
            ],
            store=None,
            subject=CI_SUBJECT,
        )
    assert panel.tree.get_children() == ["event-1"]
    assert panel.visible_subject() == CI_SUBJECT
    assert "sujet : Tests CI" in panel._subject_text.text
    assert panel._subject_chip.packed
    # The caption is bounded by the table's width, so the line itself is the
    # place to look for the subject.
    assert "sujet : Tests CI" in panel._summary_text


def test_panel_keeps_the_search_query_under_a_subject():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(id=1, message="Enabled CI for Demo"),
                make_event(id=2, message="Enabled CI for Other"),
            ],
            store=None,
            subject=CI_SUBJECT,
        )
        panel._entry.insert("end", "Other")
        panel._render()
    assert panel.tree.get_children() == ["event-2"]


def test_dismissing_the_subject_restores_the_whole_log():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(id=1, message="Enabled CI for Demo"),
                make_event(id=2, message="Published Demo to srv"),
            ],
            store=None,
            subject=CI_SUBJECT,
        )
        panel.dismiss_subject()
    assert panel.tree.get_children() == ["event-1", "event-2"]
    assert panel.visible_subject() is None
    assert not panel._subject_chip.packed


def test_a_poll_does_not_undo_the_dismissal():
    # The journal re-applies the same subject on every tick: a naive
    # implementation would re-arm the filter the user just cleared.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), make_event(id=2, message="Published")], subject=CI_SUBJECT)
        panel.dismiss_subject()
        panel.apply([make_event(id=1), make_event(id=2, message="Published")], subject=CI_SUBJECT)
    assert panel.visible_subject() is None
    assert panel.tree.get_children() == ["event-1", "event-2"]


def test_clicking_another_page_re_arms_the_subject():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), make_event(id=2, message="Published")], subject=CI_SUBJECT)
        panel.dismiss_subject()
        panel.set_subject(SERVER_SUBJECT)
    assert panel.visible_subject() == SERVER_SUBJECT
    assert panel.tree.get_children() == ["event-2"]


def test_back_to_the_workspace_list_clears_the_chip():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), make_event(id=2, message="Published")], subject=CI_SUBJECT)
        panel.set_subject(None)
    assert panel.visible_subject() is None
    assert not panel._subject_chip.packed
    assert panel.tree.get_children() == ["event-1", "event-2"]


def test_the_subject_is_applied_on_open_and_never_again():
    # set_subject re-renders only when the subject changes, so a page opened
    # between two polls does not cost an extra table rebuild.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        renders: list[int] = []
        panel._render = lambda **_kwargs: renders.append(1)  # type: ignore[method-assign]
        panel.set_subject(CI_SUBJECT)
        panel.set_subject(CI_SUBJECT)
    assert len(renders) == 1


# ------------------------------------------------------------- critical gate
# ------------------------------------------------------------ download icon
def test_download_icon_draws_a_muted_glyph():
    with fake_gui():
        icon = monitoring._download_icon(FakeTk.Frame(None), command=lambda: None)
    assert icon._options["width"] == 24
    assert icon._options["cursor"] == "hand2"
    assert icon._options["takefocus"] is True
    assert len(icon.lines()) == len(monitoring._ICON_STROKES)
    assert all(line["fill"] == monitoring.TEXT_MUTED for line in icon.lines())


def test_download_icon_runs_its_command_on_click_and_from_the_keyboard():
    calls: list[str] = []
    with fake_gui():
        icon = monitoring._download_icon(FakeTk.Frame(None), command=lambda: calls.append("x"))
        for sequence in ("<Button-1>", "<Return>", "<Key-space>"):
            icon._bindings[sequence](None)
    assert calls == ["x", "x", "x"]


def test_download_icon_highlights_its_strokes_on_hover():
    with fake_gui():
        icon = monitoring._download_icon(FakeTk.Frame(None), command=lambda: None)
        fire(icon, "<Enter>", None)
        hovered = icon.lines()
        fire(icon, "<Leave>", None)
        left = icon.lines()
    assert all(line["fill"] == monitoring.TEXT_PRIMARY for line in hovered)
    assert all(line["fill"] == monitoring.TEXT_MUTED for line in left)


def test_download_icon_raises_a_tooltip_only_when_the_host_provides_one():
    hints: list[tuple[str, str]] = []
    with fake_gui():
        monitoring._download_icon(
            FakeTk.Frame(None),
            command=lambda: None,
            tooltip=lambda widget, text: hints.append((widget.__class__.__name__, text)),
        )
        monitoring._download_icon(FakeTk.Frame(None), command=lambda: None)
    assert hints == [("Canvas", "Exporter le journal (JSON)")]


# -------------------------------------------------------------------- panel
def test_panel_apply_inserts_rows_with_severity_tags():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [make_event(id=1, level="INFO"), make_event(id=2, level="CRITICAL", name="publish")],
            store=None,
        )
    assert panel.tree.get_children() == ["event-1", "event-2"]
    assert panel.tree.item("event-2")["tags"] == [
        monitoring.level_tag(make_event(id=2, level="CRITICAL", name="publish"))
    ]


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
        events = [make_event(id=1, message="docker up"), make_event(id=2, message="git push")]
        panel.apply(events, store=None, query="git")
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


def _workspace(tmp_path, **kwargs):
    return Workspace(
        id=kwargs.pop("id", "ws-1"),
        name=kwargs.pop("name", "Demo"),
        workflows_dir=tmp_path / "workflows",
        port=kwargs.pop("port", 5678),
        db=DbConfig(DbMode.NONE),
        **kwargs,
    )


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
    # Back to the global scope: the header label is emptied because the global
    # scope needs no wording — it is the default one.
    assert panel._scope.text == ""
    assert "Tous les workspaces · 2 événement(s)" in panel._summary.text


def test_monitoring_panel_search_reuses_cached_events():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, message="docker up"), make_event(id=2, message="git push")])
        entry = next(widget for widget in _walk(panel) if isinstance(widget, FakeTk.Entry))
        entry.insert(0, "git")
        fire(entry, "<KeyRelease>", None)
    assert panel.tree.get_children() == ["event-2"]


def test_monitoring_panel_search_filters_by_level():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, level="INFO"), make_event(id=2, level="ERROR")])
        entry = next(widget for widget in _walk(panel) if isinstance(widget, FakeTk.Entry))
        entry.insert(0, "ERROR")
        fire(entry, "<Return>", None)
    assert panel.tree.get_children() == ["event-2"]


def test_monitoring_panel_has_a_search_field_and_no_filter_buttons():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
    entry = next(widget for widget in _walk(panel) if isinstance(widget, FakeTk.Entry))
    # The dark entry blends into the panel; a raw Tk entry would be white.
    assert entry._options["bg"] == monitoring.SURFACE
    assert entry._options["fg"] == monitoring.TEXT_PRIMARY
    assert entry._options["insertbackground"] == monitoring.TEXT_PRIMARY
    # A single field replaces the five level radio buttons.
    assert not FakeTtk.Button.instances


def test_monitoring_panel_placeholder_hides_as_soon_as_the_field_is_used():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        assert panel._placeholder._options["text"] == "Rechercher…"
        assert panel._placeholder._place_options is not None
        panel._entry.insert(0, "docker")
        panel._render()
    assert panel._placeholder._place_options is None


def test_monitoring_panel_placeholder_click_focuses_the_search_field():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        fire(panel._placeholder, "<Button-1>", None)
    assert panel._entry._focused is True


def test_monitoring_panel_export_callback_is_optional():
    with fake_gui():
        monitoring.MonitoringPanel(FakeTk.Frame(None))
    assert not FakeTk.Canvas.instances


def test_monitoring_panel_exports_through_the_download_icon():
    exports: list[str] = []
    with fake_gui():
        panel = monitoring.MonitoringPanel(
            FakeTk.Frame(None),
            on_export=lambda: exports.append("json"),
            tooltip=lambda widget, _text: None,
        )
        assert FakeTk.Canvas.instances == [panel._export_icon]
        fire(panel._export_icon, "<Button-1>", None)
    assert exports == ["json"]


def test_panel_tree_is_requested_at_its_minimum_widths():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))

    # The journal opens on the room the four columns really need, not on the
    # ~1060px a fixed set of widths used to ask for before a character was shown.
    assert panel.tree.column_widths() == dict(monitoring._JOURNAL_MINIMUMS)
    for name, width in panel.tree.column_widths().items():
        assert width == monitoring._JOURNAL_MINIMUMS[name]


def test_panel_tree_columns_follow_their_own_content():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(
                    id=1,
                    message="démarrage du workspace — docker compose up -d sur n8n-ws-3f2a terminé en 12,4 s",
                )
            ],
            store=None,
        )

    widths = panel.tree.column_widths()
    # The message column is the one that carries prose, so it is the one that
    # takes real room, whatever the pane is.
    assert widths["message"] > monitoring._JOURNAL_MINIMUMS["message"]
    assert (
        monitoring._JOURNAL_MINIMUMS["name"]
        <= widths["name"]
        < monitoring._JOURNAL_MAXIMUMS["name"]
    )
    # Every column stays inside its declared bounds, and none of them is elastic.
    for name, width in widths.items():
        assert monitoring._JOURNAL_MINIMUMS[name] <= width <= monitoring._JOURNAL_MAXIMUMS[name]
    assert all(request["stretch"] is False for request in panel.tree.column_requests().values())


def test_the_panel_follows_the_table_and_never_stretches_it():
    # The two halves of the sizing rule in one place: the table is packed without
    # a horizontal fill, so its heading row sits over its own cells, and the
    # panel asks its pane for the width of the whole table.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(
                    id=1,
                    message="démarrage du workspace terminé",
                )
            ],
            store=None,
        )

    assert "x" not in str(panel.tree._pack_options.get("fill", ""))
    assert panel._options["width"] == sum(panel.tree.column_widths().values())


def test_the_caption_and_the_detail_never_out_request_the_table():
    # A container's request is the largest of its children's, so a caption wider
    # than the table would widen the card: the columns stay exact but the table
    # stops meeting the card's outline, the slack landing on the right because the
    # table is packed ``anchor="nw"``. Both labels are therefore kept within the
    # table's own width — the caption cut with an ellipsis, the prose re-wrapped.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply(
            [
                make_event(
                    id=1,
                    message="docker compose up -d sur n8n-ws-3f2a terminé en 12,4 s " * 3,
                )
            ],
            store=None,
        )

    total = panel._fitter.total()
    # Measured the same way the panel measures, so the assertion holds whether
    # the host has font metrics or falls back to the per-character estimate.
    measure = theme.text_measure(panel, monitoring.FONT_META)
    assert measure(panel._summary._options["text"]) <= total
    assert panel._detail._options["wraplength"] == max(total - 24, 200)

    # A caption far longer than the table is cut with a trailing ellipsis.
    panel._summary_text = "Tous les workspaces · " + "· ".join(["un événement"] * 40)
    panel._bound_to_table()
    shown = panel._summary._options["text"]
    assert shown.endswith("…")
    assert measure(shown) <= total


def test_every_fit_tells_the_host_it_may_have_to_grow():
    # The journal's pane carries no weight: when the window is too narrow the
    # table is cut, and the window is the only thing that can give it room. The
    # panel renders and calls back — it never resizes anything itself.
    fitted: list[int] = []
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None), on_fitted=lambda: fitted.append(1))
        panel.apply([make_event(id=1, message="un événement")], store=None)
        panel.apply([make_event(id=2, message="un autre événement")], store=None)

    # Once per fit, so a longer message arriving later is still measured.
    assert len(fitted) == 2


def test_the_table_is_packed_after_the_detail_pane():
    # A container shorter than the sum of its children takes the shortfall from
    # the last ones packed. The table is the elastic part — it shows fewer rows in
    # a short pane — so it is packed last and the detail pane keeps its lines.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))

    assert panel.children.index(panel.tree) > panel.children.index(panel._detail)


def test_panel_tree_columns_do_not_depend_on_the_table_own_width():
    # A tree's own ``winfo_width`` is the box the fitter gave it, not a budget it
    # is allowed to grow into: reading it back would make the fit chase its own
    # output. The room comes from the *pane* the board hands the panel.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        events = [
            make_event(
                id=1,
                message="démarrage du workspace — docker compose up -d sur n8n-ws-3f2a terminé en 12,4 s",
            )
        ]
        panel.apply(events, store=None)
        first = panel.tree.column_widths()

        panel.tree._width = 1200
        panel.apply(events, store=None)

    assert panel.tree.column_widths() == first


def test_the_journal_follows_the_pane_it_is_shown_in():
    # The one elastic column of the journal is the message, so a wider pane gives
    # it exactly the room that appeared — and nothing else moves, because a
    # timestamp and a level are the same length whatever the window is.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel._width = 1200
        panel.apply([make_event(id=1, message="démarrage du workspace")], store=None)
        wide = panel.table_widths()

        panel._width = 520
        panel.apply([make_event(id=1, message="démarrage du workspace")], store=None)
        narrow = panel.table_widths()

    assert wide["message"] > narrow["message"]
    assert wide["time"] == narrow["time"]
    assert wide["level"] == narrow["level"]
    assert sum(narrow.values()) <= 520 - 2 * monitoring._PANEL_PADX


# --- the rail: a collapsed journal, and the one control that brings it back ---


def test_a_collapsed_journal_is_one_button_not_a_cut_table():
    # 46 pixels cannot show a tree, a search field and a detail pane: what is left
    # of them is worse than none of them. The rail is a single control, and the
    # journal's own widgets are simply forgotten — nothing is destroyed, so the
    # events, the filter and the selection all survive the round trip.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, message="un événement")], store=None)
        assert panel.rail is False

        panel.set_rail(True)

        assert panel.rail is True
        assert panel._rail_frame is not None
        assert panel._rail_frame.packed
        for widget in (panel._header, panel._entry, panel._summary, panel.tree, panel._detail):
            assert widget.packed is False

        panel.set_rail(False)

        assert panel.rail is False
        assert panel._rail_frame is None
        for widget in (panel._header, panel._entry, panel._summary, panel.tree, panel._detail):
            assert widget.packed is True


def test_the_rail_reopens_the_journal_through_the_host():
    # The panel never resizes itself: it is the board that owns the split, so the
    # rail's only action is a callback and the user keeps the decision.
    reopened: list[int] = []
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None), on_expand=lambda: reopened.append(1))
        panel.set_rail(True)

        strip = panel._rail_frame
        assert strip is not None
        # Frame, chevron and caption are all bound, so the whole strip is a
        # target — clicking anywhere on it brings the journal back.
        for child in (strip, *strip.children):
            fire(child, "<Button-1>", SimpleNamespace())

    assert reopened == [1, 1, 1]


def test_the_rail_keeps_the_detail_pane_below_the_table_when_it_comes_back():
    # The packing order *is* the layout rule (the elastic part last), so a
    # collapse and an expand must not quietly invert it: a six-line detail pane
    # packed after the table would absorb the shortfall of a short pane.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.set_rail(True)
        panel.set_rail(False)
        children = panel.children

    assert children.index(panel.tree) > children.index(panel._detail)
    assert children.index(panel.tree) > children.index(panel._summary)


def test_the_journal_is_the_same_panel_after_a_round_trip():
    # The rail must not cost the panel its state: a user who collapses the log to
    # read the list and opens it again expects the same rows, not a fresh one.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1, message="un événement")], store=None)
        rows = list(panel.tree._items)
        panel.set_rail(True)
        panel.set_rail(False)

    assert list(panel.tree._items) == rows


def test_a_rail_state_that_did_not_change_touches_nothing():
    # The board decides the rail on every reflow, most of the time with the answer
    # it already gave: rebuilding the strip each time would flicker.
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.set_rail(True)
        strip = panel._rail_frame
        panel.set_rail(True)

        assert panel._rail_frame is strip


def test_the_rail_carries_a_tooltip_when_the_host_provides_one():
    told: list[tuple[object, str]] = []
    with fake_gui():
        panel = monitoring.MonitoringPanel(
            FakeTk.Frame(None), tooltip=lambda widget, text: told.append((widget, text))
        )
        panel.set_rail(True)

    assert told and told[-1][1] == "Afficher le journal"


def test_a_rail_without_a_host_tooltip_is_harmless():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))

        panel.set_rail(True)

    assert panel.rail is True


def test_panel_detail_wraps_at_its_own_width():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel._detail._width = 524

    fire(panel._detail, "<Configure>", SimpleNamespace(width=524))

    # 500 = 524 - 24 of padding: the detail never wraps at a stale hard-coded px.
    assert panel._detail._options["wraplength"] == 500


def test_server_panel_label_wraps_at_its_own_width():
    with fake_gui():
        panel = monitoring.ServerPanel(FakeTk.Frame(None))
        panel.apply(ServerSnapshot(health=_health(), logs="ready"))
        panel._label._width = 628

    fire(panel._label, "<Configure>", SimpleNamespace(width=628))

    assert panel._label._options["wraplength"] == 600


# --- Ctrl+C: copy the selected event ----------------------------------------


def test_ctrl_c_is_bound_on_the_table_only():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        entry = panel._entry

    # Tk delivers a key to the focused widget, its class and the toplevel, so the
    # shortcut is bound on the table — a binding on the panel would never fire,
    # and one bound more widely would take the search field's own Ctrl+C away.
    assert "<Control-c>" in panel.tree._bindings
    assert "<Control-c>" not in panel._bindings
    assert "<Control-c>" not in entry._bindings


def test_ctrl_c_copies_the_selected_event_whole():
    event = make_event(
        id=2,
        level="ERROR",
        name="publish",
        message="git push refusé",
        context={"workspace": "demo", "port": 5678},
        exception="Traceback:\n  push failed",
    )
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1), event], store=None)
        # A click on a row selects it and refreshes the detail pane, as Tk does.
        panel.tree.selection_set("event-2")
        fire(panel.tree, "<<TreeviewSelect>>", None)
        copied: list[str] = []
        panel.clipboard_clear = lambda: copied.clear()
        panel.clipboard_append = copied.append

        result = fire(panel.tree, "<Control-c>", None)

    # Exactly one event, and the whole of it: the same text the detail pane shows.
    assert result == "break"
    assert copied == [monitoring.event_detail(event)]
    assert copied[0] == panel._detail.text
    # The other row is not part of it: one event, not the table.
    assert "premier" not in copied[0]


def test_ctrl_c_without_a_selection_leaves_the_clipboard_alone():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1)], store=None)
        copied: list[str] = []
        panel.clipboard_clear = lambda: copied.clear()
        panel.clipboard_append = copied.append

        result = fire(panel.tree, "<Control-c>", None)

    assert result == "break"
    assert copied == []


def test_ctrl_c_follows_the_current_selection():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        first, second = make_event(id=1, message="premier"), make_event(id=2, message="deuxième")
        panel.apply([first, second], store=None)
        # A clipboard that keeps every append, so both copies are visible.
        copied: list[str] = []
        panel.clipboard_clear = lambda: None
        panel.clipboard_append = copied.append

        panel.tree.selection_set("event-1")
        fire(panel.tree, "<Control-c>", None)
        panel.tree.selection_set("event-2")
        fire(panel.tree, "<Control-c>", None)

    assert [text.splitlines()[1] for text in copied] == ["premier", "deuxième"]


def test_copy_survives_a_clipboard_that_is_unavailable():
    with fake_gui():
        panel = monitoring.MonitoringPanel(FakeTk.Frame(None))
        panel.apply([make_event(id=1)], store=None)
        panel.tree.selection_set("event-1")
        panel.clipboard_clear = lambda: (_ for _ in ()).throw(RuntimeError("no clipboard"))

        assert fire(panel.tree, "<Control-c>", None) == "break"
