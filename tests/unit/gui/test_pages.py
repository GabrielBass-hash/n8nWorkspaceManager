"""Unit tests for the page contract (:mod:`n8n_launcher.gui.pages`).

A page is a plain object satisfying the :class:`~n8n_launcher.gui.pages.Page`
protocol, so what matters here is the contract itself — the subject a page
declares and the journalling of its actions — not any widget. The dock that
drives those pages is exercised in ``test_board.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from helpers import make_workspace

from n8n_launcher.core.models import Workspace
from n8n_launcher.gui.pages import PageKind, PageSubject, log_page_event

CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))
SERVER_SUBJECT = PageSubject("Serveur", ("Published", "server"))


class FakePage:
    """A page that records what the dock asked it to do."""

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


def test_a_page_kind_names_its_own_card() -> None:
    # One value per view, so the dock can key its cards and a second workspace
    # lands on the card that is already open.
    assert PageKind.CI != PageKind.SERVER
    assert [kind.value for kind in PageKind] == ["ci", "server"]


def test_a_subject_carries_a_label_and_its_tokens() -> None:
    assert CI_SUBJECT.label == "Tests CI"
    assert "CI" in CI_SUBJECT.tokens


def test_log_page_event_names_the_workspace(caplog: pytest.LogCaptureFixture) -> None:
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    with caplog.at_level(logging.INFO, logger="n8n_launcher.gui.pages"):
        log_page_event("ouverte", CI_SUBJECT, workspace)
    assert caplog.records[0].getMessage() == "Page Tests CI : ouverte pour « Demo »"


def test_log_page_event_without_a_workspace(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="n8n_launcher.gui.pages"):
        log_page_event("ouverte", SERVER_SUBJECT, None)
    assert caplog.records[0].getMessage() == "Page Serveur : ouverte"


def test_a_page_action_lands_on_the_tokens_its_subject_filters(caplog) -> None:
    # The panel filters the journal on the subject's tokens, so an action taken
    # on a page is only findable if the message carries one of them.
    with caplog.at_level(logging.INFO, logger="n8n_launcher.gui.pages"):
        log_page_event("Enregistré", CI_SUBJECT, None)
    message = caplog.records[0].getMessage()
    assert all(token in message for token in ("CI",))
