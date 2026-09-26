"""Shared sizing helpers: fit a table to its content, and a window to the screen.

Every table in the launcher — the event journal, the GitHub Actions runs tree,
the pipeline selection tree, the repository picker — used to declare fixed
pixel widths for its columns. Those numbers cannot be right for every host: a
column holding ``INFO`` reserved the room of a long traceback, so the column
that actually needed the space (the message) was the one Tk clipped, and the
table needed ~590 to ~1060 px before a single character was visible.

The rule implemented here is the same everywhere:

* a column is sized to the widest of its heading and its own values, within a
  declared minimum and maximum, so a column with little in it stays little;
* the room left over goes to one *flexible* column (the message, the details,
  the label column) which absorbs the surplus;
* when the pane is too narrow for even the minimums, the fixed columns give
  their slack back, so the table shrinks gracefully instead of being cut.

:class:`ColumnFitter` is the Tk half of that rule. It reads the rows back from
the widget (never from the caller's data) and refits on ``<Configure>``, so a
table always describes what it is currently showing, whatever the user does to
the window. :func:`ellipsize` applies the same "content first, elastic column
yields" idea to a single ``tk.Label``, and :func:`screen_fraction_size` gives
windows a size that follows the display instead of a hard-coded box.
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


def _longest(rows: Sequence[Mapping[str, str]], column: str) -> list[str]:
    """Return the *column*'s longest values, the only ones worth measuring."""
    texts = [str(row.get(column) or "") for row in rows]
    return heapq.nlargest(_MEASURED_CANDIDATES, texts, key=len)


def column_widths(
    rows: Sequence[Mapping[str, str]],
    *,
    available: int,
    headings: Mapping[str, str],
    minimums: Mapping[str, int],
    maximums: Mapping[str, int],
    flexible: Sequence[str],
    measure: Callable[[str], int],
) -> dict[str, int]:
    """Return the pixel width of every declared column of *rows*.

    Each fixed column takes the width of its widest heading or value (plus the
    cell margin), clamped between ``minimums`` and ``maximums``. Whatever room
    is left inside *available* is shared by the ``flexible`` columns, so the
    column that carries the prose is the one that grows; when the pane is too
    small for that content, the flexible columns are the ones that yield, and the
    fixed ones only give way once they are at their minimums. An ``available`` of
    zero or less means "no room to share yet" (a widget that has not been
    mapped): every column then keeps its content width, which is what makes an
    unmapped table request exactly the width it needs.

    The returned widths fit *available* whenever the declared minimums do, so
    Tk never has to clip a column to make room for another, and no width ever
    goes below its minimum.
    """
    elastic = [name for name in flexible if name in headings]
    widths: dict[str, int] = {}
    naturals: dict[str, int] = {}
    for name, heading in headings.items():
        widest = max((measure(text) for text in _longest(rows, name)), default=0)
        # The heading must stay readable even when its column holds nothing.
        natural = max(widest, measure(heading)) + _CELL_PADDING
        naturals[name] = min(max(natural, minimums[name]), maximums[name])
        if name not in elastic:
            widths[name] = naturals[name]

    if elastic:
        # The elastic column is never narrower than the values it holds: a dialog
        # that has not been mapped yet has no spare room to share, and this is
        # what makes it size *itself* to the content it is about to show. It is
        # never wider than its share of the pane, so it cannot starve a fixed one.
        spare = max(available, 0) - sum(widths.values())
        share = spare // len(elastic)
        widths.update({name: min(max(share, naturals[name]), maximums[name]) for name in elastic})
    _settle(widths, elastic, minimums, maximums, available)
    return {name: widths[name] for name in headings}


def _settle(
    widths: dict[str, int],
    elastic: Sequence[str],
    minimums: Mapping[str, int],
    maximums: Mapping[str, int],
    available: int,
) -> None:
    """Make *widths* add up to *available*, the elastic columns yielding first.

    Two directions are needed. A pane too small for the content takes its room
    from the elastic columns before touching a fixed one — a column holding
    ``INFO`` must not shrink because a message is long — and a pane larger than
    the content hands the surplus to the remaining columns once the elastic ones
    have reached their maximum.
    """
    if available <= 1:
        return
    deficit = sum(widths.values()) - available
    if deficit <= 0:
        _grow(widths, maximums, -deficit)
        return
    for name in elastic:
        if deficit <= 0:
            return
        given = min(widths[name] - minimums[name], deficit)
        widths[name] -= given
        deficit -= given
    _take(widths, minimums, deficit)


def _take(widths: dict[str, int], minimums: Mapping[str, int], amount: int) -> None:
    """Remove *amount* pixels from the columns above their minimum, evenly.

    Only reached once the elastic columns have given everything they can. The
    slack is taken proportionally so that no column collapses before another has
    given its share, and every share is capped by the slack of its own column:
    rounding to whole pixels must never push a width below its minimum, let
    alone turn it negative.
    """
    if amount <= 0:
        return
    slacks = {name: widths[name] - minimums[name] for name in widths}
    if sum(slacks.values()) <= 0:
        # Even the minimums overflow: the table is as narrow as it may be, and
        # Tk clips its tail. Nothing better is available at this point.
        return
    total = sum(slacks.values())
    # Every share is computed from the *original* amount and slack, so the first
    # column cannot end up paying for the rounding of the last one; what the
    # integer division leaves over is taken afterwards, from what is still free.
    left = amount
    for name, slack in slacks.items():
        share = min(round(amount * slack / total), slack)
        widths[name] -= share
        left -= share
    for name in widths:
        share = min(left, widths[name] - minimums[name])
        widths[name] -= share
        left -= share
        if left <= 0:
            return


def _grow(widths: dict[str, int], maximums: Mapping[str, int], amount: int) -> None:
    """Hand *amount* spare pixels to the columns that still have headroom.

    Reached when the elastic columns are at their maximum: the remaining columns
    then grow, in proportion to the room they were each allowed, rather than the
    first one of them absorbing a pane's worth of pixels.
    """
    if amount <= 0:
        return
    rooms = {name: maximums[name] - widths[name] for name in widths}
    rooms = {name: room for name, room in rooms.items() if room > 0}
    if not rooms:
        # Every column is at its maximum: the pane keeps a little empty space
        # rather than a column growing past the bound declared for it.
        return
    total = sum(rooms.values())
    left = amount
    for name, room in rooms.items():
        share = min(round(amount * room / total), room)
        widths[name] += share
        left -= share
    for name in rooms:
        share = min(left, maximums[name] - widths[name])
        widths[name] += share
        left -= share
        if left <= 0:
            return


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
    """Keep a ``ttk.Treeview``'s columns sized to their content.

    One instance per table: declare the columns' headings, bounds and the
    flexible one, then call :meth:`rows` after every render. The fitter reads
    the rows back from the widget, so the widths always describe what is on
    screen, and it refits itself on ``<Configure>`` — resize the window, drag
    the sash, and every column re-reads its content within its new bounds.
    """

    def __init__(
        self,
        tree: ttk.Treeview,
        *,
        columns: Sequence[str],
        headings: Mapping[str, str],
        minimums: Mapping[str, int],
        maximums: Mapping[str, int],
        flexible: str,
        measure: Callable[[str], int],
    ) -> None:
        """Bind the fitter to *tree* and the column bounds it must respect."""
        self._tree = tree
        self._headings = dict(headings)
        self._minimums = dict(minimums)
        self._maximums = dict(maximums)
        self._flexible = flexible
        self._measure = measure
        # ``#0`` is the tree's own label column: its content is the item's text
        # rather than a slot of ``values``, hence the separate branch in
        # ``_rows``. Its display position is always first.
        self._has_label = "#0" in self._headings
        self._value_columns = tuple(name for name in columns if name in self._headings)
        self._widths: dict[str, int] = {}
        self._applied: dict[str, int] = {}
        self._available = 0
        tree.bind("<Configure>", self.on_resize)

    def widths(self) -> dict[str, int]:
        """Return the last computed widths (the state the fitter applied)."""
        return dict(self._widths)

    def rows(self) -> None:
        """Refit the columns to the rows the tree is currently showing."""
        self._fit(self._tree_width())

    def on_resize(self, event: object = None) -> None:
        """Refit after the tree was resized (the ``<Configure>`` handler)."""
        width = event if isinstance(event, int) else getattr(event, "width", None)
        self._fit(width if isinstance(width, int) else self._tree_width())

    def _fit(self, available: int) -> None:
        """Push the widths that fit *available* pixels, skipping no-op pushes.

        Pushing an unchanged width is not merely wasteful: a re-layout triggered
        by the push could fire ``<Configure>`` again, and the guard is what keeps
        the two apart.
        """
        if available <= 1:
            # Not mapped yet: leave the requested widths alone, ``<Configure>``
            # will bring the real size.
            return
        self._available = available
        self._widths = column_widths(
            self._rows(),
            available=available,
            headings=self._headings,
            minimums=self._minimums,
            maximums=self._maximums,
            flexible=(self._flexible,),
            measure=self._measure,
        )
        for name, width in self._widths.items():
            if self._applied.get(name) == width:
                continue
            with contextlib.suppress(Exception):
                # Every table in the launcher is left-aligned, and ``stretch`` is
                # reserved for the flexible column: the others keep the exact
                # width their content asked for.
                self._tree.column(
                    name,
                    width=width,
                    minwidth=self._minimums[name],
                    stretch=name == self._flexible,
                    anchor="w",
                )
            self._applied[name] = width

    def _tree_width(self) -> int:
        """Return the tree's pixel width, or 0 when it is not mapped yet."""
        try:
            width = self._tree.winfo_width()
        except Exception:
            return 0
        return width if isinstance(width, int) and width > 1 else 0

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
        with contextlib.suppress(Exception):
            label.config(wraplength=target)

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


__all__ = [
    "ELLIPSIS",
    "ColumnFitter",
    "bind_wraplength",
    "column_widths",
    "ellipsize",
    "screen_fraction_size",
    "screen_size",
]
