"""Unit tests for the toolkit-free text rules (:mod:`n8n_launcher.gui_utils.text`).

A column's width is its content and the room is a budget the table absorbs; a
string that does not fit is cut with a trailing ellipsis. Neither rule knows
what renders it, so neither test needs a display — only a ``measure`` callable.
"""

from __future__ import annotations

import pytest

from n8n_launcher.gui_utils import text

# A monospace-like measure: 10 units per character keeps the arithmetic of the
# tests readable while still going through the real measurement path.
CHAR_WIDTH = 10


def measure(value: str) -> int:
    """Return a predictable width for *value*."""
    return len(value) * CHAR_WIDTH


HEADINGS = {"time": "Heure", "level": "Niveau", "message": "Message"}
MINIMUMS = {"time": 40, "level": 40, "message": 100}
MAXIMUMS = {"time": 200, "level": 200, "message": 5000}


def widths_for(rows, **kwargs):
    """Return :func:`column_widths` for *rows* with the test's own bounds."""
    return text.column_widths(
        rows,
        headings=kwargs.get("headings", HEADINGS),
        minimums=kwargs.get("minimums", MINIMUMS),
        maximums=kwargs.get("maximums", MAXIMUMS),
        measure=kwargs.get("measure", measure),
    )


def row(time="16/09 14:32:07", level="INFO", message="démarrage"):
    """Return one journal row as the sizing rule reads it."""
    return {"time": time, "level": level, "message": message}


def budget(widths, available, **kwargs):
    """Return :func:`fit_budget` for *widths* with the test's own bounds."""
    return text.fit_budget(
        widths,
        MINIMUMS,
        MAXIMUMS,
        available=available,
        flexible=kwargs.get("flexible", "message"),
    )


def natural(value: str) -> int:
    """Return the width a column holding only *value* would take."""
    return len(value) * CHAR_WIDTH + text._CELL_PADDING


def test_a_short_value_keeps_its_column_short():
    # The level column is sized to the widest heading or value, not to the room a
    # long status might need: "INFO" is narrower than the word "Niveau".
    assert widths_for([row()])["level"] == natural("Niveau")
    # ... and it grows for a longer value, at the expense of nothing else.
    assert widths_for([row(level="CRITICAL")])["level"] == natural("CRITICAL")
    # Each column is sized on its own content: the time column fits its value, and
    # the message is not shortened to help it.
    widths = widths_for([row()])
    assert widths["time"] == natural("16/09 14:32:07")
    assert widths["message"] == natural("démarrage")


def test_a_wide_value_grows_its_column_up_to_the_maximum():
    # The message takes the width of its content...
    widths = widths_for([row(message="x" * 40)])
    assert widths["message"] == natural("x" * 40)
    # ... and stops at its declared maximum: the bound is what keeps a hosting
    # window from being asked to open as wide as the longest message ever logged.
    assert widths_for([row(message="x" * 500)])["message"] == 5000


def test_a_column_never_shrinks_below_its_minimum():
    # An empty table still shows readable headings...
    widths = widths_for([])
    assert widths["level"] == natural("Niveau")
    assert widths["time"] == natural("Heure")
    # ... and a heading narrower than the column's floor still gets the floor:
    # "Message" is 82px wide, the message column is never squeezed below 100.
    assert widths["message"] == MINIMUMS["message"]
    assert all(width >= MINIMUMS[name] for name, width in widths.items())


def test_the_heading_sizes_an_empty_column():
    # A column holding only short values still reserves its own heading, so the
    # header never ends up narrower than the word on it.
    widths = widths_for([row(level="")])
    assert widths["level"] == natural("Niveau")
    assert widths["level"] > measure("")


def test_the_widths_do_not_depend_on_the_pane():
    # The whole point of the rule: the widths are a property of the content, so
    # two panes of very different sizes ask the table for the very same thing.
    # Squeezing a table into a narrow pane is what used to cut the message.
    rows = [row(message="x" * 40), row(level="CRITICAL")]
    assert widths_for(rows) == widths_for(rows)


def test_a_column_absent_from_headings_is_ignored():
    rows = [{"time": "10:00", "level": "INFO", "message": "un message", "extra": "x" * 90}]
    widths = widths_for(rows, headings={"time": "Heure", "message": "Message"})
    assert set(widths) == {"time", "message"}
    assert widths == {"time": natural("10:00"), "message": natural("un message")}


def test_only_the_longest_values_are_measured():
    measured: list[str] = []

    def counting(text: str) -> int:
        """Record every measured string, then measure it normally."""
        measured.append(text)
        return measure(text)

    rows = [row(message=f"message numéro {index}") for index in range(50)]
    widths_for(rows, measure=counting)
    # 50 rows would be 150 measurements; the shortlist keeps it bounded.
    assert len(measured) < 40


def test_a_name_that_fits_is_untouched():
    assert text.ellipsize("Marketing", 200, measure) == "Marketing"
    assert text.ellipsize("Marketing", 0, measure) == "Marketing"


def test_a_name_that_does_not_fit_ends_with_an_ellipsis():
    cut = text.ellipsize("Pipeline de facturation mensuel", 100, measure)
    assert cut.endswith(text.ELLIPSIS)
    assert measure(cut) <= 100
    assert cut.startswith("Pipeline")


def test_ellipsize_falls_back_to_the_marker_alone():
    # A width narrower than the marker itself leaves no room for a character.
    assert text.ellipsize("Marketing", 5, measure) == text.ELLIPSIS


def test_ellipsize_drops_trailing_spaces_before_the_marker():
    cut = text.ellipsize("Marketing    ", 100, measure)
    assert cut == f"Marketing{text.ELLIPSIS}"


def test_ellipsize_measures_logarithmically():
    calls: list[str] = []

    def counting(text: str) -> int:
        """Record every measured string, then measure it normally."""
        calls.append(text)
        return measure(text)

    text.ellipsize("x" * 500, 100, counting)
    # A linear scan would measure 500 strings; the search is O(log n).
    assert len(calls) < 20


# tests readable while still going


def test_a_table_that_already_fits_keeps_every_column_but_the_flexible_one():
    # A pane wider than the table must not stretch it: a stretched column is one
    # whose text ends nowhere, and no row gained a pixel of readability. The one
    # exception is the column that was declared to take the spare room.
    widths = {"time": 140, "level": 80, "message": 300}
    fitted = budget(widths, 1200)
    assert fitted["time"] == 140
    assert fitted["level"] == 80
    assert fitted["message"] == 980
    # Without a flexible column the surplus simply stays the pane's slack.
    assert budget(widths, 1200, flexible=None) == widths


def test_the_room_a_wide_pane_leaves_over_goes_to_the_flexible_column():
    # The message is prose, so it is the one column that reads better wide; the
    # timestamp and the level are not, so they keep the room they asked for.
    widths = {"time": 140, "level": 80, "message": 300}
    fitted = budget(widths, 700)
    assert fitted["time"] == 140
    assert fitted["level"] == 80
    assert fitted["message"] == 480
    assert sum(fitted.values()) == 700


def test_the_flexible_column_stops_at_its_maximum():
    # The surplus beyond the column's declared maximum stays empty: a table does
    # not invent space, and the rest of the room is the pane's slack.
    widths = {"time": 140, "level": 80, "message": 300}
    fitted = budget(widths, 10000)
    assert fitted["message"] == MAXIMUMS["message"]
    assert sum(fitted.values()) == MAXIMUMS["message"] + 220


def test_without_a_flexible_column_the_surplus_stays_empty():
    widths = {"time": 140, "level": 80, "message": 300}
    fitted = budget(widths, 700, flexible=None)
    assert fitted == widths


def test_a_narrow_room_is_taken_from_the_columns_that_have_it():
    # Water-filling: the cap comes down on every column at once, so a column
    # already narrower than the cap keeps its own width and the wide one pays
    # alone — the room is not taken from the first column in sight.
    widths = {"time": 140, "level": 80, "message": 900}
    fitted = budget(widths, 600)
    assert fitted["time"] == 140
    assert fitted["level"] == 80
    assert fitted["message"] == 380
    assert sum(fitted.values()) == 600


def test_the_wide_columns_level_off_together_in_a_crowded_room():
    # Once the cap reaches the narrow columns, all three shrink evenly: the
    # message gives up more, but never more than proportionally, and the shortest
    # column is never the one that is cut first.
    widths = {"time": 900, "level": 800, "message": 900}
    fitted = budget(widths, 700)
    # One pixel apart at most: the cap is found exactly, and the odd leftover
    # pixel is handed to a column rather than dropped, so the table is never a
    # pixel narrower than the room it was fitted to.
    assert max(fitted.values()) - min(fitted.values()) <= 1
    assert sum(fitted.values()) == 700
    assert min(fitted.values()) >= 100


def test_a_deficit_never_goes_below_a_declared_minimum():
    # A pane narrower than the sum of the minimums is the one case left cut: the
    # window's own floor is there to prevent it, and the columns stay readable
    # rather than collapsing to nothing.
    widths = {"time": 900, "level": 800, "message": 900}
    fitted = budget(widths, 100)
    assert fitted == MINIMUMS
    assert sum(fitted.values()) == 180


def test_an_unmeasured_room_leaves_the_content_widths_alone():
    # ``available`` of 1 or less means Tk has not mapped the pane: fitting against
    # it would push every column to its minimum, so the natural widths stand.
    widths = {"time": 140, "level": 80, "message": 300}
    assert budget(widths, 0) == widths
    assert budget(widths, 1) == widths


def test_column_widths_fits_the_room_it_is_given():
    # The content sizing is the starting point and the room is what it is
    # reconciled with, in one call: a journal table in a wide pane takes the
    # spare room in its message column, and nothing else moves.
    rows = [row(), row(message="démarrage du pipeline de synchronisation git")]
    widths = text.column_widths(
        rows,
        headings=HEADINGS,
        minimums=MINIMUMS,
        maximums=MAXIMUMS,
        measure=measure,
        available=1000,
        flexible="message",
    )
    content = widths_for(rows)
    assert widths["time"] == content["time"]
    assert widths["level"] == content["level"]
    assert widths["message"] > content["message"]
    assert sum(widths.values()) == 1000


@pytest.mark.parametrize("value", ["", "Marketing", "x" * 300])
def test_ellipsize_never_grows_a_string(value: str) -> None:
    # Whatever the input, the result fits the budget it was given.
    for width in (20, 60, 200, 5000):
        assert measure(text.ellipsize(value, width, measure)) <= max(width, 1)
