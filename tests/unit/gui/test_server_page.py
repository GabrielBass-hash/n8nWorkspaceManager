"""Tests for the server supervision page (gui/server_page.py).

The page replaced a modal window, so the behaviours tested in the old dialog's
test section (rendering a snapshot, a failing read, a manual refresh) live here
— through the widget, with the ``retarget`` and visibility rules a page adds.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from helpers import FakeTk, FakeTtk, fake_server_page_bases, make_workspace

from n8n_launcher.core.models import ServerConfig, Workspace
from n8n_launcher.gui import server_page
from n8n_launcher.gui.monitoring import ServerSnapshot
from n8n_launcher.gui.server_page import NO_SERVER_NOTE, SERVER_SUBJECT, ServerPage
from n8n_launcher.remote import RemoteHealth


def _workspace(tmp_path, *, deployed: bool = True):
    """Return a workspace, deployed on a server unless told otherwise."""
    workspace = make_workspace(tmp_path, "Demo", 5678)
    workspace.server = ServerConfig(
        enabled=deployed,
        host="prod.example.test" if deployed else "",
        ssh_port=22,
        user="n8n",
        key_path=None,
        base_dir="~/n8n" if deployed else "",
        n8n_port=5678,
    )
    return workspace


def _snapshot(*, healthy: bool = True) -> ServerSnapshot:
    return ServerSnapshot(
        health=RemoteHealth(available=True, healthy=healthy, services={"n8n": "running"}),
        logs="n8n ready",
        history=({"sha": "abcdef1234", "status": "ok", "at": "2026-09-25 10:00:00"},),
    )


@contextmanager
def _fake_page_gui():
    """Rebase the page classes and swap the modules they build widgets with."""
    with ExitStack() as stack:
        for module in ("server_page", "monitoring"):
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.tk", FakeTk()))
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.ttk", FakeTtk()))
        stack.enter_context(fake_server_page_bases())
        yield stack.enter_context(patch("n8n_launcher.gui.pages.tk", FakeTk()))


@contextmanager
def _page(tmp_path, *, cached=None, available=None, status=None):
    """Yield a page bound to a host, with the reads it asks for recorded.

    The fake GUI stays open for the whole ``with`` body: the page builds its
    widgets and pushes snapshots through the same objects the host calls.
    """
    reads: list[tuple] = []
    statuses: list[str] = []

    def refresh(workspace, page, *, force: bool = False) -> None:
        reads.append((workspace, force))
        page.apply_snapshot(workspace.id, _snapshot())

    with _fake_page_gui():
        page = ServerPage(
            FakeTk.Frame(None),
            source=lambda _ws: cached if cached is not None else ServerSnapshot(),
            refresh=refresh,
            available=available,
            on_status=(statuses.append if status is None else status),
        )
        page.retarget(_workspace(tmp_path))
        yield page, reads, statuses


# ------------------------------------------------------------------- subject
def test_the_page_declares_its_journal_subject() -> None:
    assert SERVER_SUBJECT.label == "Serveur"
    assert SERVER_SUBJECT.tokens == ("Serveur", "Déploiement")


# ------------------------------------------------------------------ building
def test_the_header_names_the_workspace_and_the_server(tmp_path) -> None:
    with _page(tmp_path) as (page, _reads, _statuses):
        assert "« Demo »" in page._title._options["text"]
        assert "Supervision du serveur" in page._title._options["text"]


def test_the_page_carries_a_manual_refresh_button(tmp_path) -> None:
    with _page(tmp_path) as (_shown, reads, _statuses):
        button = next(b for b in reversed(FakeTtk.Button.instances) if b.text == "Actualiser")
        button.command()
        # The button is a forced read: it must not be served from the cache.
        assert [force for _ws, force in reads] == [True]


# ------------------------------------------------------------------ lifecycle
def test_a_hidden_page_is_not_read(tmp_path) -> None:
    # The tab is a page in a shared window: reading a server nobody looks at
    # spends four SSH round trips for nothing, so a retarget while hidden only
    # serves the cache.
    with _page(tmp_path) as (page, reads, _statuses):
        page.on_hide()
        other = _workspace(tmp_path)
        other.id = "ws-other"
        page.retarget(other)

        assert reads == []


def test_showing_the_tab_asks_for_a_read(tmp_path) -> None:
    with _page(tmp_path) as (page, reads, _statuses):
        assert reads == []  # retarget on a hidden page only serves the cache
        page.on_show()
        assert [ws.id for ws, _force in reads] == [page.workspace.id]


def test_closing_the_page_refuses_every_later_render(tmp_path) -> None:
    with _page(tmp_path) as (page, _reads, _statuses):
        page.on_close()
        page.apply_snapshot(page.workspace.id, _snapshot())
        # Nothing was rendered: Tk would raise against a destroyed widget.
        assert "n8n ready" not in page.note_text()


# ------------------------------------------------------------------ retarget
def test_a_visible_page_refreshes_when_the_selection_moves(tmp_path) -> None:
    with _page(tmp_path) as (page, reads, _statuses):
        page.on_show()
        other = _workspace(tmp_path)
        other.id = "ws-other"
        other.name = "Autre"
        page.retarget(other)

        assert page.workspace is other
        # Once for the workspace the tab was opened for, once for the new one.
        assert [ws.id for ws, _force in reads] == ["ws-demo", "ws-other"]


def test_the_cache_is_shown_before_a_read_lands(tmp_path) -> None:
    with _page(tmp_path, cached=_snapshot(healthy=False)) as (page, _reads, _statuses):
        # Served from the cache with no read at all: the tab is never blank.
        assert "Santé : dégradée." in page.note_text()


def test_a_stale_snapshot_is_never_rendered(tmp_path) -> None:
    # A read takes seconds over SSH and the user can move the selection in the
    # meantime: the answer for the previous workspace is dropped.
    with _page(tmp_path) as (page, _reads, _statuses):
        previous = page.workspace.id
        other = _workspace(tmp_path)
        other.id = "ws-other"
        page.retarget(other)

        page.apply_snapshot(previous, _snapshot())

        assert "n8n ready" not in page.note_text()


# --------------------------------------------------------------- the note
def test_a_workspace_without_a_server_shows_the_note(tmp_path) -> None:
    with _page(tmp_path, available=lambda _ws: False) as (page, reads, _statuses):
        page.on_show()
        assert page.note_text() == NO_SERVER_NOTE
        assert reads == []


def test_an_unreadable_server_config_shows_the_note(tmp_path) -> None:
    def boom(_workspace):
        raise OSError("config is gone")

    with _page(tmp_path, available=boom) as (page, reads, _statuses):
        page.on_show()
        assert page.note_text() == NO_SERVER_NOTE
        assert reads == []


def test_a_retarget_to_a_deployed_workspace_leaves_the_note(tmp_path) -> None:
    reads: list[Workspace] = []
    with _fake_page_gui():
        page = ServerPage(
            FakeTk.Frame(None),
            source=lambda _ws: ServerSnapshot(),
            refresh=lambda ws, _page, *, force=False: reads.append(ws),
            available=lambda ws: ws.id != "ws-offline",
        )
        offline = _workspace(tmp_path, deployed=False)
        offline.id = "ws-offline"
        page.retarget(offline)
        assert page.note_text() == NO_SERVER_NOTE

        page.retarget(_workspace(tmp_path))
        page.on_show()

    assert page.note_text() != NO_SERVER_NOTE
    assert [ws.id for ws in reads] == [page.workspace.id]


# ------------------------------------------------------------------ rendering
def test_a_read_snapshot_is_rendered_and_reported(tmp_path) -> None:
    statuses: list[str] = []
    with _page(tmp_path, status=statuses.append) as (page, _reads, _unused):
        page.apply_snapshot(page.workspace.id, _snapshot(healthy=True))
        assert "Santé : ok." in page.note_text()
        assert statuses == ["Supervision « Demo » — sain"]

        page.apply_snapshot(page.workspace.id, _snapshot(healthy=False))
        assert statuses[-1] == "Supervision « Demo » — à vérifier"


def test_the_note_never_reads_as_a_failure(tmp_path) -> None:
    # A workspace with no server is a configuration: the text must not look
    # like a broken deployment.
    with _page(tmp_path, available=lambda _ws: False) as (page, _reads, _statuses):
        text = page.note_text()
        assert "Santé" not in text
        assert "Lecture partielle" not in text
        assert text == NO_SERVER_NOTE


def test_the_panel_survives_a_read_that_lands_after_the_tab_closed(tmp_path) -> None:
    with _page(tmp_path) as (page, _reads, _statuses):
        page.on_close()
        # The host still caches it: this must raise nothing on the way.
        page.apply_snapshot(page.workspace.id, _snapshot())


def test_the_page_module_exports_its_note() -> None:
    assert server_page.NO_SERVER_NOTE == NO_SERVER_NOTE
    assert server_page.SERVER_SUBJECT is SERVER_SUBJECT
