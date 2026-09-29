"""Unit tests for the journal subjects (:mod:`n8n_launcher.core.subjects`).

A subject is what a focused view asks the journal to show: a label a person
reads, and tokens matched case-sensitively against the event text. What matters
here is that a view's own actions land on the tokens its own subject filters on
— without that, a scoped journal finds almost nothing.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace
from n8n_launcher.core.subjects import PageKind, PageSubject, log_page_event
from n8n_launcher.monitoring.events import Event
from n8n_launcher.monitoring.present import filter_events

LOGGER = "n8n_launcher.core.subjects"


def make_workspace(tmp_path: Path, name: str = "Alpha") -> Workspace:
    """Return a workspace whose folder lives under *tmp_path*."""
    return Workspace(
        id=f"ws-{name.lower()}",
        name=name,
        workflows_dir=tmp_path / name,
        port=5678,
        db=DbConfig(DbMode.MANAGED),
    )


def test_a_page_kind_names_its_own_card() -> None:
    assert PageKind.CI == "ci"
    assert PageKind.SERVER == "server"
    assert len(set(PageKind)) == len(list(PageKind))


def test_a_subject_carries_a_label_and_its_tokens() -> None:
    subject = PageSubject("Tests CI", ("CI", "GitHub"))
    assert subject.label == "Tests CI"
    assert subject.tokens == ("CI", "GitHub")
    # Frozen: one subject is shared by every consumer filtering on it, so it
    # must not be mutable behind their back.
    with pytest.raises(dataclasses.FrozenInstanceError):
        subject.label = "Autre"  # type: ignore[misc]


def test_log_page_event_names_the_workspace(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    subject = PageSubject("Tests CI", ("CI",))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_page_event("Actualisé", subject, make_workspace(tmp_path))
    assert "Tests CI : Actualisé pour « Alpha »" in caplog.text


def test_log_page_event_without_a_workspace(caplog: pytest.LogCaptureFixture) -> None:
    subject = PageSubject("Tests CI", ("CI",))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_page_event("Actualisé", subject, None)
    assert "Tests CI : Actualisé" in caplog.text
    # No workspace means no dangling "pour « »".
    assert "pour" not in caplog.text


def test_a_page_action_lands_on_the_tokens_its_subject_filters(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The journalled action must be findable by the subject that wrote it."""
    subject = PageSubject("Tests CI", ("CI", "GitHub"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        log_page_event("Sélection enregistrée", subject, make_workspace(tmp_path))
    record = caplog.records[0]
    event = Event(level="INFO", name=record.name, message=record.getMessage())
    assert filter_events([event], tokens=subject.tokens) == [event]
    # A subject that does not match must not pick it up.
    other = PageSubject("Serveur", ("Published",))
    assert filter_events([event], tokens=other.tokens) == []
