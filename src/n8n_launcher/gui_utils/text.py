"""Text measurement rules: a column's width is its content, a string is cut to its room.

Every table in the launcher — the event journal, the GitHub Actions runs tree,
the pipeline selection tree, the repository picker — used to declare fixed pixel
widths for its columns. Those numbers cannot be right for every host: a column
holding ``INFO`` reserved the room of a long traceback, so the column that
actually needed the space (the message) was the one that got clipped.

The rule implemented here is **content first, then the room**:

* **Content first.** A column takes the width of its own widest value, within a
  declared minimum and maximum, so a column with little in it stays little and
  nothing is reserved in advance.
* **Then the room.** :func:`fit_budget` reconciles that natural width with the
  room available: the surplus goes to the one ``flexible`` column, and a deficit
  is taken back from the columns with the most of it, never below their declared
  minimum. What is left after a fit stays empty rather than being handed to a
  column that did not ask for it.

This module is deliberately free of any toolkit. Widths are not pixels until a
view says so: :func:`column_widths` and :func:`ellipsize` take a ``measure``
callable, which is what makes the same rule reusable by any renderer.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping, Sequence

ELLIPSIS = "…"

# A table cell draws a few pixels of margin on each side of its text (and a
# little more under the heading). Without it, the last glyph of every value
# looks clipped even though the column is technically wide enough.
_CELL_PADDING = 12

# How many of the longest strings per column are actually measured. A measured
# width follows string length closely, so the widest row is nearly always among
# the longest ones: measuring a bounded shortlist keeps a 500-row table at eight
# measurements per column instead of one per cell, which matters because the
# journal refits on every keystroke of its search field.
_MEASURED_CANDIDATES = 8


def _longest(rows: Sequence[Mapping[str, str]], column: str) -> list[str]:
    """Return the *column*'s longest values, the only ones worth measuring."""
    texts = [str(row.get(column) or "") for row in rows]
    return heapq.nlargest(_MEASURED_CANDIDATES, texts, key=len)


def _compress(
    widths: Mapping[str, int], minimums: Mapping[str, int], available: int
) -> dict[str, int]:
    """Return *widths* shrunk to fit *available*, floor by floor.

    The shrink is a **water-filling**: a cap is lowered on every column at once
    until the table fits, so the columns that gave room are the ones that had the
    most of it, and a narrow column keeps its natural width until the wide ones
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
    its content asked for: a room wider than the table must not stretch it,
    because a stretched column is a column whose text ends nowhere.

    An *available* of 1 or less means "not measured" — what a widget manager
    answers before the first layout — and the widths are returned untouched, so
    an unmeasured table stays on its content sizing instead of collapsing to its
    minimums.

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
    """Return the width of every declared column of *rows*.

    Each column first takes the width of its widest heading or value (plus the
    cell margin), clamped between ``minimums`` and ``maximums``. The whole table
    is then fitted to *available* when that room is known: the deficit is taken
    back from the columns that have the most slack and the surplus is given to
    *flexible*. Passing no *available* keeps the pure content sizing, which is
    what a view that sizes itself from its content wants.

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
    """Return *text* cut to at most *width*, with a trailing ellipsis.

    Used for the one elastic field of a row of widgets (the workspace name),
    where the neighbouring chips hold short fixed content that must never be
    clipped. A binary search over the prefix keeps the number of measurements
    logarithmic in the length of the string, so this is cheap enough to run on
    every resize of every visible row.
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


__all__ = ["ELLIPSIS", "column_widths", "ellipsize", "fit_budget"]
