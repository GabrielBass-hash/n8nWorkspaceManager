"""Pages: the launcher's information views, and the journal subject they drive.

An information view (the CI pipeline tree, the server supervision) used to open
as a ``Toplevel`` on top of the launcher: a second window the user had to move,
resize and close, hiding the very journal that explains what the view is showing.
A *page* is the same view embedded in the launcher's dashboard, in a card of its
own between the workspace list and the journal, so all three are on screen and
read together (see :mod:`n8n_launcher.gui.board`).

A page is more than a repackaged widget:

* it declares a :class:`PageSubject` — the label the user reads and the tokens
  the journal filters on while the page is focused, so opening a page scopes the
  log to the same subject the page is about;
* it is a *live* view, not a one-shot rendering: it is retargeted when the
  selected workspace changes, told to start (:meth:`Page.on_show`) and stop
  (:meth:`Page.on_hide`) its timers as the user moves between cards, and told to
  stop them for good (:meth:`Page.on_close`) when its card goes away. The timers
  are the whole reason a page cannot be a plain frame the dock forgets — Tk would
  keep firing a poll against destroyed widgets.

The page actions are journalled (see :func:`log_page_event`): the operations
behind the pages emit almost no events of their own, so without this a page's
own history would be empty and the subject filter would have nothing to show.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..core.models import Workspace

logger = logging.getLogger(__name__)


class PageKind(StrEnum):
    """The information views that open as a card instead of a pop-up window.

    One value per view, not per workspace: :class:`~n8n_launcher.gui.board.PageDock`
    keys its cards on this, so the same kind always lands on the same card.
    """

    CI = "ci"
    SERVER = "server"


@dataclass(frozen=True)
class PageSubject:
    """What the journal shows while a page is the focused card.

    ``label`` is the human name — the card's title and the journal's chip — and
    ``tokens`` are matched **case-sensitively** against the event text (see
    :func:`n8n_launcher.gui.monitoring.matches_tokens`). The launcher's messages
    are English and regularised ("Enabled CI for Foo"), so a case-sensitive
    match is precise where a case-insensitive one would also match "credential"
    or "spécifique" on a ``ci`` token.
    """

    label: str
    tokens: tuple[str, ...]


class Page(Protocol):
    """A view embedded in the launcher's dashboard.

    Structural on purpose: a page is a ``tk.Frame`` subclass built against
    whatever ``tk`` module is in effect (the unit-test fakes substitute that
    module, and subclassing the real ``tkinter.Frame`` at import time would
    bypass the swap), exactly like ``gui.app.RowFrame``. The dock only ever calls
    the members below, so a page that has nothing to refresh simply leaves
    ``on_show``/``on_hide``/``on_close`` empty.

    The protocol is deliberately *not* a subclass of ``tk.Widget`` — a protocol
    cannot derive from a concrete class — so a card declares its page to the
    paned window as the widget it really is.
    """

    # The workspace the page currently shows; the dock sets it through
    # ``retarget``, and the app reads it to scope the journal beside the page.
    workspace: Workspace | None
    # The name and journal tokens this page asks for while it is focused.
    subject: PageSubject

    def retarget(self, workspace: Workspace) -> None:
        """Re-point the page at *workspace* (the list selection changed)."""

    def on_show(self) -> None:
        """Called when the page's card becomes the focused one."""

    def on_hide(self) -> None:
        """Called when another card takes the focus."""

    def on_close(self) -> None:
        """Called before the page's card is removed, to stop its timers."""

    def destroy(self) -> None:
        """Release the page's widgets; the dock calls it when closing a card."""


def log_page_event(action: str, subject: PageSubject, workspace: Workspace | None) -> None:
    """Journal one user action taken on a page.

    The page filters the journal on its own subject, so the actions taken on it
    ("Ouvert", "Actualiser", "Enregistré"…) are the events that filter is
    guaranteed to find. Without them a page would filter the journal down to the
    few lines the operations behind it happen to log, which is nearly nothing.
    """
    target = f" pour « {workspace.name} »" if workspace is not None else ""
    logger.info("Page %s : %s%s", subject.label, action, target)


__all__ = [
    "Page",
    "PageKind",
    "PageSubject",
    "log_page_event",
]
