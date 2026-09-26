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


def widths_for(rows, **kwargs):
    """Return :func:`column_widths` for *rows* with the test's own bounds."""
    return layout.column_widths(
        rows,
        headings=kwargs.get("headings", HEADINGS),
        minimums=kwargs.get("minimums", MINIMUMS),
        maximums=kwargs.get("maximums", MAXIMUMS),
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
def make_fitter(tree=None, **kwargs):
    """Return a fitter over a fake tree, with the runs-tree shape."""
    tree = tree or FakeTtk.Treeview(None, columns=("detail",))
    fitter = layout.ColumnFitter(
        tree,
        columns=kwargs.get("columns", ("detail",)),
        headings=kwargs.get("headings", {"#0": "Run", "detail": "Détails"}),
        minimums=kwargs.get("minimums", {"#0": 100, "detail": 60}),
        maximums=kwargs.get("maximums", {"#0": 300, "detail": 3000}),
        measure=measure,
    )
    return tree, fitter


def test_column_fitter_sizes_the_columns_to_the_rows_it_reads_back():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="✓  build (success)", values=("success",))

    fitter.rows()

    widths = fitter.widths()
    assert widths["#0"] == len("✓  build (success)") * CHAR_WIDTH + layout._CELL_PADDING
    assert widths["detail"] == natural("success")
    assert tree.column_widths() == widths


def test_column_fitter_walks_a_nested_tree():
    tree, fitter = make_fitter()
    job = tree.insert("", "end", iid="run-1", text="run", values=("queued",))
    tree.insert(job, "end", iid="job-1", text="    importer", values=("running",))

    fitter.rows()

    # The nested row is measured like a top-level one: its indented label is the
    # widest string of the table.
    assert fitter.widths()["#0"] == natural("    importer")


def test_column_fitter_ignores_the_pane_it_is_shown_in():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    tree._width = 400
    fitter.rows()
    narrow = dict(fitter.widths())

    # The very same rows in a much wider table: the widths are a property of the
    # content, so resizing the window (or dragging the paned sash) changes
    # nothing — there is no longer a ``<Configure>`` handler to feed.
    tree._width = 1400
    fitter.rows()
    assert fitter.widths() == narrow
    assert "<Configure>" not in tree._bindings


def test_column_fitter_fits_a_tree_that_is_not_mapped_yet():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="un run assez long", values=("success",))

    # ``winfo_width`` reports 1 before the first layout, and the columns are
    # pushed anyway: this is what makes a dialog size itself to its content
    # before it is ever shown.
    fitter.rows()
    assert fitter.widths()["#0"] == natural("un run assez long")
    assert tree.column_widths() == fitter.widths()


def test_column_fitter_does_not_push_an_unchanged_width_twice():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))
    fitter.rows()
    first = dict(tree._columns)

    fitter.rows()

    # Identical options: a re-push is wasted work on a table re-rendered on every
    # keystroke of a search field.
    assert tree._columns == first


def test_column_fitter_survives_a_row_that_vanished():
    _tree, fitter = make_fitter()

    class Vanished(FakeTtk.Treeview):
        """A tree whose rows are gone, as when a snapshot lands after a close."""

        def item(self, iid, **kwargs):  # type: ignore[override]
            raise ValueError(f"no item {iid}")

    fitter._tree = Vanished(None, columns=("detail",))
    fitter.rows()
    # No rows, no crash: the columns fall back to their minimum, which holds
    # their heading readably.
    assert fitter.widths()["#0"] == 100


def test_no_column_is_stretchable():
    tree, fitter = make_fitter()
    tree.insert("", "end", iid="run-1", text="run", values=("success",))

    fitter.rows()

    # No column absorbs the room the widget has to spare: each one keeps the exact
    # width its content asked for, which is the whole point of the rule.
    assert tree._columns["#0"]["stretch"] is False
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

    class Vanished(FakeTtk.Treeview):
        """A tree whose rows are gone, as when a snapshot lands after a close."""

        def item(self, iid, **kwargs):  # type: ignore[override]
            raise ValueError(f"no item {iid}")

    fitter._tree = Vanished(None, columns=("detail",))
    fitter._tree._items = tree._items
    fitter.rows()

    # The rows are walked, every one of them raises, and the fit falls back to the
    # headings instead of propagating.
    assert fitter.widths()["#0"] == 100


# ------------------------------------------------------------ content_width
def test_content_width_keeps_the_width_the_content_asks_for():
    assert layout.content_width(880, screen_width=1920) == 880


def test_content_width_is_capped_by_the_screen():
    # A long prose label must not open a dialog wider than the display: the
    # fraction is the launcher-wide share of the screen.
    assert layout.content_width(3000, screen_width=1920) == round(1920 * 0.72)
    assert layout.content_width(3000, screen_width=1000) == 720


def test_content_width_honours_a_minimum():
    # A dialog whose content is tiny (a row of buttons) still needs room for it.
    assert layout.content_width(40, screen_width=1920, minimum=420) == 420


def test_content_width_caps_its_minimum_on_a_small_screen():
    # The floor is a floor for a large display, never a licence to overflow a
    # small one: a 800px laptop must still get a window that fits.
    assert layout.content_width(40, screen_width=800, minimum=1200) == 800


def test_content_width_falls_back_on_an_unmeasured_window():
    # Tk never mapped it, or there is no screen at all (the fakes): the default
    # stands in for both rather than a one-pixel window.
    assert layout.content_width(0, screen_width=1920) == layout.DEFAULT_CONTENT_WIDTH
    assert layout.content_width(900, screen_width=1) == layout.DEFAULT_CONTENT_WIDTH


# ------------------------------------------------------------- WindowFitter
class FakeWindow:
    """A dialog whose requested size and screen the tests drive directly."""

    def __init__(self, *, reqwidth=800, reqheight=400, screen=(1920, 1080)):
        self._reqwidth = reqwidth
        self._reqheight = reqheight
        self._screen = screen
        self._geometry = ""
        self._bindings: dict[str, object] = {}
        self.laid_out = 0

    def bind(self, sequence, handler=None, add=None):
        """Record the handler, as Tk does for a widget binding."""
        self._bindings[sequence] = handler

    def update_idletasks(self) -> None:
        """Count the layout passes, so a fit that never reads the widget shows."""
        self.laid_out += 1

    def winfo_reqwidth(self) -> int:
        """Report the width the content asks for."""
        return self._reqwidth

    def winfo_reqheight(self) -> int:
        """Report the height the content asks for."""
        return self._reqheight

    def winfo_screenwidth(self) -> int:
        """Report the display the window is on."""
        return self._screen[0]

    def winfo_screenheight(self) -> int:
        """Report the display the window is on."""
        return self._screen[1]

    def geometry(self, value: str) -> None:
        """Record the applied geometry, as ``wm geometry`` would set it."""
        self._geometry = value

    @property
    def width(self) -> int:
        """Return the applied width, parsed back out of the geometry string."""
        return int(self._geometry.split("x", 1)[0]) if self._geometry else 0

    def resize_to(self, width: int) -> None:
        """Report a ``<Configure>`` at *width*, as a user dragging an edge does."""
        if self._geometry:
            self._geometry = f"{width}x{self._geometry.split('x', 1)[1]}"
        handler = self._bindings.get("<Configure>")
        if handler is not None:
            handler(SimpleNamespace(width=width))


def make_window_fitter(**kwargs):
    """Return a fake parent, a fake dialog and a fitter over both.

    Keyword arguments go to the fake dialog, except ``height`` which is the one
    :class:`WindowFitter` option that is not a metric of the widget.
    """
    parent = SimpleNamespace(winfo_rootx=lambda: 0, winfo_rooty=lambda: 0)
    parent.winfo_width = lambda: 1000
    parent.winfo_height = lambda: 800
    height = kwargs.pop("height", None)
    window = FakeWindow(**kwargs)
    return parent, window, layout.WindowFitter(window, parent=parent, height=height)


def test_the_window_opens_at_the_width_its_content_asks_for():
    _parent, window, fitter = make_window_fitter(reqwidth=940, reqheight=500)

    fitter.fit()

    assert window.width == 940
    assert window._geometry == "940x500+30+100"


def test_the_window_is_centred_over_its_parent():
    parent, window, fitter = make_window_fitter(reqwidth=500, reqheight=400)
    parent.winfo_rootx = lambda: 100
    parent.winfo_rooty = lambda: 50

    fitter.fit()

    # Horizontal thirds, as the other dialogs have always done it.
    x = 100 + (1000 - 500) // 2
    y = 50 + (800 - 400) // 3
    assert window._geometry == f"500x400+{x}+{y}"


def test_the_window_never_opens_wider_than_its_screen():
    _parent, window, fitter = make_window_fitter(reqwidth=4000, screen=(1280, 800))

    fitter.fit()

    assert window.width == round(1280 * 0.72)


def test_the_window_grows_when_its_content_does():
    # The runs tab is fed asynchronously, so the tree inside the dialog can become
    # wider than the width the dialog opened at. A window that then has to be
    # widened by hand is the defect this fixes.
    _parent, window, fitter = make_window_fitter(reqwidth=700, reqheight=400)
    fitter.fit()
    assert window.width == 700

    window._reqwidth = 940
    fitter.fit()

    assert window.width == 940


def test_the_window_never_shrinks_by_itself():
    # A narrower snapshot must not shrink the window under the user's cursor, nor
    # undo a width they chose.
    _parent, window, fitter = make_window_fitter(reqwidth=940, reqheight=400)
    fitter.fit()
    before = window._geometry

    window._reqwidth = 700
    fitter.fit()

    assert window._geometry == before
    assert window.width == 940


def test_resizing_the_window_by_hand_ends_the_auto_fitting():
    _parent, window, fitter = make_window_fitter(reqwidth=700, reqheight=400)
    fitter.fit()

    # The user dragged an edge: the reported width is not the one we applied.
    window.resize_to(820)
    assert fitter.locked is True

    window._reqwidth = 1200
    fitter.fit()

    assert window.width == 820


def test_the_fitters_own_geometry_does_not_lock_it():
    # The ``<Configure>`` our own ``geometry()`` triggers reports back that same
    # width, and must not be mistaken for the user taking over.
    _parent, window, fitter = make_window_fitter(reqwidth=940, reqheight=500)
    fitter.fit()

    window.resize_to(940)

    assert fitter.locked is False


def test_the_initial_layout_does_not_lock_the_fitter():
    # Tk lays the dialog out once on its own before anything is applied; that
    # says nothing about the user's intent.
    _parent, window, fitter = make_window_fitter(reqwidth=700, reqheight=400)

    window.resize_to(430)
    fitter.fit()

    assert fitter.locked is False
    assert window.width == 700


def test_a_window_can_keep_a_fixed_height():
    # The supervision window holds a block of container logs: its height must
    # stay the screen-derived one it had, and only the width follows the content.
    _parent, window, fitter = make_window_fitter(reqwidth=1100, reqheight=9000, height=680)
    fitter.fit()

    # The 9000px the logs would have asked for is ignored; the width still follows
    # the content.
    assert window._geometry == "1100x680+0+40"
    assert window.width == 1100


def test_the_fitter_survives_a_window_it_cannot_measure():
    class Broken:
        """A dialog whose geometry is unavailable, as on a half-built window."""

        def bind(self, sequence, handler=None, add=None):
            """Accept the binding without keeping it."""

        def update_idletasks(self) -> None:
            """Fail the way a destroyed widget does."""
            raise RuntimeError("invalid command name")

    fitter = layout.WindowFitter(Broken(), parent=SimpleNamespace())
    fitter.fit()  # must not raise

    assert fitter.locked is False
