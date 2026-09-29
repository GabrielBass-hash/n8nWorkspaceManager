"""What an information view asks the journal to show while it is in focus.

A view is scoped in the journal by a *subject*: the label a person reads, and the
tokens the event text is matched against. Both live here, away from any view, so
that "what does the log show while this is open" is one answer for every view
rather than one per implementation.

The token match is **case-sensitive** (see
:func:`n8n_launcher.monitoring.present.matches_tokens`). The launcher's messages
are English and regularised — "Enabled CI for Foo" — so a case-sensitive match on
a short token like ``CI`` is precise where a case-insensitive one would also
sweep in every event carrying "credential" or "spécifique".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from .models import Workspace

logger = logging.getLogger(__name__)


class PageKind(StrEnum):
    """The information views a dashboard can show as a card.

    One value per view, not per workspace: a host keys its cards on this, so the
    same kind always lands on the same card rather than multiplying as the user
    walks the list.
    """

    CI = "ci"
    SERVER = "server"


@dataclass(frozen=True)
class PageSubject:
    """What the journal shows while a view is the focused card.

    ``label`` is the human name — the card's title and the journal's chip — and
    ``tokens`` are matched case-sensitively against the event text.
    """

    label: str
    tokens: tuple[str, ...]


def log_page_event(action: str, subject: PageSubject, workspace: Workspace | None) -> None:
    """Journal one user action taken on a view.

    A view filters the journal on its own subject, so the actions taken on it
    ("Ouvert", "Actualiser", "Enregistré"…) are the events that filter is
    guaranteed to find. Without them a view would filter the journal down to the
    few lines the operations behind it happen to log, which is nearly nothing.
    """
    target = f" pour « {workspace.name} »" if workspace is not None else ""
    logger.info("Page %s : %s%s", subject.label, action, target)


__all__ = ["PageKind", "PageSubject", "log_page_event"]
