"""Shared sizing helpers: fit a table to the room it is given, and a window to its screen.

Every table in the launcher — the event journal, the GitHub Actions runs tree,
the pipeline selection tree, the repository picker — used to declare fixed
pixel widths for its columns. Those numbers cannot be right for every host: a
column holding ``INFO`` reserved the room of a long traceback, so the column
that actually needed the space (the message) was the one Tk clipped.

The rule implemented here is the same everywhere, and it is **elastic**: a
column is sized to the widest of its heading and its own values, within a
declared minimum and maximum — and the table as a whole is then fitted to the
room its pane actually has.

* **Content first.** A column takes the width of its own widest value, so a
  column with little in it stays little and nothing is reserved in advance.
* **Then the room.** :func:`fit_budget` reconciles that natural width with the
  room available: the surplus goes to the pane's slack, and a deficit is taken
  back from the columns with the most of it, never below their declared
  minimum. One column is declared ``flexible`` and absorbs what is left, up to
  its maximum — the message in a journal, a pipeline name in a tree.
* **The pane is the user's.** What is left after a fit stays empty rather than
  being handed to a column that did not ask for it, and a table that needs more
  than its pane has is compressed to its columns' minimums rather than widening
  the window; the one case left cut is a room narrower than the sum of those
  minimums, where the last column is what Tk clips.

Two consequences make the launcher responsive instead of merely content-sized:
the widths are a function of *both* the rows and the pane, so :class:`ColumnFitter`
**does** refit on ``<Configure>`` — debounced, and only when the room really
changed, so a drag cannot start a push loop; and nothing anywhere grows the
window any more, because a table that cannot be shown at its natural width is
compressed down to its minimums instead of dragging the window wider.

:class:`WindowFitter` closes the loop for the windows that are *not* a pane: a
dialog that never declares a width opens at whatever Tk negotiates from its
children, which is the sum of the *minimum* column widths, so its content is
clipped. Once the rows are in, ``winfo_reqwidth`` is the real content width —
every child's own request, fitted tables included — and :func:`content_width`
bounds it to the display the window is on.

:func:`ellipsize` applies the same "content first" idea to a single
``tk.Label``, and :func:`screen_fraction_size` gives windows that hold no table
a size that follows the display instead of a hard-coded box.
"""

from __future__ import annotations

import contextlib
import heapq
import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from tkinter import ttk

ELLIPSIS = "…"

# A Treeview draws a few pixels of margin on each side of a cell's text (and a
# little more under the heading). Without it, the last glyph of every value
# looks clipped even though the column is technically wide enough.
_CELL_PADDING = 12

# How many of the longest strings per column are actually measured. A pixel
# width follows string length closely, so the widest row is nearly always among
# the longest ones: measuring a bounded shortlist keeps a 500-row table at eight
# measurements per column instead of one per cell, and the fitter runs again on
# every keystroke of the journal's search field.
_MEASURED_CANDIDATES = 8

# Below this, wrapping text becomes a column of single letters; used as the
# floor of ``bind_wraplength`` so a collapsed pane degrades instead of vanishing.
_MIN_WRAP_LENGTH = 120

# How long a resize waits before the columns are re-fitted, in milliseconds. A
# window drag fires ``<Configure>`` continuously, and every fit measures text and
# pushes a width: coalescing the burst keeps a drag smooth, and the leading edge
# of a real resize is under 100ms away.
REFIT_DEBOUNCE_MS = 80


def _longest(rows: Sequence[Mapping[str, str]], column: str) -> list[str]:
    """Return the *column*'s longest values, the only ones worth measuring."""
    texts = [str(row.get(column) or "") for row in rows]
    return heapq.nlargest(_MEASURED_CANDIDATES, texts, key=len)


def _compress(
    widths: Mapping[str, int], minimums: Mapping[str, int], available: int
) -> dict[str, int]:
    """Return *widths* shrunk to fit *available* pixels, floor by floor.

    The shrink is a **water-filling**: a cap is lowered on every column at once
    until the table fits, so the columns that gave room are the ones that had the
    most of it and a narrow column keeps its natural width until the wide ones
    are down to its level. The cap is found by binary search because the sum is
    monotonic in the cap, which makes this exact and logarithmic in the widths
    rather than a pixel-at-a-time walk.

    Nothing ever goes below a declared minimum: when the room is narrower than
    the sum of the minimums the cap bottoms out and the table is simply cut,
    which is the one case the window's own floor is there to prevent.
    """

    def total_at(cap: int) -> int:
        return sum(max(minimums.get(name, 0), min(width, cap)) for name, width in widths.items())

    if total_at(max(widths.values(), default=0)) <= available:
        return dict(widths)
    low, high = 0, max(widths.values(), default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if total_at(middle) <= available:
            low = middle
        else:
            high = middle - 1
    result = {name: max(minimums.get(name, 0), min(width, low)) for name, width in widths.items()}
    # Water-filling leaves a remainder of fewer pixels than there are columns
    # (one at a time is not enough to raise a whole column). Those pixels go to
    # the columns with the largest remaining shortfall, so the cap is reached
    # evenly rather than on whichever column happens to come first.
    leftover = available - sum(result.values())
    for name in sorted(widths, key=lambda item: (-(widths[item] - result[item]), item)):
        if leftover <= 0:
            break
        if result[name] < widths[name]:
            result[name] += 1
            leftover -= 1
    return result


def fit_budget(
    widths: Mapping[str, int],
    minimums: Mapping[str, int],
    maximums: Mapping[str, int],
    *,
    available: int,
    flexible: str | None = None,
) -> dict[str, int]:
    """Return *widths* reconciled with the *available* room.

    A table whose content does not fit is compressed (see :func:`_compress`); a
    table that does fit is left as it is, and only the *flexible* column takes any
    of the room left over, up to its maximum. Every other column keeps the width
    its content asked for: a pane wider than the table must not stretch it,
    because a stretched column is a column whose text ends nowhere.

    A room of 1 or less means "not measured" — Tk's answer before the first
    layout — and the widths are returned untouched, so an unmeasured table stays
    on its content sizing instead of collapsing to its minimums.

    *flexible* names the one column that may grow: the message of a journal
    event, the pipeline name of a tree, the repository name of the picker. It is
    the column whose content is prose, so it is the one that benefits from the
    spare room — and the one that can be given more without inventing space for
    a column that only holds ``INFO``.
    """
    if available <= 1:
        return dict(widths)
    result = dict(widths)
    total = sum(result.values())
    if total > available:
        return _compress(result, minimums, available)
    if flexible is not None and flexible in result:
        room = maximums.get(flexible, result[flexible]) - result[flexible]
        if room > 0:
            result[flexible] += min(room, available - total)
    return result


def column_widths(
    rows: Sequence[Mapping[str, str]],
    *,
    headings: Mapping[str, str],
    minimums: Mapping[str, int],
    maximums: Mapping[str, int],
    measure: Callable[[str], int],
    available: int | None = None,
    flexible: str | None = None,
) -> dict[str, int]:
    """Return the pixel width of every declared column of *rows*.

    Each column first takes the width of its widest heading or value (plus the
    cell margin), clamped between ``minimums`` and ``maximums``. The whole table
    is then fitted to *available* pixels when that room is known: the deficit is
    taken back from the columns that have the most slack and the surplus is given
    to *flexible*. Passing no *available* keeps the pure content sizing, which is
    what a dialog that sizes itself from its content wants.

    The maximums are what keep this from running away: they are the declared
    bound on how much room a column may ever ask for.
    """
    widths: dict[str, int] = {}
    for name, heading in headings.items():
        widest = max((measure(text) for text in _longest(rows, name)), default=0)
        # The heading must stay readable even when its column holds nothing.
        natural = max(widest, measure(heading)) + _CELL_PADDING
        low = minimums.get(name, 0)
        high = max(maximums.get(name, natural), low)
        widths[name] = min(max(natural, low), high)
    if available is not None:
        return fit_budget(widths, minimums, maximums, available=available, flexible=flexible)
    return widths


def ellipsize(text: str, width: int, measure: Callable[[str], int]) -> str:
    """Return *text* cut to at most *width* pixels, with a trailing ellipsis.

    Used for the one elastic field of a row of widgets (the workspace name),
    where the neighbouring chips hold short fixed content that must never be
    clipped. A binary search over the prefix keeps the number of measurements
    logarithmic in the length of the string, so this is cheap enough to run on
    every ``<Configure>`` of every visible row.
    """
    if width <= 0 or measure(text) <= width:
        return text
    budget = width - measure(ELLIPSIS)
    if budget <= 0:
        return ELLIPSIS
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    # Trailing spaces are free: dropping them keeps the result inside the budget.
    return f"{text[:low].rstrip()}{ELLIPSIS}"


class ColumnFitter:
    """Keep a ``ttk.Treeview``'s columns — and its container — sized to its room.

    One instance per table: declare the columns' headings and bounds, then call
    :meth:`rows` after every render. The fitter reads the rows back from the
    widget, so the widths always describe what is on screen.

        The table is packed **without** a horizontal fill: a ``ttk.Treeview``
        requests the sum of its columns, so left unfilled it is exactly as wide
        as the table — the header row sits over the cells instead of stretching
        past them — and *container* is given that same width, so the widget
        holding the table asks its geometry manager for the room the table needs.
        A pane that can give it does; a pane wider than the table stretches
        nothing. That request only follows a push once the table has been
        re-asked for it: see :meth:`_invalidate_request`.


    The container is only as wide as its **widest child**, so a view that keeps a
    caption or a prose block around its table must keep them within
    :meth:`total` (see :func:`ellipsize` and :func:`wrap_at`): one label wider
    than the table widens the container, and the table then keeps its exact
    columns but stops meeting the container's outline, the slack landing on the
    right because the table is packed ``anchor="nw"``.

    The widths are a function of the rows **and** of the room, so a resize does
    have something to recompute and the fitter listens for it on the container's
    ``<Configure>``. Two guards keep that from becoming a busy loop: the refit is
    debounced (:data:`REFIT_DEBOUNCE_MS`, so a window drag is one fit and not a
    hundred), and a room that has not actually changed is ignored outright. A
    fit whose widths came out identical pushes nothing at all.
    """

    def __init__(
        self,
        tree: ttk.Treeview,
        *,
        columns: Sequence[str],
        headings: Mapping[str, str],
        minimums: Mapping[str, int],
        maximums: Mapping[str, int],
        measure: Callable[[str], int],
        container: tk.Misc | None = None,
        available: Callable[[], int] | None = None,
        flexible: str | None = None,
    ) -> None:
        """Bind the fitter to *tree* and the column bounds it must respect.

        *container* is the widget whose width must follow the table (the panel,
        the page or the dialog holding it). It is optional: a table in a window
        that already sizes itself from ``winfo_reqwidth()`` needs no help.

        *available* reports the room the table has, and is what makes the fitter
        responsive: with it, the columns are fitted to the pane instead of asking
        it to grow, and a ``<Configure>`` triggers a refit. *flexible* names the
        one column allowed to absorb the room that is left over.
        """
        self._tree = tree
        self._headings = dict(headings)
        self._minimums = dict(minimums)
        self._maximums = dict(maximums)
        self._measure = measure
        self._container = container
        self._available = available
        self._flexible = flexible
        # ``#0`` is the tree's own label column: its content is the item's text
        # rather than a slot of ``values``, hence the separate branch in
        # ``_rows``. Its display position is always first.
        self._has_label = "#0" in self._headings
        self._value_columns = tuple(name for name in columns if name in self._headings)
        self._widths: dict[str, int] = {}
        self._applied: dict[str, int] = {}
        self._applied_total = 0
        self._room = 0
        self._refit_id: str | None = None
        if available is not None:
            self._listen_resize()

    def widths(self) -> dict[str, int]:
        """Return the last computed widths (the state the fitter applied)."""
        return dict(self._widths)

    def total(self) -> int:
        """Return the width of the whole table: the sum of its column widths."""
        return sum(self._widths.values())

    def minimum_total(self) -> int:
        """Return the narrowest the table can be: the sum of its minimums.

        This is the floor a pane has to honour for the table to show all of its
        columns, which is what the dashboard's breakpoints are measured against:
        below it the last column is cut, and the honest answer is to give the
        table the room instead.
        """
        return sum(self._minimums.get(name, 0) for name in self._headings)

    def room(self) -> int:
        """Return the room the table was last fitted to (0 when unconstrained)."""
        return self._room

    def rows(self) -> None:
        """Refit the columns to the rows the tree is currently showing."""
        room = self._measure_room()
        # Remembered even when the widget could not be measured, so a later
        # ``<Configure>`` for the room it did have is recognised as a change.
        self._room = room or 0
        self._widths = column_widths(
            self._rows(),
            headings=self._headings,
            minimums=self._minimums,
            maximums=self._maximums,
            measure=self._measure,
            available=room,
            flexible=self._flexible,
        )
        changed = False
        for name, width in self._widths.items():
            if self._applied.get(name) == width:
                continue
            with contextlib.suppress(Exception):
                # Every table in the launcher is left-aligned, and no column
                # stretches on its own: each one keeps the exact width the fit
                # gave it, and the room the widget has to spare stays empty
                # rather than being handed to a column that did not ask for it.
                self._tree.column(
                    name,
                    width=width,
                    minwidth=self._minimums.get(name, 0),
                    stretch=False,
                    anchor="w",
                )
            self._applied[name] = width
            changed = True
        if changed:
            self._invalidate_request()
        self._align_container()

    # --------------------------------------------------------------- responsive
    def _listen_resize(self) -> None:
        """Refit when the container's room changes, debounced.

        The binding is *added* rather than set: a container can carry several
        independent listeners for the same sequence — this refit and the
        ``WindowFitter`` that watches a dialog for the user taking over — and a
        plain ``bind`` would replace the first one with the second, which is how a
        table inside a resizable dialog silently stopped following it.
        """
        if self._container is None:
            return
        with contextlib.suppress(Exception):
            self._container.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, _event: object = None) -> None:
        """Schedule a refit for the new room, unless the room did not change.

        Tk reports the *event's* width on the container, which includes the
        container's own padding and borders — so it is only a change detector,
        never the budget itself: :meth:`_measure_room` asks the widget what the
        table really has.
        """
        if self._refit_id is not None:
            return
        room = self._available() if self._available is not None else 0
        if room == self._room:
            return
        with contextlib.suppress(Exception):
            self._refit_id = self._tree.after(REFIT_DEBOUNCE_MS, self._refit)

    def _refit(self) -> None:
        """Run the debounced refit, unless the room came back unchanged."""
        self._refit_id = None
        if self._available is None:
            return
        room = self._available()
        if room == self._room or room <= 1:
            return
        self.rows()

    def _measure_room(self) -> int | None:
        """Return the room to fit into, or ``None`` when it is unconstrained.

        A widget that has not been mapped reports 1, which is Tk's "I do not
        know" answer: fitting against it would push every column to its minimum,
        so an unmeasured table keeps its pure content sizing until it is on
        screen and has a real room to answer with.
        """
        if self._available is None:
            return None
        room = self._available()
        return room if room > 1 else None

    def _invalidate_request(self) -> None:
        """Make an **already mapped** table ask for the widths just pushed to it.

        A ``ttk::treeview`` sizes itself from its columns, but the request it
        handed to its geometry manager is cached and a ``column -width`` push
        does not invalidate it: a table that was already on screen kept the box
        of the widths it was *built* with (its columns' minimums) forever, so
        every later push was invisible — the columns read back at their fitted
        width while the widget stayed 434px wide, which is exactly a table whose
        last column is cut with an empty hole to its right.

        Re-setting an option to the value it already holds runs the widget's
        ``Configure``, and that is what makes it re-ask its geometry manager.
        Only a real change pays for it, so a poll that finds the same rows does
        nothing.
        """
        with contextlib.suppress(Exception):
            self._tree.configure(height=self._tree.cget("height"))

    def _align_container(self) -> None:
        """Ask *container* for the width of the whole table.

        The         container's own request is what its geometry manager allocates from
        (``ttk.PanedWindow`` for the journal, a dialog's ``WindowFitter``
        through ``winfo_reqwidth()``), so this is the single place where "the
        table needs this much" turns into "give it that much". It is set on
        every fit rather than only on a change because the total is what the
        container follows, not each column.
        """
        if self._container is None:
            return
        total = self.total()
        if total == self._applied_total:
            return
        with contextlib.suppress(Exception):
            # ``Misc.configure`` takes its options as a mapping and does not
            # declare ``width``, which every real container here does have (a
            # frame, a page, a toplevel).
            self._container.configure(cnf={"width": total})
        self._applied_total = total

    def _rows(self) -> list[dict[str, str]]:
        """Return the rendered rows as ``{column: text}`` maps.

        The walk is recursive because the runs tree nests steps and pipelines
        under their job, and their labels are the widest strings of the table.
        """
        collected: list[dict[str, str]] = []
        pending = list(self._tree.get_children(""))
        while pending:
            iid = pending.pop()
            item = self._item(iid)
            if item is None:
                continue
            values = item.get("values")
            texts = values if isinstance(values, (list, tuple)) else ()
            row: dict[str, str] = {}
            if self._has_label:
                row["#0"] = str(item.get("text") or "")
            for index, name in enumerate(self._value_columns):
                row[name] = str(texts[index]) if index < len(texts) else ""
            collected.append(row)
            pending.extend(self._tree.get_children(iid))
        return collected

    def _item(self, iid: str) -> Mapping[str, object] | None:
        """Return the tree's row *iid*, or None if the widget no longer has it.

        A snapshot can be re-rendered from a background worker, so a row may
        already be gone by the time the fitter walks the tree.
        """
        try:
            return self._tree.item(iid)
        except Exception:
            return None


def wrap_at(
    label: tk.Label, width: int, *, minimum: int = _MIN_WRAP_LENGTH, padding: int = 0
) -> None:
    """Re-wrap *label*'s text at *width* pixels, right now.

    This is the computation :func:`bind_wraplength` runs on every
    ``<Configure>``, for a caller that already knows the width — a view that has
    just sized its table and must therefore re-wrap the prose around it *in the
    same pass*, before the container counts that label's request.
    """
    target = max(width - padding, minimum)
    with contextlib.suppress(Exception):
        label.config(wraplength=target)


def bind_wraplength(
    label: tk.Label, *, minimum: int = _MIN_WRAP_LENGTH, padding: int = 0
) -> tk.Label:
    """Make *label* wrap its text at its own width, and return it.

    A ``wraplength`` fixed at build time cuts the text off in a narrow pane and
    wraps it far too early in a wide one. Following the widget's width is the
    only way both stay readable, so the label is re-wrapped on every
    ``<Configure>``.
    """
    last = -1

    def on_resize(event: object = None) -> None:
        """Re-wrap the text at the label's current width."""
        nonlocal last
        width = event if isinstance(event, int) else getattr(event, "width", None)
        if not isinstance(width, int) or width <= 1:
            return
        target = max(width - padding, minimum)
        if target == last:
            return
        last = target
        wrap_at(label, width, minimum=minimum, padding=padding)

    label.bind("<Configure>", on_resize)
    return label


def bind_ellipsize(
    label: tk.Label,
    text: Callable[[], str],
    measure: Callable[[str], int],
    *,
    apply: Callable[[str], None] | None = None,
) -> tk.Label:
    """Keep *label*'s text inside its own width, re-cut on every ``<Configure>``.

    The single-label counterpart of :func:`bind_wraplength`, for a field that
    must stay on one line: the caller owns the *raw* text and this re-applies it
    cut to the widget's current width, so a value of unpredictable length — a
    workspace name in a journal header — can never out-request its own label and
    widen the card holding it. *apply* defaults to ``config(text=…)``.

    The raw text is read from *text* on every resize rather than captured, so
    the caller keeps setting it the plain way and this stays the only thing that
    knows about the ellipsis.
    """
    last = -1

    def on_resize(event: object = None) -> None:
        """Re-cut the text at the label's current width."""
        nonlocal last
        width = event if isinstance(event, int) else getattr(event, "width", None)
        if not isinstance(width, int) or width <= 1:
            return
        if width == last:
            return
        last = width
        raw = text()
        cut = ellipsize(raw, width, measure)
        with contextlib.suppress(Exception):
            if apply is None:
                label.config(text=cut)
            else:
                apply(cut)

    label.bind("<Configure>", on_resize)
    return label


def screen_size(root: tk.Misc) -> tuple[int, int]:
    """Return the pixel size of the display *root* lives on.

    A metric of one pixel is Tk's answer when the window was never mapped, and
    the unit-test fakes have no screen at all; both are reported as ``(1, 1)``
    so :func:`screen_fraction_size` takes its ``default`` instead of computing
    a one-pixel window.
    """
    try:
        return root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        return 1, 1


def screen_fraction_size(
    screen_width: int,
    screen_height: int,
    *,
    fraction: tuple[float, float],
    minimum: tuple[int, int],
    maximum: tuple[int, int],
    default: tuple[int, int],
) -> tuple[int, int]:
    """Return a ``(width, height)`` sized as a clamped fraction of the screen.

    A metric of one pixel means Tk never mapped the window, so *default* is
    returned instead — that is also the geometry used by the tests. The
    minimums are a floor for a *large* screen, never a licence to exceed a small
    one: a 800px-wide display must still get a window that fits, so the floor is
    itself capped by the screen. The fractions stay below 1, which is what keeps
    the result inside the display on the upper end.
    """
    measured = screen_width > 1 and screen_height > 1
    width = round(screen_width * fraction[0]) if measured else default[0]
    height = round(screen_height * fraction[1]) if measured else default[1]
    low_width = min(minimum[0], screen_width) if measured else minimum[0]
    low_height = min(minimum[1], screen_height) if measured else minimum[1]
    return (
        min(max(width, low_width), maximum[0]),
        min(max(height, low_height), maximum[1]),
    )


# Share of the display a content-sized window may take. It reuses the main
# window's own fraction (``gui.app.WINDOW_WIDTH_FRACTION``) so no window in the
# launcher is allowed to claim more of the screen than the launcher itself does.
DEFAULT_WIDTH_FRACTION = 0.72

# Width given to a window whose content could not be measured — Tk never mapped
# it, or the fakes have no screen. Only reachable on an exotic window manager;
# it is also the geometry the unit tests observe.
DEFAULT_CONTENT_WIDTH = 900


def content_width(
    content: int,
    *,
    screen_width: int,
    minimum: int = 0,
    fraction: float = DEFAULT_WIDTH_FRACTION,
    default: int = DEFAULT_CONTENT_WIDTH,
) -> int:
    """Return the width a window needs for *content*, bounded by its screen.

    *content* is a natural width, typically ``winfo_reqwidth()``. A screen of one
    pixel means Tk never mapped the window (or there is no screen at all, as in
    the test fakes), so *default* is returned rather than a one-pixel window.

    The two bounds pull in opposite directions and both matter: *minimum* keeps a
    content-free dialog usable (a window of buttons still needs room for its
    labels), while the fraction keeps a long prose label from opening a dialog
    wider than the display. The floor is capped by the screen so a small laptop
    still gets a window that fits.
    """
    measured = content > 1 and screen_width > 1
    width = content if measured else default
    if not measured:
        return max(width, minimum)
    low = min(minimum, screen_width)
    high = max(low, round(screen_width * fraction))
    return min(max(width, low), high)


class WindowFitter:
    """Open a window at the width its content needs, within its screen.

    A dialog that never declares a width opens at whatever Tk negotiates from
    its children, which is the sum of the *minimum* column widths — what a table
    requests before its first render — so a table's content is clipped. Once the
    rows are in, ``winfo_reqwidth`` is the real content width: every child's own
    request, fitted tables included. This applies it, bounds it to the display,
    and centres the window over *parent*.

    Later calls only ever *grow* the window, and never again once the user has
    resized it themselves:

    * a table fed asynchronously (the runs tab) can become wider than the dialog
      that opened for it, and a window that has to be widened by hand is the
      whole defect this fixes;
    * a snapshot that comes back narrower must not shrink the window under the
      user's cursor, nor undo a width they chose;
    * so the moment a ``<Configure>`` reports a width this class did not apply,
      the user has taken over and the fitting stops.
    """

    def __init__(
        self,
        window: tk.Toplevel,
        *,
        parent: tk.Misc,
        minimum: int = 0,
        fraction: float = DEFAULT_WIDTH_FRACTION,
        default: int = DEFAULT_CONTENT_WIDTH,
        height: int | None = None,
    ) -> None:
        """Bind the fitter to *window*, centred over *parent*.

        *height* overrides the window's own requested height, for a window whose
        content is taller than any sane screen wants to show (a block of container
        logs): its height then stays whatever the caller decided, and only the
        width follows the content.
        """
        self._window = window
        self._parent = parent
        self._minimum = minimum
        self._fraction = fraction
        self._default = default
        self._height = height
        self._applied = 0
        self._locked = False
        # Added, not set: a window holding a fitted table carries two listeners
        # for ``<Configure>`` — this one and the table's refit — and a plain
        # ``bind`` would drop whichever came first, which is how a table in a
        # resizable dialog stopped following the dialog.
        window.bind("<Configure>", self.on_resize, add="+")

    @property
    def locked(self) -> bool:
        """Whether the user has resized the window, ending the auto-fitting."""
        return self._locked

    def fit(self) -> None:
        """Size and centre the window on its content, unless it would shrink."""
        if self._locked:
            return
        # Positioning is best-effort: an exotic window manager, or a half-built
        # dialog, must not stop it from opening at all.
        with contextlib.suppress(Exception):
            # The children's requests — a fitted table's columns above all — are
            # only up to date once Tk has laid the dialog out.
            self._window.update_idletasks()
            width = content_width(
                int(self._window.winfo_reqwidth()),
                screen_width=screen_size(self._window)[0],
                minimum=self._minimum,
                fraction=self._fraction,
                default=self._default,
            )
            if width <= self._applied:
                return
            if self._height is not None:
                height = self._height
            else:
                height = int(self._window.winfo_reqheight())
            x = self._parent.winfo_rootx() + max((self._parent.winfo_width() - width) // 2, 0)
            y = self._parent.winfo_rooty() + max((self._parent.winfo_height() - height) // 3, 0)
            self._window.geometry(f"{width}x{height}+{x}+{y}")
            self._applied = width

    def on_resize(self, event: object = None) -> None:
        """Stop fitting once the user has resized the window themselves.

        The ``<Configure>`` this class triggers by applying a width reports back
        that same width, so only a *different* one means a human dragged an edge.
        Anything arriving before the first fit is Tk's own initial layout, which
        says nothing about the user's intent.
        """
        width = event if isinstance(event, int) else getattr(event, "width", None)
        if self._applied > 0 and isinstance(width, int) and width != self._applied:
            self._locked = True


__all__ = [
    "DEFAULT_CONTENT_WIDTH",
    "DEFAULT_WIDTH_FRACTION",
    "ELLIPSIS",
    "REFIT_DEBOUNCE_MS",
    "ColumnFitter",
    "WindowFitter",
    "bind_ellipsize",
    "bind_wraplength",
    "column_widths",
    "content_width",
    "ellipsize",
    "fit_budget",
    "screen_fraction_size",
    "screen_size",
    "wrap_at",
]
