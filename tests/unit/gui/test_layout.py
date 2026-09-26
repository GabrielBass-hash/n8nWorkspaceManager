"""Unit tests for the shared layout helpers (:mod:`n8n_launcher.gui.layout`)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from helpers import FakeTk, FakeTtk

from n8n_launcher.gui import layout

# A monospace-like measure: 10px per character keeps the arithmetic of the
# tests readable while still going through the real measurement path.
CHAR_WIDTH = 10


def measure(text: str) -> int:
    """Return a predictable pixel width for *text*."""
    return len(text) * CHAR_WIDTH


HEADINGS = {"time": "Heure", "level": "Niveau", "message": "Message"}
MINIMUMS = {"time": 40, "level": 40, "message": 100}
MAXIMUMS = {"time": 200, "level": 200, "message": 5000}


def widths_for(rows, *, available, flexible="message", **kwargs):
    """Return :func:`column_widths` for *rows* with the test's own bounds."""
    return layout.column_widths(
        rows,
        available=available,
        headings=kwargs.get("headings", HEADINGS),
        minimums=kwargs.get("minimums", MINIMUMS),
        maximums=kwargs.get("maximums", MAXIMUMS),
        flexible=kwargs.get("flexible", (flexible,)),
        measure=kwargs.get("measure", measure),
    )


def row(time="16/09 14:32:07", level="INFO", message="démarrage"):
    """Return one journal row as the fitter reads it."""
    return {"time": time, "level": level, "message": message}


# --------------------------------------------------------- column_widths
def natural(text: str) -> int:
    """Return the width a column holding only *text* would take."""
    return len(text) * CHAR_WIDTH + layout._CELL_PADDING


def test_a_short_value_keeps_its_column_short():
    # The level column is sized to the widest heading or value, not to the room a
    # long status might need: "INFO" is narrower than the word "Niveau".
    assert widths_for([row()], available=600)["level"] == natural("Niveau")
    # ... and it grows for a longer value, at the message's expense.
    assert widths_for([row(level="CRITICAL")], available=600)["level"] == natural("CRITICAL")
    # The time column fits its value ("16/09 14:32:07"), the message takes the rest.
    widths = widths_for([row()], available=600)
    assert widths["time"] == natural("16/09 14:32:07")
    assert widths["message"] == 600 - widths["time"] - widths["level"]


def test_a_wide_value_grows_its_column_up_to_the_maximum():
    # The message keeps at least the width of its content, and grows into the
    # spare room of the pane.
    widths = widths_for([row(message="x" * 40)], available=1000)
    assert widths["message"] == 1000 - widths["time"] - widths["level"]
    assert widths["message"] >= natural("x" * 40)
    # In a pane large enough, the column stops at its declared maximum: a wider
    # value is cut rather than stealing the room of the other columns.
    assert widths_for([row(message="x" * 500)], available=8000)["message"] == 5000


def test_the_elastic_column_yields_before_a_fixed_one():
    # The message is far wider than the pane: it is the message that gets cut to
    # the room that is actually left, and the fixed columns keep their full
    # natural width.
    widths = widths_for([row(message="x" * 500)], available=1000)
    assert widths["message"] == 1000 - widths["time"] - widths["level"]
    assert widths["time"] == natural("16/09 14:32:07")
    assert widths["level"] == natural("Niveau")


def test_a_column_never_shrinks_below_its_minimum():
    # An empty table still shows readable headings, and the spare goes to the
    # elastic column.
    widths = widths_for([], available=600)
    assert widths["level"] == natural("Niveau")
    assert widths["time"] == natural("Heure")
    assert widths["message"] == 600 - widths["time"] - widths["level"]
    assert all(width >= MINIMUMS[name] for name, width in widths.items())


def test_the_message_takes_everything_left_over():
    widths = widths_for([row(), row(message="un message un peu plus long")], available=800)
    assert sum(widths.values()) == 800
    assert widths["message"] == 800 - widths["time"] - widths["level"]


def test_an_unmapped_table_asks_for_its_content_only():
    # ``available`` of zero means the widget was never laid out: the columns keep
    # their content width, which is what makes the dialog size itself to them.
    widths = widths_for([row(message="un message un peu plus long")], available=0)
    assert widths["message"] == natural("un message un peu plus long")
    assert widths["level"] == natural("Niveau")
    assert sum(widths.values()) < 600


def test_a_narrow_pane_shrinks_the_fixed_columns_before_the_message():
    # Not even the minimums fit: the elastic column is already at its minimum, so
    # the fixed ones give their slack back rather than letting Tk clip the tail.
    widths = widths_for([row(message="y" * 40)], available=300)
    assert widths["message"] == 100
    assert sum(widths.values()) == 300
    assert widths["time"] == 133  # 152 - 19, its proportional share of the 24 left
    assert widths["level"] == 67  # 72 - 5


def test_the_total_always_fits_a_pane_larger_than_the_minimums():
    for available in (200, 300, 500, 700, 1000, 4000):
        widths = widths_for([row(message="z" * 30), row(level="CRITICAL")], available=available)
        assert sum(widths.values()) <= max(available, 1)


def test_a_pane_narrower_than_the_minimums_keeps_them():
    # Nothing better exists: every column sits on its floor and Tk clips the tail.
    widths = widths_for([row()], available=90)
    assert widths == {"time": 40, "level": 40, "message": 100}


def test_the_flexible_column_is_the_only_one_that_stretches():
    # Two flexible columns share the spare, and a column stopped by its maximum
    # hands the remainder to the other one.
    headings = {"a": "A", "b": "B"}
    minimums = {"a": 50, "b": 50}
    maximums = {"a": 80, "b": 400}
    widths = layout.column_widths(
        [],
        available=400,
        headings=headings,
        minimums=minimums,
        maximums=maximums,
        flexible=("a", "b"),
        measure=measure,
    )
    assert widths == {"a": 80, "b": 320}
    # A shared spare below both minimums is impossible to satisfy: both keep
    # their floor and the table overflows rather than losing a column.
    tight = layout.column_widths(
        [],
        available=60,
        headings=headings,
        minimums=minimums,
        maximums=maximums,
        flexible=("a", "b"),
        measure=measure,
    )
    assert tight == {"a": 50, "b": 50}


def test_a_column_absent_from_headings_is_ignored():
    widths = widths_for([row()], available=600, flexible="time")
    assert set(widths) == set(HEADINGS)
    # "time" is elastic and bounded: it stops at its maximum and the fixed columns
    # grow into what is left, none of them dropping below their content.
    assert widths["time"] == 200
    assert widths["level"] == 78  # 72 + its share of the 226 pixels left over
    assert widths["message"] == 322
    assert sum(widths.values()) == 600
    assert widths["level"] >= natural("Niveau")


def test_only_the_longest_values_are_measured():
    measured: list[str] = []

    def counting(text: str) -> int:
        """Record every measured string, then measure it normally."""
        measured.append(text)
        return measure(text)

    rows = [row(message=f"message numéro {index}") for index in range(50)]
    layout.column_widths(
        rows,
        available=800,
        headings=HEADINGS,
        minimums=MINIMUMS,
        maximums=MAXIMUMS,
        flexible=("message",),
        measure=counting,
    )
    # 50 rows would be 150 measurements; the shortlist keeps it bounded.
    assert len(measured) < 40


# --------------------------------------------------------------- ellipsize
def test_a_name_that_fits_is_untouched():
    assert layout.ellipsize("Marketing", 200, measure) == "Marketing"
    assert layout.ellipsize("Marketing", 0, measure) == "Marketing"


def test_a_name_that_does_not_fit_ends_with_an_ellipsis():
    text = layout.ellipsize("Pipeline de facturation mensuel", 100, measure)
    assert text.endswith(layout.ELLIPSIS)
    assert measure(text) <= 100
    assert text.startswith("Pipeline")


def test_ellipsize_falls_back_to_the_marker_alone():
    # A width narrower than the marker itself leaves no room for a character.
    assert layout.ellipsize("Marketing", 5, measure) == layout.ELLIPSIS


def test_ellipsize_drops_trailing_spaces_before_the_marker():
    text = layout.ellipsize("Marketing    ", 100, measure)
    assert text == f"Marketing{layout.ELLIPSIS}"


def test_ellipsize_measures_logarithmically():
    calls: list[str] = []

    def counting(text: str) -> int:
        """Record every measured string, then measure it normally."""
        calls.append(text)
        return measure(text)

    layout.ellipsize("x" * 500, 100, counting)
    # A linear scan would measure 500 strings; the search is O(log n).
    assert len(calls) < 20


# --------------------------------------------------------- screen geometry
def test_screen_fraction_size_clamps_to_the_bounds():
    assert layout.screen_fraction_size(
        3840,
        2160,
        fraction=(0.72, 0.72),
        minimum=(1000, 460),
        maximum=(1600, 900),
        default=(1000, 600),
    ) == (1600, 900)
    assert layout.screen_fraction_size(
        1920,
        1080,
        fraction=(0.72, 0.72),
        minimum=(1000, 460),
        maximum=(1600, 900),
        default=(1000, 600),
    ) == (1382, 778)


def test_screen_fraction_size_never_exceeds_a_small_screen():
    # The minimum is a floor for a large screen, not a licence to overflow a
    # small one.
    assert layout.screen_fraction_size(
        800,
        600,
        fraction=(0.72, 0.72),
        minimum=(1000, 460),
        maximum=(1600, 900),
        default=(1000, 600),
    ) == (800, 460)


def test_screen_fraction_size_falls_back_on_an_unmapped_screen():
    assert layout.screen_fraction_size(
        1,
        1,
        fraction=(0.72, 0.72),
        minimum=(1000, 460),
        maximum=(1600, 900),
        default=(1000, 600),
    ) == (1000, 600)


def test_screen_size_reports_an_unmapped_screen_as_one_pixel():
    class NoScreen:
        """A widget whose interpreter has no screen, like a test fake."""

    assert layout.screen_size(NoScreen()) == (1, 1)
    assert layout.screen_size(SimpleNamespace(winfo_screenwidth=lambda: 800)) == (1, 1)
    root = SimpleNamespace(winfo_screenwidth=lambda: 1920, winfo_screenheight=lambda: 1080)
    assert layout.screen_size(root) == (1920, 1080)


# ----------------------------------------------------------- ColumnFitter
def make_fitter(tree=None, *, flexible="detail", **kwargs):
    """Return a fitter over a fake tree, with the runs-tree shape."""
    tree = tree or FakeTtk.Treeview(None, columns=("detail",))
    headings = kwargs.get("headings", {"#0": "Run", "detail": "Détails"})
    fitter = layout.ColumnFitter(
        tree,
        columns=kwargs.get("columns", ("detail",)),
        headings=headings,
        minimums=kwargs.get("minimums", {"#0": 100, "detail": 60}),
        maximums=kwargs.get("maximums", {"#0": 300, "detail": 3000}),
        flexible=flexible,
        measure=measure,
    )
    return tree, fitter


def test_column_fitter_sizes_the_columns_to_the_rows_it_reads_back():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="✓  build (success)", values=("success",))
    tree._width = 500

    fitter.rows()

    widths = fitter.widths()
    assert widths["#0"] == len("✓  build (success)") * CHAR_WIDTH + layout._CELL_PADDING
    assert widths["detail"] == 500 - widths["#0"]
    assert tree.column_widths() == widths


def test_column_fitter_walks_a_nested_tree():
    tree, fitter = make_fitter()
    job = tree.insert("", "end", iid="run-1", text="run", values=("queued",))
    tree.insert(job, "end", iid="job-1", text="    importer", values=("running",))
    tree._width = 600

    fitter.rows()

    # The nested row is measured like a top-level one: its indented label is the
    # widest string of the table.
    assert fitter.widths()["#0"] == natural("    importer")


def test_column_fitter_refits_on_configure():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    tree._width = 400
    fitter.rows()
    narrow = fitter.widths()["detail"]

    # Resizing the window: the flexible column absorbs the new room.
    fitter.on_resize(SimpleNamespace(width=900))
    assert fitter.widths()["detail"] == 900 - fitter.widths()["#0"]
    assert fitter.widths()["detail"] > narrow
    assert tree.column_widths()["detail"] == fitter.widths()["detail"]


def test_column_fitter_ignores_an_unmapped_tree():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))

    # ``winfo_width`` reports 1 before the first layout: nothing is pushed, so
    # the widget keeps the requested widths it was built with.
    fitter.rows()
    assert fitter.widths() == {}
    assert tree.column_widths() == {}


def test_column_fitter_does_not_push_an_unchanged_width_twice():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    tree._width = 500
    fitter.rows()
    first = dict(tree._columns)

    fitter.rows()

    # Identical options: a re-push could feed a ``<Configure>`` loop.
    assert tree._columns == first


def test_column_fitter_survives_a_row_that_vanished():
    tree, fitter = make_fitter()
    tree._width = 400

    class Vanished(FakeTtk.Treeview):
        """A tree whose rows are gone, as when a snapshot lands after a close."""

        def item(self, iid, **kwargs):  # type: ignore[override]
            raise ValueError(f"no item {iid}")

    vanished = Vanished(None, columns=("detail",))
    vanished._width = 400
    fitter._tree = vanished
    fitter.rows()
    # No rows, no crash: the columns fall back to their minimum, which holds
    # their heading readably.
    assert fitter.widths()["#0"] == 100
    assert sum(fitter.widths().values()) == 400


def test_column_fitter_marks_only_the_flexible_column_stretchable():
    tree, fitter = make_fitter(flexible="#0")
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    tree._width = 500

    fitter.rows()

    assert tree._columns["#0"]["stretch"] is True
    assert tree._columns["detail"]["stretch"] is False
    assert tree._columns["detail"]["minwidth"] == 60


# -------------------------------------------------------- bind_wraplength
def test_bind_wraplength_follows_the_label_width():
    label = FakeTk.Label(None, text="a long traceback", wraplength=480)
    layout.bind_wraplength(label, minimum=120, padding=24)

    label._bindings["<Configure>"](SimpleNamespace(width=524))

    assert label._options["wraplength"] == 500


def test_bind_wraplength_never_goes_below_the_minimum():
    label = FakeTk.Label(None, text="x", wraplength=480)
    layout.bind_wraplength(label, minimum=120, padding=24)

    label._bindings["<Configure>"](SimpleNamespace(width=40))

    assert label._options["wraplength"] == 120


def test_bind_wraplength_skips_an_unmapped_label():
    label = FakeTk.Label(None, text="x", wraplength=480)
    layout.bind_wraplength(label)

    label._bindings["<Configure>"](SimpleNamespace(width=1))

    assert label._options["wraplength"] == 480


def test_bind_wraplength_ignores_a_repeated_width():
    label = FakeTk.Label(None, text="x", wraplength=480)
    layout.bind_wraplength(label, padding=0)

    label._bindings["<Configure>"](SimpleNamespace(width=700))
    label._options["wraplength"] = -1  # simulate a redraw touching the option
    label._bindings["<Configure>"](SimpleNamespace(width=700))

    # Nothing changed, so the option is left alone.
    assert label._options["wraplength"] == -1


@pytest.mark.parametrize("text", ["", "Marketing", "x" * 300])
def test_ellipsize_never_grows_a_string(text: str) -> None:
    # Whatever the input, the result fits the budget it was given.
    for width in (20, 60, 200, 5000):
        assert measure(layout.ellipsize(text, width, measure)) <= max(width, 1)


def test_a_vanished_row_is_skipped_by_the_walk():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    tree._width = 500

    class Vanished(FakeTtk.Treeview):
        """A tree whose rows are gone, as when a snapshot lands after a close."""

        def item(self, iid, **kwargs):  # type: ignore[override]
            raise ValueError(f"no item {iid}")

    fitter._tree = Vanished(None, columns=("detail",))
    fitter._tree._items = tree._items
    fitter._tree._width = 500
    fitter.rows()

    # The rows are walked, every one of them raises, and the fit falls back to the
    # headings instead of propagating.
    assert fitter.widths()["#0"] == 100


def test_the_fitter_survives_a_tree_that_cannot_be_measured():
    _tree, fitter = make_fitter()

    class Unmeasurable(FakeTtk.Treeview):
        """A tree whose geometry is unavailable, as on a half-built dialog."""

        def winfo_width(self) -> int:
            """Fail the way a destroyed widget does."""
            raise RuntimeError("invalid command name")

    fitter._tree = Unmeasurable(None, columns=("detail",))
    fitter.rows()

    assert fitter.widths() == {}


def test_a_pane_wider_than_every_maximum_leaves_space_beside_the_table():
    widths = layout.column_widths(
        [],
        available=1000,
        headings={"message": "Message"},
        minimums={"message": 50},
        maximums={"message": 80},
        flexible=("message",),
        measure=measure,
    )
    # Every column sits on its declared bound; the rest of the pane stays empty
    # rather than a column growing past what it was allowed to take.
    assert widths == {"message": 80}


def test_two_elastic_columns_yield_in_order():
    headings = {"a": "A", "b": "B"}
    minimums = {"a": 50, "b": 50}
    maximums = {"a": 400, "b": 400}
    # "a" holds a long value and "b" is empty: the pane is too narrow for both
    # content widths, and it is "a" that gives its room back — "b" never pays for
    # a value it does not hold.
    widths = layout.column_widths(
        [{"a": "x" * 30, "b": ""}],
        available=200,
        headings=headings,
        minimums=minimums,
        maximums=maximums,
        flexible=("a", "b"),
        measure=measure,
    )
    assert widths == {"a": 100, "b": 100}


def test_a_table_without_a_flexible_column_keeps_its_content_widths():
    headings = {"visibility": "Visibilité", "branch": "Branche"}
    widths = layout.column_widths(
        [{"visibility": "public", "branch": "main"}, {"visibility": "privé", "branch": "dev"}],
        available=800,
        headings=headings,
        minimums={"visibility": 80, "branch": 60},
        maximums={"visibility": 130, "branch": 220},
        flexible=(),
        measure=measure,
    )
    # Nothing is designated elastic, so no column grows past its content for the
    # pane's sake: the surplus is only handed to the columns, in proportion to
    # the room they were each allowed, and they stop on their bounds.
    assert widths == {"visibility": 130, "branch": 220}
    assert sum(widths.values()) < 800
    assert widths["visibility"] > measure("privé") + layout._CELL_PADDING
