"""Pages: the launcher's information views, embedded in its dashboard.

An information view (the CI pipeline tree, the server supervision) used to open
as a ``Toplevel`` on top of the launcher: a second window the user had to move,
resize and close, hiding the very journal that explains what the view is showing.
A *page* is the same view embedded in the launcher's dashboard, in a card of its
own between the workspace list and the journal, so all three are on screen and
read together (see :mod:`n8n_launcher.gui.board`).

A page is more than a repackaged widget: it is a *live* view, not a one-shot
rendering. It is retargeted when the selected workspace changes, told to start
(:meth:`Page.on_show`) and stop (:meth:`Page.on_hide`) its timers as the user
moves between cards, and told to stop them for good (:meth:`Page.on_close`) when
its card goes away. The timers are the whole reason a page cannot be a plain
frame the dock forgets — Tk would keep firing a poll against destroyed widgets.

What a page *declares* — its :class:`PageKind`, the journal
:class:`PageSubject` it filters on and the :func:`log_page_event` call that feeds
that filter — belongs to no widget and lives in
:mod:`n8n_launcher.core.subjects`, so a headless front end can name its views and
scope the journal the same way.
"""

from __future__ import annotations

from typing import Protocol

from ..core.models import Workspace
from ..core.subjects import PageKind, PageSubject, log_page_event


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


__all__ = [
    "Page",
    "PageKind",
    "PageSubject",
    "log_page_event",
]
