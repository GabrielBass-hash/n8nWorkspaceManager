"""Unit tests for the page host (:mod:`n8n_launcher.gui.pages`).

A page is a plain object satisfying the :class:`~n8n_launcher.gui.pages.Page`
protocol, so the host is exercised with a recording stub: what matters here is
the notebook bookkeeping (one tab per kind, activation, closing) and the journal
contract, not any widget.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from helpers import FakeTk, FakeTtk, make_workspace

from n8n_launcher.core.models import Workspace
from n8n_launcher.gui import pages
from n8n_launcher.gui.pages import PageHost, PageKind, PageSubject, log_page_event

CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))
SERVER_SUBJECT = PageSubject("Serveur", ("Published", "server"))


class FakePage:
    """A page that records what the host asked it to do."""

    def __init__(self, workspace: Workspace, subject: PageSubject) -> None:
        """Stand for a page of *subject* showing *workspace*."""
        self.workspace: Workspace | None = workspace
        self.subject = subject
        self.retargets: list[Workspace] = []
        self.shown = 0
        self.hidden = 0
        self.closed = 0
        self.destroy_calls = 0

    def retarget(self, workspace: Workspace) -> None:
        """Follow the list selection."""
        self.workspace = workspace
        self.retargets.append(workspace)

    def on_show(self) -> None:
        """Count a show."""
        self.shown += 1

    def on_hide(self) -> None:
        """Count a hide."""
        self.hidden += 1

    def on_close(self) -> None:
        """Count a close."""
        self.closed += 1

    def destroy(self) -> None:
        """Count a destroy, the way a widget page releases itself."""
        self.destroy_calls += 1


@pytest.fixture
def host():
    """Build a :class:`PageHost` on the fake notebook, with its activation log."""
    activated: list[FakePage | None] = []
    homes: list[object] = []
    with (
        patch("n8n_launcher.gui.pages.tk", FakeTk()),
        patch("n8n_launcher.gui.pages.ttk", FakeTtk()),
    ):
        page_host = PageHost(
            FakeTk.Frame(None),
            home_factory=lambda notebook: homes.append(notebook) or FakeTk.Frame(notebook),
            on_activate=activated.append,
        )
    return page_host, page_host.home, activated


def _open(host_tuple, kind: PageKind, workspace: Workspace, subject: PageSubject) -> FakePage:
    """Open the page of *kind* through a factory building :class:`FakePage`."""
    host_tuple[0].open(kind, workspace, lambda ws: FakePage(ws, subject))
    page = host_tuple[0].page(kind)
    assert isinstance(page, FakePage)
    return page


def test_the_home_tab_comes_first(host) -> None:
    page_host, home, activated = host
    notebook = page_host.notebook
    assert [tab["frame"] for tab in notebook._tabs] == [home]
    assert notebook._tabs[0]["text"] == pages.HOME_TITLE
    assert page_host.active is None
    assert page_host.subject is None
    assert activated == []


def test_the_home_tab_is_built_from_the_notebook(host) -> None:
    # Tk only accepts a notebook descendant as a tab, so the list card has to be
    # created from the notebook rather than re-parented into it.
    page_host, home, _ = host
    assert home is page_host.home
    assert home in page_host.notebook.children


def test_a_home_factory_is_optional() -> None:
    with (
        patch("n8n_launcher.gui.pages.tk", FakeTk()),
        patch("n8n_launcher.gui.pages.ttk", FakeTtk()),
    ):
        page_host = PageHost(FakeTk.Frame(None))
    assert page_host.notebook._tabs[0]["frame"] is page_host.home


def test_open_adds_a_tab_named_after_the_subject(host) -> None:
    page_host, home, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    page = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    assert page_host.notebook._tabs[1]["text"] == "Tests CI"
    assert [tab["frame"] for tab in page_host.notebook._tabs] == [home, page]
    assert page_host.active is page
    assert page_host.subject is CI_SUBJECT
    assert activated == [page]


def test_a_fresh_page_is_filled_for_the_workspace_it_opens_for(host) -> None:
    # A page is built empty; the host is what tells it which workspace to show.
    page_host, _, _ = host
    first = make_workspace(Path("/tmp"), "First", 5678)
    page = page_host.open(PageKind.CI, first, lambda ws: FakePage(ws, CI_SUBJECT))
    assert page.workspace is first
    assert page.retargets == [first]


def test_a_second_workspace_reuses_the_open_tab(host) -> None:
    page_host, _, activated = host
    first = make_workspace(Path("/tmp"), "First", 5678)
    second = make_workspace(Path("/tmp"), "Second", 5679)
    page = _open(host, PageKind.CI, first, CI_SUBJECT)
    page_host.open(PageKind.CI, second, lambda ws: pytest.fail("page rebuilt"))
    assert page_host.page(PageKind.CI) is page
    assert page.workspace is second
    # Opened for the first, then retargeted for the second.
    assert page.retargets == [first, second]
    assert len(page_host.notebook._tabs) == 2
    assert activated == [page, page]


def test_each_kind_gets_its_own_tab(host) -> None:
    page_host, home, _ = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(host, PageKind.SERVER, workspace, SERVER_SUBJECT)
    assert page_host.kinds() == (PageKind.CI, PageKind.SERVER)
    assert [tab["frame"] for tab in page_host.notebook._tabs] == [home, ci, server]
    assert page_host.active is server
    assert page_host.subject is SERVER_SUBJECT


def test_retarget_repoints_every_open_page(host) -> None:
    page_host, _, _ = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    other = make_workspace(Path("/tmp"), "Other", 5679)
    ci = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(host, PageKind.SERVER, workspace, SERVER_SUBJECT)
    page_host.retarget(other)
    assert ci.workspace is other
    assert server.workspace is other


def test_clicking_a_tab_activates_its_page(host) -> None:
    page_host, _, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    _open(host, PageKind.SERVER, workspace, SERVER_SUBJECT)
    activated.clear()
    page_host.notebook.select(ci)
    page_host.notebook.fire_tab_changed()
    assert activated == [ci]
    assert page_host.subject is CI_SUBJECT


def test_selecting_the_home_tab_drops_the_subject(host) -> None:
    page_host, _, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    _open(host, PageKind.CI, workspace, CI_SUBJECT)
    page_host.select(None)
    assert activated[-1] is None
    assert page_host.subject is None


def test_activating_a_page_reports_it_once(host) -> None:
    # Selecting a tab makes Tk fire its virtual event, and the host notifies
    # explicitly as well: the app must not render the journal twice for it.
    page_host, _, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    _open(host, PageKind.CI, workspace, CI_SUBJECT)
    activated.clear()
    page_host.select(PageKind.CI)
    assert activated == [page_host.page(PageKind.CI)]
    activated.clear()
    page_host.notebook.fire_tab_changed()
    assert activated == [page_host.page(PageKind.CI)]


def test_close_stops_the_page_and_returns_to_the_list(host) -> None:
    page_host, home, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    activated.clear()
    page_host.close(PageKind.CI)
    assert ci.closed == 1
    assert ci.destroy_calls == 1
    assert [tab["frame"] for tab in page_host.notebook._tabs] == [home]
    assert page_host.notebook.select() is home
    assert activated == [None]
    assert page_host.subject is None


def test_closing_a_hidden_page_keeps_the_visible_one(host) -> None:
    page_host, _, activated = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(host, PageKind.SERVER, workspace, SERVER_SUBJECT)
    activated.clear()
    page_host.close(PageKind.CI)
    assert ci.closed == 1
    assert page_host.active is server
    assert activated == [server]


def test_close_of_a_kind_that_is_not_open_does_nothing(host) -> None:
    page_host, _, activated = host
    page_host.close(PageKind.SERVER)
    assert activated == []


def test_open_after_a_close_builds_a_fresh_page(host) -> None:
    page_host, _, _ = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    first = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    page_host.close(PageKind.CI)
    second = _open(host, PageKind.CI, workspace, CI_SUBJECT)
    assert second is not first
    assert page_host.kinds() == (PageKind.CI,)


def test_the_notebook_is_packed_in_its_host(host) -> None:
    page_host, _, _ = host
    assert page_host.notebook.packed


# -------------------------------------------------------------- journalling
def test_page_actions_are_journalled(caplog: pytest.LogCaptureFixture, host) -> None:
    page_host, _, _ = host
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    with caplog.at_level(logging.INFO, logger="n8n_launcher.gui.pages"):
        _open(host, PageKind.CI, workspace, CI_SUBJECT)
        other = make_workspace(Path("/tmp"), "Other", 5679)
        page_host.open(PageKind.CI, other, lambda ws: pytest.fail("page rebuilt"))
        page_host.close(PageKind.CI)
    messages = [record.getMessage() for record in caplog.records]
    assert "Page Tests CI : ouverte pour « Demo »" in messages
    assert "Page Tests CI : ciblée sur pour « Other »" in messages
    assert "Page Tests CI : fermée pour « Other »" in messages
    # The subject tokens must reach the log the panel filters on, otherwise the
    # page would filter the journal down to nothing.
    assert all(CI_SUBJECT.tokens[0] in message for message in messages)


def test_log_page_event_without_a_workspace(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="n8n_launcher.gui.pages"):
        log_page_event("ouverte", SERVER_SUBJECT, None)
    assert caplog.records[0].getMessage() == "Page Serveur : ouverte"
