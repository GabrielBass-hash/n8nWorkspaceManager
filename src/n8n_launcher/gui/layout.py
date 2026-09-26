"""Shared sizing helpers: size a table to its content, and a window to its screen.

Every table in the launcher — the event journal, the GitHub Actions runs tree,
the pipeline selection tree, the repository picker — used to declare fixed
pixel widths for its columns. Those numbers cannot be right for every host: a
column holding ``INFO`` reserved the room of a long traceback, so the column
that actually needed the space (the message) was the one Tk clipped, and the
table needed ~590 to ~1060 px before a single character was visible.

The rule implemented here is the same everywhere:

* a column is sized to the widest of its heading and its own values, within a
  declared minimum and maximum, so a column with little in it stays little;
* the width does **not** depend on the pane. A table is never squeezed into
  whatever room the window happens to leave: the pane is the user's to give
  (window width, paned sash) and the declared maximums are the bound that keeps
  a column — and the window asking for it — from growing without limit.

:class:`ColumnFitter` is the Tk half of that rule: it reads the rows back from
the widget (never from the caller's data) and pushes the widths they ask for.
Because the widths no longer depend on the widget's size, there is nothing to
refit on ``<Configure>`` — the fitter is called once per render instead, and the
table always describes what is currently on screen.

:class:`WindowFitter` closes the loop one level up. A dialog that never declares
a width opens at whatever Tk negotiates from its children, which is the sum of
the *minimum* column widths (what a table requests before its first render), so
its content is clipped. Once the rows are in, ``winfo_reqwidth`` is the real
content width — every child's own request, fitted tables included — and
:func:`content_width` bounds it to the display the window is on.

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


def _longest(rows: Sequence[Mapping[str, str]], column: str) -> list[str]:
    """Return the *column*'s longest values, the only ones worth measuring."""
    texts = [str(row.get(column) or "") for row in rows]
    return heapq.nlargest(_MEASURED_CANDIDATES, texts, key=len)


def column_widths(
    rows: Sequence[Mapping[str, str]],
    *,
    headings: Mapping[str, str],
    minimums: Mapping[str, int],
    maximums: Mapping[str, int],
    measure: Callable[[str], int],
) -> dict[str, int]:
    """Return the pixel width of every declared column of *rows*.

    Each column takes the width of its widest heading or value (plus the cell
    margin), clamped between ``minimums`` and ``maximums``. The width is a
    property of the content alone: it is deliberately *not* a share of the pane,
    because sharing made the column carrying the prose the one that got cut
    whenever the window was narrower than the content. A table that overflows
    its pane is not wrong — the user can widen the window, drag the paned sash,
    or read the whole entry in the detail pane.

    The maximums are what keep this from running away: they are the declared
    bound on how much room a column may ever ask for, and therefore how much a
    window hosting the table can be asked to open at.
    """
    widths: dict[str, int] = {}
    for name, heading in headings.items():
        widest = max((measure(text) for text in _longest(rows, name)), default=0)
        # The heading must stay readable even when its column holds nothing.
        natural = max(widest, measure(heading)) + _CELL_PADDING
        widths[name] = min(max(natural, minimums[name]), maximums[name])
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
    """Keep a ``ttk.Treeview``'s columns sized to their content.

    One instance per table: declare the columns' headings and bounds, then call
    :meth:`rows` after every render. The fitter reads the rows back from the
    widget, so the widths always describe what is on screen.

    There is no ``<Configure>`` handler: the widths are a function of the rows,
    not of the widget's size, so a resize has nothing to recompute. That is also
    why the fitter fits a table that has not been mapped yet — a dialog is sized
    to its content before it is ever shown.
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
    ) -> None:
        """Bind the fitter to *tree* and the column bounds it must respect."""
        self._tree = tree
        self._headings = dict(headings)
        self._minimums = dict(minimums)
        self._maximums = dict(maximums)
        self._measure = measure
        # ``#0`` is the tree's own label column: its content is the item's text
        # rather than a slot of ``values``, hence the separate branch in
        # ``_rows``. Its display position is always first.
        self._has_label = "#0" in self._headings
        self._value_columns = tuple(name for name in columns if name in self._headings)
        self._widths: dict[str, int] = {}
        self._applied: dict[str, int] = {}

    def widths(self) -> dict[str, int]:
        """Return the last computed widths (the state the fitter applied)."""
        return dict(self._widths)

    def rows(self) -> None:
        """Refit the columns to the rows the tree is currently showing."""
        self._widths = column_widths(
            self._rows(),
            headings=self._headings,
            minimums=self._minimums,
            maximums=self._maximums,
            measure=self._measure,
        )
        for name, width in self._widths.items():
            if self._applied.get(name) == width:
                continue
            with contextlib.suppress(Exception):
                # Every table in the launcher is left-aligned, and no column
                # stretches: each one keeps the exact width its content asked
                # for, and the room the widget has to spare stays empty rather
                # than being handed to a column that did not ask for it.
                self._tree.column(
                    name,
                    width=width,
                    minwidth=self._minimums[name],
                    stretch=False,
                    anchor="w",
                )
            self._applied[name] = width

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
        window.bind("<Configure>", self.on_resize)

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
    "ColumnFitter",
    "WindowFitter",
    "bind_wraplength",
    "column_widths",
    "content_width",
    "ellipsize",
    "screen_fraction_size",
    "screen_size",
]
