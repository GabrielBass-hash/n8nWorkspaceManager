"""Unit tests for the dashboard (:mod:`n8n_launcher.gui.board`).

Two halves, tested separately because they are two rules:

* :class:`~n8n_launcher.gui.board.PageDock` owns the page lifecycle — one card per
  kind, built on first use, retargeted with the selection, shown and hidden by
  focus, closed with its timers stopped;
* :class:`~n8n_launcher.gui.board.Board` owns the *split*, which is a pure
  function of the room (asserted through :meth:`Board._plan`, so no sash has to
  move to know what the board would do) and one application of that function.

Everything runs on the fakes, so a page is a plain recording object and the
paned window is the model in ``helpers`` — a pane is what lies between its own
sash and the next one, less the chrome, exactly as the real widget lays it out.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from helpers import FakeTk, FakeTtk, fake_board_bases, make_workspace

from n8n_launcher.core.models import Workspace
from n8n_launcher.gui import board
from n8n_launcher.gui.board import Board, PageDock
from n8n_launcher.gui.pages import PageKind, PageSubject

CI_SUBJECT = PageSubject("Tests CI", ("CI", "GitHub"))
SERVER_SUBJECT = PageSubject("Serveur", ("Published", "server"))


class FakePage:
    """A page that records what the dock asked it to do."""

    def __init__(self, workspace: Workspace, subject: PageSubject) -> None:
        """Stand for a page of *subject* showing *workspace*."""
        self.workspace: Workspace | None = workspace
        self.subject = subject
        self.parent: object = None
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


def _factory(subject: PageSubject, built: list[FakePage]):
    """Return a dock factory building :class:`FakePage` of *subject*."""

    def build(workspace: Workspace, parent: object) -> FakePage:
        page = FakePage(workspace, subject)
        page.parent = parent
        built.append(page)
        return page

    return build


@pytest.fixture
def dock():
    """Build an empty dock on the fakes, with its activation log.

    The patches stay open for the whole test: a card (and therefore a page) is
    built when the dock is *used*, not when it is constructed.
    """
    activated: list[FakePage | None] = []
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        yield PageDock(FakeTk.Frame(None), on_activate=activated.append), activated


def _open(dock_tuple, kind: PageKind, workspace: Workspace, subject: PageSubject) -> FakePage:
    """Open the page of *kind* through the recording factory."""
    built: list[FakePage] = []
    dock_tuple[0].open(kind, workspace, _factory(subject, built))
    page = dock_tuple[0].page(kind)
    assert isinstance(page, FakePage)
    return page


def _build_board(**kwargs) -> Board:
    """Build a board on the fakes with two trivial columns."""
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        return Board(
            FakeTk.Frame(None),
            list_factory=lambda parent: FakeTk.Frame(parent),
            journal_factory=lambda parent: FakeTk.Frame(parent),
            **kwargs,
        )


# --------------------------------------------------------------------- the dock
def test_an_empty_dock_holds_nothing(dock) -> None:
    page_dock, activated = dock
    assert page_dock.empty
    assert page_dock.kinds() == ()
    assert page_dock.active is None
    assert page_dock.subject is None
    assert activated == []


def test_open_builds_a_card_titled_after_the_page_subject(dock) -> None:
    page_dock, activated = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    page = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    card = page_dock.card(PageKind.CI)
    assert card is not None
    assert card.title == "Tests CI"
    assert card in page_dock.pane.panes()
    assert page_dock.subject is CI_SUBJECT
    assert activated == [page]


def test_a_page_is_built_into_the_card_body(dock) -> None:
    # Tk cannot re-parent a widget, so the page must be born in the card that
    # will hold it — this is the whole reason the factory takes a parent.
    page_dock, _ = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    page = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    assert page.parent is page_dock.card(PageKind.CI).body()


def test_a_fresh_page_is_filled_for_the_workspace_it_opens_for(dock) -> None:
    page_dock, _ = dock
    first = make_workspace(Path("/tmp"), "First", 5678)
    page = page_dock.open(PageKind.CI, first, _factory(CI_SUBJECT, []))
    assert page.workspace is first
    assert page.retargets == [first]


def test_a_second_workspace_reuses_the_card(dock) -> None:
    page_dock, _ = dock
    first = make_workspace(Path("/tmp"), "First", 5678)
    second = make_workspace(Path("/tmp"), "Second", 5679)
    page = _open(dock, PageKind.CI, first, CI_SUBJECT)
    page_dock.open(PageKind.CI, second, lambda *_args: pytest.fail("page rebuilt"))
    assert page_dock.page(PageKind.CI) is page
    assert page.workspace is second
    assert page.retargets == [first, second]
    assert len(page_dock.pane.panes()) == 1


def test_each_kind_gets_its_own_card(dock) -> None:
    page_dock, _ = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    _open(dock, PageKind.SERVER, workspace, SERVER_SUBJECT)
    assert page_dock.kinds() == (PageKind.CI, PageKind.SERVER)
    assert len(page_dock.pane.panes()) == 2
    assert page_dock.subject is SERVER_SUBJECT


def test_focus_moves_the_subject_and_the_visibility(dock) -> None:
    # Every card is on screen at once, so *focus* is what tells a page whether
    # the user is looking at it — and therefore whether it may spend an API call.
    page_dock, activated = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(dock, PageKind.SERVER, workspace, SERVER_SUBJECT)
    # Opening the second card took the focus, so the first one was shown and
    # hidden again rather than left polling behind it.
    assert (ci.shown, ci.hidden) == (1, 1)
    assert (server.shown, server.hidden) == (1, 0)
    activated.clear()
    page_dock.focus(PageKind.CI)
    assert page_dock.subject is CI_SUBJECT
    assert ci.shown == 2
    assert server.hidden == 1
    assert activated == [ci]


def test_focusing_the_focused_card_notifies_nothing(dock) -> None:
    page_dock, activated = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    activated.clear()
    page_dock.focus(PageKind.CI)
    assert activated == []
    assert ci.shown == 1


def test_focusing_a_kind_that_is_not_open_does_nothing(dock) -> None:
    page_dock, activated = dock
    page_dock.focus(PageKind.SERVER)
    assert activated == []


def test_retarget_repoints_every_open_page(dock) -> None:
    page_dock, activated = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(dock, PageKind.SERVER, workspace, SERVER_SUBJECT)
    other = make_workspace(Path("/tmp"), "Other", 5679)
    activated.clear()
    page_dock.retarget(other)
    assert ci.workspace is other
    assert server.workspace is other
    # The focused page has not changed, so the host is not told again.
    assert activated == []


def test_close_stops_the_page_and_frees_its_card(dock) -> None:
    page_dock, activated = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    ci = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    activated.clear()
    page_dock.close(PageKind.CI)
    assert ci.closed == 1
    assert ci.destroy_calls == 1
    assert page_dock.empty
    assert page_dock.pane.panes() == ()
    # A closed page stops being a filter: the journal goes back to the whole log.
    assert page_dock.subject is None
    assert activated == [None]


def test_closing_the_focused_card_falls_back_to_the_other(dock) -> None:
    page_dock, _ = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    server = _open(dock, PageKind.SERVER, workspace, SERVER_SUBJECT)
    page_dock.close(PageKind.SERVER)
    assert page_dock.active is page_dock.page(PageKind.CI)
    assert page_dock.subject is CI_SUBJECT


def test_a_page_opened_while_another_is_being_focused_is_shown_itself(dock) -> None:
    # ``_notify`` coalesces instead of recursing: a host whose activation
    # handler opens a card (a page reacting to being shown) would otherwise run
    # the visibility sync inside itself, and the page it just opened would never
    # be told it is on screen.
    activated: list[FakePage | None] = []
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):

        def on_activate(page) -> None:
            activated.append(page)
            if page is not None and PageKind.SERVER not in the_dock.kinds():
                the_dock.open(PageKind.SERVER, workspace, _factory(SERVER_SUBJECT, []))

        the_dock = PageDock(FakeTk.Frame(None), on_activate=on_activate)
        ci = the_dock.open(PageKind.CI, workspace, _factory(CI_SUBJECT, []))
        server = the_dock.page(PageKind.SERVER)

    assert the_dock.kinds() == (PageKind.CI, PageKind.SERVER)
    assert the_dock.active is server
    # Both cards were shown, in that order, and the journal followed: each page
    # was published exactly once, the one behind it hidden.
    assert (ci.shown, ci.hidden) == (1, 1)
    assert server.shown == 1
    assert activated == [ci, server]


def test_closing_a_kind_that_is_not_open_does_nothing(dock) -> None:
    page_dock, activated = dock
    page_dock.close(PageKind.SERVER)
    assert activated == []


def test_open_after_a_close_builds_a_fresh_page(dock) -> None:
    page_dock, _ = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    first = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    page_dock.close(PageKind.CI)
    second = _open(dock, PageKind.CI, workspace, CI_SUBJECT)
    assert second is not first
    assert page_dock.kinds() == (PageKind.CI,)


def test_dock_actions_are_journalled(dock, caplog: pytest.LogCaptureFixture) -> None:
    page_dock, _ = dock
    workspace = make_workspace(Path("/tmp"), "Demo", 5678)
    other = make_workspace(Path("/tmp"), "Other", 5679)
    with caplog.at_level(logging.INFO, logger="n8n_launcher.core.subjects"):
        _open(dock, PageKind.CI, workspace, CI_SUBJECT)
        page_dock.open(PageKind.CI, other, lambda *_args: pytest.fail("page rebuilt"))
        page_dock.close(PageKind.CI)
    messages = [record.getMessage() for record in caplog.records]
    assert "Page Tests CI : ouverte pour « Demo »" in messages
    assert "Page Tests CI : ciblée sur pour « Other »" in messages
    assert "Page Tests CI : fermée pour « Other »" in messages


# -------------------------------------------------------------------- the board
def test_the_board_has_a_column_per_space() -> None:
    the_board = _build_board()
    panes = the_board.pane.panes()
    assert list(panes) == [the_board.list_card, the_board.journal_card]


def test_an_empty_dock_costs_no_pane() -> None:
    the_board = _build_board()
    assert the_board.dock.empty
    assert the_board.dock.pane not in the_board.pane.panes()


def test_the_dock_pane_is_inserted_before_the_journal() -> None:
    # Column order is fixed (list, dock, journal) so a sash index always means
    # the same column, whatever order the pages were opened in.
    the_board = _build_board()
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        the_board.dock.open(
            PageKind.CI,
            make_workspace(Path("/tmp"), "Demo", 5678),
            _factory(CI_SUBJECT, []),
        )
    assert list(the_board.pane.panes()) == [
        the_board.list_card,
        the_board.dock.pane,
        the_board.journal_card,
    ]


def test_the_dock_column_is_inserted_before_the_journal_when_panes_are_paths() -> None:
    # A real ``ttk.PanedWindow`` answers ``panes()`` with widget *paths*, so
    # ``self._journal in panes()`` is always false: the dock was appended *after*
    # the journal, which left the journal with the 1px remainder and handed the
    # whole room to the dock. Verified here against the path-shaped answer.
    FakeTtk.PanedWindow.paths = True
    try:
        the_board = _build_board()
        assert the_board.dock.pane not in the_board.pane.panes()

        with (
            patch("n8n_launcher.gui.board.tk", FakeTk()),
            patch("n8n_launcher.gui.board.ttk", FakeTtk()),
            fake_board_bases(),
        ):
            the_board.dock.open(
                PageKind.CI,
                make_workspace(Path("/tmp"), "Demo", 5678),
                _factory(CI_SUBJECT, []),
            )
        the_board._paned._width = 1400
        the_board.reflow()

        panes = list(the_board.pane.panes())
        assert str(the_board.list_card) == panes[0]
        assert str(the_board.dock.pane) == panes[1]
        assert str(the_board.journal_card) == panes[2]
        # Every column got the width the plan decided, not the leftover.
        assert the_board.widths == {
            "list": 616,
            "dock": board.DOCK_MINIMUM,
            "journal": board.JOURNAL_MINIMUM,
        }
    finally:
        FakeTtk.PanedWindow.paths = False


def test_the_board_proxies_the_focused_subject() -> None:
    the_board = _build_board()
    assert the_board.subject() is None
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        the_board.dock.open(
            PageKind.CI,
            make_workspace(Path("/tmp"), "Demo", 5678),
            _factory(CI_SUBJECT, []),
        )
    assert the_board.subject() is CI_SUBJECT


def test_a_wide_window_gives_the_leftover_to_the_list() -> None:
    the_board = _build_board()
    plan = the_board._plan(1400)
    assert plan == board.Split(
        list=1400 - 2 * board.PANE_CHROME - board.JOURNAL_MINIMUM,
        dock=0,
        journal=board.JOURNAL_MINIMUM,
        journal_rail=False,
    )
    assert plan.list >= board.LIST_MINIMUM


def test_an_open_page_takes_its_minimum_between_the_two() -> None:
    the_board = _build_board()
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        the_board.dock.open(
            PageKind.CI,
            make_workspace(Path("/tmp"), "Demo", 5678),
            _factory(CI_SUBJECT, []),
        )
    plan = the_board._plan(1400)
    assert plan.dock == board.DOCK_MINIMUM
    assert plan.journal == board.JOURNAL_MINIMUM
    assert plan.list == 1400 - 2 * board.PANE_CHROME - board.DOCK_MINIMUM - board.JOURNAL_MINIMUM


def test_a_narrow_window_collapses_the_journal_to_its_rail() -> None:
    # The list keeps its floor first; only when that is not enough does the
    # journal give up its width, because a rail still shows one control.
    the_board = _build_board()
    plan = the_board._plan(560)
    assert plan.journal_rail is True
    assert plan.journal == board.JOURNAL_RAIL
    assert plan.list >= board.LIST_MINIMUM


def test_the_smallest_window_the_board_accepts_shows_all_three_columns() -> None:
    # The three minimums plus the chrome is the floor the whole design rests on:
    # a window at exactly that width shows the list, the page and the journal's
    # rail whole, with nothing left to give back and nothing cut. This is the
    # width ``window_minsize`` is derived from, so the arithmetic is checked
    # here rather than assumed.
    the_board = _build_board()
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        the_board.dock.open(
            PageKind.CI,
            make_workspace(Path("/tmp"), "Demo", 5678),
            _factory(CI_SUBJECT, []),
        )
    floor = board.LIST_MINIMUM + board.DOCK_MINIMUM + board.JOURNAL_RAIL + 2 * board.PANE_CHROME
    plan = the_board._plan(floor)
    assert plan == board.Split(
        list=board.LIST_MINIMUM,
        dock=board.DOCK_MINIMUM,
        journal=board.JOURNAL_RAIL,
        journal_rail=True,
    )
    assert plan_room(plan) == floor


def plan_room(plan: board.Split) -> int:
    """Return the room a plan occupies, chrome included, dock empty or not."""
    columns = [plan.list, plan.journal] + ([plan.dock] if plan.dock else [])
    return sum(columns) + 2 * board.PANE_CHROME


def test_the_journal_rail_is_told_to_the_host() -> None:
    seen: list[bool] = []
    the_board = _build_board(on_journal_toggle=seen.append)
    the_board.pane._width = 560
    the_board.reflow()
    assert seen == [True]
    assert the_board.journal_rail is True


def test_a_wide_window_keeps_the_journal_open() -> None:
    seen: list[bool] = []
    the_board = _build_board(on_journal_toggle=seen.append)
    the_board.pane._width = 1400
    the_board.reflow()
    assert seen == []
    assert the_board.journal_rail is False


def test_toggling_the_journal_is_a_choice_the_room_does_not_undo() -> None:
    # An explicit preference outlives the width that produced it: a later reflow
    # at the same width must not collapse the journal back.
    the_board = _build_board()
    the_board.pane._width = 1400
    the_board.toggle_journal()
    assert the_board.journal_rail is True
    the_board.reflow()
    assert the_board._plan(1400).journal == board.JOURNAL_RAIL
    the_board.toggle_journal()
    assert the_board.journal_rail is False
    the_board.reflow()
    assert the_board._plan(1400).journal == board.JOURNAL_MINIMUM


def test_reflow_does_nothing_before_the_board_is_measured() -> None:
    # A paned window of one pixel means Tk has not mapped it: the widths it was
    # built with are the ones to keep.
    the_board = _build_board()
    the_board.pane._width = 1
    the_board.reflow()
    assert the_board.widths == {}


def test_reflow_moves_the_sashes_to_the_plan() -> None:
    the_board = _build_board()
    the_board.pane._width = 1400
    the_board.reflow()
    plan = the_board._plan(1400)
    # One gap between the two columns, so one sash: the list's right edge. The
    # journal is the room the split left, which it reads as a width, not a sash.
    assert the_board.pane.sashes() == [plan.list]
    assert the_board.widths["list"] == plan.list
    assert the_board.journal_pane_width() == plan.journal


def test_a_resize_is_debounced_into_one_reflow() -> None:
    # A window drag fires a burst of ``<Configure>``: one reflow, not forty.
    the_board = _build_board()
    the_board.pane.fire_configure(1400)
    the_board.pane.fire_configure(1200)
    the_board.pane.fire_configure(900)
    assert len(the_board.pane.after_callbacks) == 1
    assert the_board.widths == {}
    the_board.pane.run_after()
    assert the_board.widths["list"] == the_board._plan(900).list


def test_the_board_tolerates_a_paned_window_it_cannot_ask() -> None:
    # Every Tk call the split makes is best-effort: a widget that refuses must
    # not take the window down with it.
    the_board = _build_board()
    the_board.pane.sashpos = lambda *_args, **_kwargs: pytest.fail("sashpos must not be blind")
    the_board.pane._width = 1400
    with patch.object(type(the_board.pane), "panes", side_effect=RuntimeError("no panes")):
        the_board.reflow()


# ------------------------------------------------- the split, over a whole range
# ``_plan`` is the board's one decision, and it is pure, so it can be swept: a
# single-width assertion can only be wrong at the width it was written for, and
# the widths between two interesting cases are the ones a user actually drags
# the window through. Every test below is a property of the whole range, not of
# one room.

# The widths worth sweeping: below the floor the window cannot show the board
# whole, around each breakpoint the decision changes, and far past anything the
# content needs.
ROOMS = [0, 1, 320, 560, 604, 605, 730, 731, 1024, 1280, 1600, 1920, 3840]


def _board_with_a_page_open() -> Board:
    """Return a board whose dock holds one page, so the three columns exist."""
    the_board = _build_board()
    with (
        patch("n8n_launcher.gui.board.tk", FakeTk()),
        patch("n8n_launcher.gui.board.ttk", FakeTtk()),
        fake_board_bases(),
    ):
        the_board.dock.open(
            PageKind.CI,
            make_workspace(Path("/tmp"), "Demo", 5678),
            _factory(CI_SUBJECT, []),
        )
    return the_board


def _floor(docked: bool) -> int:
    """The narrowest room that shows every column, the journal on its rail.

    This is the width ``window_minsize`` is derived from, so it is computed from
    the same minimums rather than written out again here.
    """
    columns = [board.LIST_MINIMUM, board.JOURNAL_RAIL] + ([board.DOCK_MINIMUM] if docked else [])
    return sum(columns) + 2 * board.PANE_CHROME


def test_no_column_is_ever_squeaked_past_its_minimum() -> None:
    """A column is collapsed, never shrunk: a minimum is a floor, not a target.

    Below the floor the widths no longer add up to the room — that is the whole
    point of a minimum, and the window's own floor is there to keep the case
    unreachable. Squeezing a column to make the arithmetic fit is what produced
    a journal one pixel wide in the first place.
    """
    for docked_board in (_build_board(), _board_with_a_page_open()):
        docked = docked_board.dock.pane in docked_board.pane.panes()
        for room in ROOMS:
            plan = docked_board._plan(room)
            assert plan.list >= board.LIST_MINIMUM, f"list {plan.list} at room {room}"
            assert plan.dock in (0, board.DOCK_MINIMUM), f"dock {plan.dock} at room {room}"
            if not docked:
                assert plan.dock == 0
            assert plan.journal >= board.JOURNAL_RAIL, f"journal {plan.journal} at room {room}"


def test_a_plan_never_asks_for_less_room_than_the_window_has() -> None:
    """The columns plus the chrome always cover the room, never fall short of it.

    Falling short is the failure that matters: Tk would then stretch whatever it
    could, which is the elastic behaviour the board exists to remove.
    """
    for the_board in (_build_board(), _board_with_a_page_open()):
        for room in ROOMS:
            plan = the_board._plan(room)
            assert plan_room(plan) >= room, f"{plan_room(plan)} < room {room}: {plan}"


def test_the_room_is_shared_exactly_once_it_can_be() -> None:
    """At or above the floor the three widths and the chrome are the room, to the pixel.

    Below it the board stops and shows the floor instead, so a window that is too
    narrow is a window the user must enlarge — never one where a column silently
    disappears.
    """
    for the_board, docked in ((_build_board(), False), (_board_with_a_page_open(), True)):
        floor = _floor(docked)
        for room in ROOMS:
            plan = the_board._plan(room)
            if room >= floor:
                assert plan_room(plan) == room, f"{plan_room(plan)} != room {room}: {plan}"
            else:
                assert plan_room(plan) == floor, f"{plan_room(plan)} != floor {floor}: {plan}"


def test_the_journal_only_ever_gains_width_as_the_room_grows() -> None:
    """Widening the window never closes the journal, and never moves the dock.

    A decision that is not monotonic is a decision that can be played backwards:
    dragging the window out and back would not return the layout it started
    from, and the user would have no way to predict which column they are paying
    for. The list is deliberately *not* covered here — it takes what is left, and
    what is left jumps when the journal opens (see the two tests below).
    """
    for the_board in (_build_board(), _board_with_a_page_open()):
        plans = [the_board._plan(room) for room in sorted(ROOMS)]
        for previous, current in itertools.pairwise(plans):
            assert current.journal >= previous.journal, f"journal shrank: {previous} -> {current}"
            assert current.dock == previous.dock, f"dock moved: {previous} -> {current}"


def test_the_list_is_monotonic_on_each_side_of_the_journal_opening() -> None:
    """Within one shape of the board, the list grows with the room.

    The list is the elastic column — it is handed whatever the other two did not
    ask for — so inside a single shape it can only gain. The only place it does
    not is the boundary where the journal springs open, which is the next test.
    """
    for the_board in (_build_board(), _board_with_a_page_open()):
        plans = [the_board._plan(room) for room in sorted(ROOMS)]
        for previous, current in itertools.pairwise(plans):
            if previous.journal == current.journal:  # same shape: the list may only grow
                assert current.list >= previous.list, f"list shrank: {previous} -> {current}"


def test_the_list_absorbs_the_journals_whole_width_when_it_opens() -> None:
    """The known discontinuity, pinned: the list drops by the journal's gain.

    A rail is all-or-nothing, so the room a window drag gives the journal jumps
    by the whole difference between a rail and a column in one pixel of drag —
    and the list, being handed the remainder, drops by exactly that much less the
    pixel gained. At 603 -> 604 that is the list falling from 533 to 240 while
    the journal springs from 46 to 340.

    This is a real wart and it is a *layout* decision rather than a bug in the
    arithmetic: the room a dragged window hands out cannot be continuous across a
    shape change. It is pinned here so the trade-off is visible, and so a future
    board that shares the slack instead has to say so and change this test.
    """
    the_board = _build_board()
    rail = the_board._plan(board.LIST_MINIMUM + board.JOURNAL_MINIMUM + 2 * board.PANE_CHROME - 1)
    open_ = the_board._plan(board.LIST_MINIMUM + board.JOURNAL_MINIMUM + 2 * board.PANE_CHROME)
    assert rail.journal_rail is True and open_.journal_rail is False
    assert open_.journal - rail.journal == board.JOURNAL_MINIMUM - board.JOURNAL_RAIL
    assert rail.list - open_.list == (board.JOURNAL_MINIMUM - board.JOURNAL_RAIL) - 1


def test_the_journal_gives_up_its_width_before_the_list_is_cut() -> None:
    """The order of sacrifice, over the whole range rather than at one width.

    The journal is the first thing to lose: a rail with its download icon still
    exports the whole history, while a list clipped past its floor is a column of
    truncated names. So wherever the list is at its floor *and* the room cannot
    be shared, the journal is already on its rail.
    """
    for the_board, docked in ((_build_board(), False), (_board_with_a_page_open(), True)):
        floor = _floor(docked)
        for room in ROOMS:
            if room >= floor:
                continue  # nothing has to give: the room is shared whole
            plan = the_board._plan(room)
            assert plan.journal_rail is True, f"list cut before the rail at room {room}: {plan}"


def test_the_rail_flag_and_the_rail_width_never_disagree() -> None:
    """``journal_rail`` is the decision, ``journal`` is its width: one thing, two views.

    They are separate fields so a reflow is a value that can be asserted on, so
    they can only stay in step if every plan is checked for the agreement — a
    flag that says "rail" next to a full-width journal is how a host ends up
    hiding a column it is still laying out for.
    """
    for the_board in (_build_board(), _board_with_a_page_open()):
        for room in ROOMS:
            plan = the_board._plan(room)
            assert (plan.journal == board.JOURNAL_RAIL) is plan.journal_rail, str(plan)
            assert plan.journal in (board.JOURNAL_MINIMUM, board.JOURNAL_RAIL), str(plan)
