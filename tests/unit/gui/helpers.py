"""Shared fakes and helpers for the GUI unit tests."""

from concurrent.futures import Future
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import ClassVar

from n8n_launcher.core.models import DbConfig, DbMode, Workspace


class FakeTk:
    END = "end"

    class Tk:
        def __init__(self):
            raise AssertionError("A real Tk root must not be created in tests")

    class Frame:
        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self.children: list[object] = []
            self._options = dict(kwargs)
            # An explicitly given ``name=`` is what makes a container's path
            # stable across snapshots; without one the widget keeps the unique
            # auto-name ``__str__`` hands out, so two unnamed widgets never
            # compare equal.
            self._name = kwargs.get("name") or f"!frame{id(self):x}"
            self._pack_options: dict[str, object] = {}
            self.destroyed = False
            self._bindings: dict[str, object] = {}
            # The mapped size, 1 by default: Tk's answer before the first layout.
            self._width = 1
            self._height = 1
            # Pages own their own poll timers (``self.after``), so the fake
            # frame schedules them exactly like the fake root does.
            self.after_callbacks: list[tuple[int, object]] = []
            self._after_ids: list[int] = []
            self._next_after_id = 0
            # Grid bookkeeping, for the widgets that reflow into columns.
            self._columns: dict[int, dict[str, object]] = {}
            self._rows: dict[int, dict[str, object]] = {}

        def after(self, delay: int, callback) -> int:
            after_id = self._next_after_id
            self._next_after_id += 1
            self._after_ids.append(after_id)
            self.after_callbacks.append((delay, callback))
            return after_id

        def after_cancel(self, after_id: int) -> None:
            with suppress(ValueError):
                index = self._after_ids.index(after_id)
                self._after_ids.pop(index)
                self.after_callbacks.pop(index)

        def winfo_toplevel(self):
            """Return the topmost ancestor, as Tk's ``winfo_toplevel`` does."""
            node = self
            while getattr(node, "_parent", None) is not None:
                node = node._parent
            return node

        def winfo_width(self) -> int:
            """Report the width tests set, like Tk reports the mapped width.

            One pixel is Tk's answer before the first layout, so a frame that has
            not been measured is indistinguishable from one that has — which is
            what every fitter's "is this room real?" check reads.
            """
            return self._width

        def __str__(self) -> str:
            """Return the widget path Tk would answer with.

            A frame's name is assigned by the paned window that manages it (a
            real ``panes()`` hands paths back, never widgets), so before that it
            is a unique string rather than an empty one: two unnamed frames must
            never compare equal.
            """
            return getattr(self, "_name", f"!frame{id(self):x}")

        def winfo_height(self) -> int:
            return self._height

        def pack(self, *_args, **kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)
            self._pack_options = dict(kwargs)
            self.packed = True

        def pack_forget(self) -> None:
            """Forget this widget only: its siblings stay where they are."""
            self.packed = False
            with suppress(ValueError):
                self._parent.children.remove(self)

        def grid(self, row: int = 0, column: int = 0, **kwargs) -> None:
            """Record a grid placement; the fakes do not lay anything out."""
            self._grid_options = {"row": row, "column": column, **kwargs}
            self.gridded = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def grid_forget(self) -> None:
            self.gridded = False
            self._grid_options = None

        def grid_configure(self, *args, **kwargs) -> None:
            self.grid(*args, **kwargs)

        def columnconfigure(self, index: int, **kwargs) -> None:
            self._columns[index] = dict(kwargs)

        def rowconfigure(self, index: int, **kwargs) -> None:
            self._rows[index] = dict(kwargs)

        def tkraise(self) -> None:
            """Bring the widget to the top of the stack, as Tk does."""
            self.raised = True

        def event_generate(self, sequence: str, **_kwargs) -> None:
            """Fire *sequence*'s bindings, as Tk does for a real event."""
            handler = self._bindings.get(sequence)
            if handler is not None:
                handler(None)

        def winfo_reqwidth(self) -> int:
            """Report the width the children ask for; tests set it when needed."""
            return getattr(self, "_reqwidth", 0)

        def winfo_reqheight(self) -> int:
            """Report the height the children ask for; tests set it when needed."""
            return getattr(self, "_reqheight", 0)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def configure(self, cnf=None, **kwargs) -> None:
            """Record options the way Tk does: a mapping, keywords, or both."""
            if isinstance(cnf, dict):
                self._options.update(cnf)
            self._options.update(kwargs)

        def cget(self, option: str):
            """Read back an option, as Tk's ``cget`` does."""
            return self._options.get(option, "")

        def destroy(self) -> None:
            self.destroyed = True
            self.children.clear()

        def winfo_exists(self) -> int:
            """Mirror Tk: report 0 as soon as ``destroy`` has run."""
            return 0 if self.destroyed else 1

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def place(self, **kwargs) -> None:
            self._place_options = dict(kwargs)

        def place_forget(self) -> None:
            self._place_options = None

    class Label:
        instances: ClassVar[list["FakeTk.Label"]] = []

        def __init__(self, parent, **kwargs):
            self._parent = parent
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}
            self._pack_options: dict[str, object] = {}
            self._width = 1
            FakeTk.Label.instances.append(self)

        @property
        def text(self):
            """Mirror Tk: ``text`` is just the option set through ``config``."""
            return self._options.get("text", "")

        def winfo_width(self) -> int:
            """Report the width tests set, like Tk reports the mapped width."""
            return self._width

        def winfo_height(self) -> int:
            return self._height

        def winfo_reqwidth(self) -> int:
            """Report the width the text asks for; tests set it when needed.

            A label's request is what a caller measures to keep a free-text
            tooltip or a prose block inside the room it was given.
            """
            return getattr(self, "_reqwidth", 0)

        def winfo_reqheight(self) -> int:
            return getattr(self, "_reqheight", 0)

        def pack(self, *_args, **kwargs) -> None:
            self.packed = True
            self._pack_options = dict(kwargs)
            if hasattr(self._parent, "children") and self not in self._parent.children:
                self._parent.children.append(self)

        def pack_forget(self) -> None:
            self.packed = False
            with suppress(ValueError):
                self._parent.children.remove(self)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def configure(self, cnf=None, **kwargs) -> None:
            """Record options the way Tk does: a mapping, keywords, or both."""
            if isinstance(cnf, dict):
                self._options.update(cnf)
            self._options.update(kwargs)

        def cget(self, option: str):
            """Read back an option, as Tk's ``cget`` does."""
            return self._options.get(option, "")

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def unbind(self, sequence: str) -> None:
            self._bindings.pop(sequence, None)

        def winfo_rootx(self) -> int:
            """Report where the label sits on screen, as the window manager would.

            A floating window is placed from its anchor's position, so a test has
            to be able to move the anchor to check where the window lands.
            """
            return getattr(self, "_rootx", 0)

        def winfo_rooty(self) -> int:
            """Report the label's top edge, as the window manager would."""
            return getattr(self, "_rooty", 0)

        def place(self, **kwargs) -> None:
            self._place_options = dict(kwargs)

        def place_forget(self) -> None:
            self._place_options = None

    class Button:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self.text = kwargs.get("text")
            self.command = kwargs.get("command")
            self._options = dict(kwargs)
            self.packed = False
            self._pack_options: dict[str, object] = {}

        def pack(self, *_args, **kwargs) -> None:
            self.packed = True
            self._pack_options = dict(kwargs)
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

    class Canvas:
        instances: ClassVar[list["FakeTk.Canvas"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}
            self._window_id = 0
            self._items: list[object] = []
            self._lines: dict[int, dict[str, object]] = {}
            self.scrolled = 0
            # The mapped width, which is the room the rows inside it are laid out
            # in: a list is what decides what a row can afford to show.
            self._width = 1
            FakeTk.Canvas.instances.append(self)

        def winfo_width(self) -> int:
            """Report the width tests set, like Tk reports the mapped width."""
            return self._width

        def pack(self, *_args, **_kwargs) -> None:
            pass

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def create_window(self, _x: int, _y: int, **kwargs) -> int:
            self._window_id += 1
            self._window_kwargs = dict(kwargs)
            self._items.append(kwargs.get("window"))
            return self._window_id

        def create_line(self, *coords, **kwargs) -> int:
            line_id = len(self._lines) + 1
            self._lines[line_id] = {"coords": coords, **kwargs}
            return line_id

        def lines(self) -> list[dict[str, object]]:
            """Return the drawn lines, as ``(coords, options)`` pairs."""
            return [dict(options) for _id, options in sorted(self._lines.items())]

        def itemconfigure(self, item_id, **kwargs) -> None:
            existing = getattr(self, "_item_kwargs", {})
            self._item_kwargs = {**existing, **kwargs}
            if item_id in self._lines:
                self._lines[item_id] = {**self._lines[item_id], **kwargs}

        def yview_scroll(self, _n: int, _what: str) -> None:
            self.scrolled += 1

        def bbox(self, _tag: str):
            return (0, 0, 800, 600)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def winfo_rootx(self) -> int:
            return 0

        def winfo_rooty(self) -> int:
            return 0

        def winfo_height(self) -> int:
            return 20

    class StringVar:
        def __init__(self, value=""):
            self._value = value

        def get(self):
            return self._value

        def set(self, value) -> None:
            self._value = value

    class IntVar:
        def __init__(self, value=0):
            self._value = value

        def get(self):
            return self._value

        def set(self, value) -> None:
            self._value = value

    class BooleanVar:
        def __init__(self, value=False):
            self._value = value

        def get(self):
            return self._value

        def set(self, value) -> None:
            self._value = value

    class Checkbutton:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self.text = kwargs.get("text")
            self.variable = kwargs.get("variable")
            self._options = dict(kwargs)

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def select(self) -> None:
            self.variable.set(1)

        def deselect(self) -> None:
            self.variable.set(0)

    class Toplevel:
        # Test hook: when True, ``wait_window`` simulates an Escape / cancel
        # instead of pressing Return (used to cover both dialog paths).
        cancel_on_wait = False
        # Every created dialog is recorded so tests can inspect widgets after
        # ``wait_window`` has driven the submit/cancel callback.
        instances: ClassVar[list["FakeTk.Toplevel"]] = []

        def __init__(self, _parent, **_kwargs):
            self.children: list[object] = []
            self._options = {}
            self._bindings: dict[str, object] = {}
            self.destroyed = False
            self._clipboard = ""
            # What the dialog's content asks for, and the display it is on: the
            # window fitters read both to pick a geometry, and a fake cannot
            # measure a font or ask a window manager.
            self._reqwidth = 1
            self._reqheight = 1
            self._screen = (1, 1)
            # Pending ``after`` timers, keyed by id (tests drive them manually).
            self._after_callbacks: list[tuple[int, object]] = []
            FakeTk.Toplevel.instances.append(self)

        def wm_overrideredirect(self, _value: bool) -> None:
            pass

        def title(self, _value: str) -> None:
            self.title_text = _value

        def minsize(self, *_args) -> None:
            self._minsize = _args

        def configure(self, cnf=None, **kwargs) -> None:
            """Record options the way Tk does: a mapping, keywords, or both."""
            if isinstance(cnf, dict):
                self._options.update(cnf)
            self._options.update(kwargs)

        def cget(self, option: str):
            """Read back an option, as Tk's ``cget`` does."""
            return self._options.get(option, "")

        def resizable(self, *_args) -> None:
            pass

        def transient(self, _root) -> None:
            pass

        def lift(self) -> None:
            self.lifted = True

        def deiconify(self) -> None:
            pass

        def grab_set(self) -> None:
            pass

        def update_idletasks(self) -> None:
            pass

        def geometry(self, value: str) -> None:
            self._geometry = value

        def winfo_reqwidth(self) -> int:
            """Report the width the dialog's children ask for.

            Tests set this to whatever the content of a dialog would be: the
            window fitters read it to decide the geometry, and the fakes cannot
            measure a font.
            """
            return self._reqwidth

        def winfo_reqheight(self) -> int:
            """Report the height the dialog's children ask for."""
            return self._reqheight

        def winfo_screenwidth(self) -> int:
            """Report the width of the display the dialog is on."""
            return self._screen[0]

        def winfo_screenheight(self) -> int:
            """Report the height of the display the dialog is on."""
            return self._screen[1]

        def winfo_width(self) -> int:
            """Report the mapped width, parsed back out of the applied geometry."""
            return int(self._geometry.split("x", 1)[0]) if self._geometry else self._reqwidth

        def winfo_height(self) -> int:
            """Report the mapped height, parsed back out of the applied geometry."""
            return int(self._geometry.split("x", 1)[1].split("+", 1)[0]) if self._geometry else 0

        def bind(self, sequence: str, handler=None, add: bool | str | None = None) -> None:
            if add:
                # ``add="+"`` keeps every handler; the fake stores them as a list.
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def destroy(self) -> None:
            self.destroyed = True
            self._after_callbacks.clear()

        def after(self, delay: int, callback) -> int:
            self._after_callbacks.append((delay, callback))
            return len(self._after_callbacks) - 1

        def after_cancel(self, _after_id: int) -> None:
            self._after_callbacks.clear()

        def winfo_exists(self) -> int:
            """Mirror Tk's ``winfo_exists``: 0 on a destroyed widget, else 1."""
            return 0 if self.destroyed else 1

        def wait_window(self) -> None:
            """Drive the dialog: act as if the user pressed Return, or Escape."""
            for child in self.children:
                handler = getattr(child, "_bindings", {}).get("<Return>")
                if handler is not None and not self.cancel_on_wait:
                    handler(None)
                    return
            escape = self._bindings.get("<Escape>")
            if escape is not None:
                escape(None)

        def clipboard_clear(self) -> None:
            self._clipboard = ""

        def clipboard_append(self, text: str) -> None:
            self._clipboard += text

        def clipboard_get(self) -> str:
            return self._clipboard

    class Entry:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}
            self._text = ""
            self.packed = False

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if not self.packed:
                return
            if hasattr(self._parent, "children") and self not in self._parent.children:
                self._parent.children.append(self)

        def pack_forget(self) -> None:
            self.packed = False
            with suppress(ValueError):
                self._parent.children.remove(self)

        def place(self, *_args, **_kwargs) -> None:
            """Record a place, so a collapsed panel's overlay can be forgotten."""
            self._placed = True

        def place_forget(self) -> None:
            self._placed = False

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def focus_set(self) -> None:
            self._focused = True

        def insert(self, _index: int, text: str) -> None:
            self._text = f"{self._text}{text}"

        def delete(self, _start: int, _end: int) -> None:
            self._text = ""

        def get(self) -> str:
            return self._text

    class Radiobutton:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self._options = dict(kwargs)

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

    class Menu:
        def __init__(self, _parent, **_kwargs):
            self._items: list[tuple[str | None, object | None]] = []

        def add_command(self, label: str, command) -> None:
            self._items.append((label, command))

        def add_separator(self) -> None:
            self._items.append((None, None))

        def tk_popup(self, _x, _y) -> None:
            pass

    # ``tkinter`` type names the layout code references at runtime through
    # ``typing.cast`` (a page is handed to the notebook as a widget). A cast
    # evaluates its first argument, so the fakes must expose the names even
    # though nothing here is type-checked.
    Misc = Frame
    Widget = Frame


# What a ttk pane loses to the sash handle and its own border: a pane is the
# window's remainder, not the remainder plus this, and a split that forgot it
# left the table's last column a few pixels short.
_PANE_CHROME = 12


class FakeTtk:
    class Frame:
        def __init__(self, _parent, *_args, **_kwargs):
            self._parent = _parent
            self.children: list[object] = []
            self.packed = False

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

    class PanedWindow:
        instances: ClassVar[list["FakeTtk.PanedWindow"]] = []
        # When set, ``panes()`` hands back widget *paths* the way a real
        # ``ttk.PanedWindow`` does, instead of the widgets themselves. Code that
        # compares a widget against them with ``in`` is then wrong exactly as it
        # is on a real display — which is how the dock ended up appended after
        # the journal instead of before it.
        paths: bool = False

        def __init__(self, _parent, **_kwargs):
            self._parent = _parent
            self.children: list[object] = []
            self._items: list[dict[str, object]] = []
            self._bindings: dict[str, object] = {}
            self.packed = False
            # The room this paned window is mapped to, and the sash positions
            # pushed into it. The fakes cannot lay panes out, so a test sets the
            # room and reads back the widths the split implies: a pane is what
            # lies between its own sash and the next one, less the chrome.
            self._sashes: dict[int, int] = {}
            self._width = 0
            self._height = 0
            # Per-pane overrides, for the tests that set a width directly.
            self._pane_widths: dict[int, int] = {}
            # Pending ``after`` timers (the board debounces its reflow on one).
            self.after_callbacks: list[tuple[int, object]] = []
            self._after_ids: list[int] = []
            self._next_after_id = 0
            FakeTtk.PanedWindow.instances.append(self)

        def __str__(self) -> str:
            """Return the widget path Tk would answer with.

            The dock's own paned window is a *pane* of the board's, so the board
            resolves it by path like any other; before it is managed its name is
            a unique string, never an empty one.
            """
            return getattr(self, "_name", f"!panedwindow{id(self):x}")

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def add(self, child, **kwargs) -> None:
            self._items.append({"child": child, "weight": kwargs.get("weight", 0)})
            self.children.append(child)

        def insert(self, index, child, **kwargs) -> None:
            """Insert a pane at *index*, keeping the others' order.

            A real paned window keeps the sashes it was given and re-indexes
            them: inserting at 1 pushes the second one along, which is why the
            board re-pushes every position after it inserts.
            """
            self._items.insert(index, {"child": child, "weight": kwargs.get("weight", 0)})
            self.children.insert(index, child)
            self._sashes = {
                (position + 1 if position >= index else position): value
                for position, value in self._sashes.items()
            }
            self._pane_widths = {
                (position + 1 if position >= index else position): value
                for position, value in self._pane_widths.items()
            }

        def forget(self, child) -> None:
            """Remove *child*'s pane, like Tk does when a tab is closed."""
            index = self.index(child)
            if index is None:
                return
            self._items.pop(index)
            self.children.remove(child)
            # The sashes after the removed pane re-index; the ones before it
            # keep their position, so the first column does not move when the
            # dock opens or closes.
            self._sashes = {
                (position - 1 if position > index else position): value
                for position, value in self._sashes.items()
                if position != index
            }
            self._pane_widths = {
                (position - 1 if position > index else position): value
                for position, value in self._pane_widths.items()
                if position != index
            }

        def panes(self) -> tuple[object, ...]:
            """Return the panes, as widget *paths* when ``paths`` is set.

            A real ``ttk.PanedWindow`` hands back paths, never widgets, and code
            that compares a widget against them with ``in`` is always wrong — so
            a test can ask for the real answer instead of trusting the friendly
            one.
            """
            children = tuple(item["child"] for item in self._items)
            if not self.paths:
                return children
            return tuple(self._pane_path(child) for child in children)

        def _pane_path(self, child) -> str:
            """Return the stable widget path Tk would answer with for *child*.

            The name is assigned once and kept, exactly as Tk's is: a path built
            from the pane's *position* would change the moment a pane is inserted
            or forgotten, and a test would then be checking the wrong thing.
            """
            if not hasattr(self, "_names"):
                self._names: dict[int, str] = {}
            if id(child) not in self._names:
                self._names[id(child)] = f"!{type(child).__name__.lower()}{len(self._names) + 1}"
            child._name = f".!panedwindow.{self._names[id(child)]}"
            return child._name

        def index(self, pane) -> int | None:
            """Return the position of *pane* (a widget or an index)."""
            if isinstance(pane, int):
                return pane if 0 <= pane < len(self._items) else None
            for position, item in enumerate(self._items):
                if item["child"] is pane:
                    return position
            return None

        def pane(self, index, option=None, **kwargs):
            """Read or write one pane's options, as ``ttk`` does."""
            entry = self._item(index)
            if entry is None:
                return None
            for key, value in kwargs.items():
                if key == "width":
                    self._pane_widths[self.index(index)] = int(value)
                else:
                    entry[key] = value
            if option is None:
                return dict(entry) if kwargs else self.pane_width(index)
            if option == "width":
                return self.pane_width(index)
            return entry.get(option)

        def sashpos(self, index: int, position: int | None = None) -> int:
            """Report the sash that closes pane *index*, or move it there.

            Verified against Tk 9.0.4 on a real display: a paned window with n
            panes has n-1 sashes, ``sashpos(i)`` is the *right edge* of pane i,
            and the handle occupies the gap before pane i+1 — so a two-column
            window is split by pushing ``sashpos(0, left_width)`` alone and
            reading the last pane back as ``room - left_width - handle``. An
            out-of-range index is an error, so a host that sizes the wrong pane
            cannot pass.
            """
            if position is not None:
                self._sashes[index] = position
            if index in self._sashes:
                return self._sashes[index]
            if not 0 <= index < len(self._items) - 1:
                if index == len(self._items) - 1:
                    return self._width
                raise IndexError(f"sash index {index} out of range")
            # An unpinned pane is left where Tk's layout put it: the room it
            # asked for, shared evenly here since the fakes carry no requests.
            share = self._width // max(len(self._items), 1)
            return self._left_edge(index) + share

        def _left_edge(self, index: int) -> int:
            """Return the x a pane starts at, its previous sash included."""
            return 0 if index == 0 else self.sashpos(index - 1) + _PANE_CHROME

        def sashes(self) -> list[int]:
            """Return the sashes a real paned window would have: one per gap."""
            return [self.sashpos(index) for index in range(len(self._items) - 1)]

        def pane_width(self, index: int) -> int:
            """Return the width the room between a pane's own bounds gives it.

            A ttk pane is what lies between its own sash and the next one, less
            the handle and the pane's own border; a test that ignored that would
            hide the split's own arithmetic, so the fake does not. The last pane
            is the room the split left, which is how Tk sizes it.
            """
            override = self._pane_widths.get(index)
            if override is not None:
                return override
            left = self._left_edge(index)
            right = self.sashpos(index)
            return max(right - left, 0)

        def pane_width_of(self, child) -> int:
            """Return the width of *child*'s pane, by widget rather than index."""
            index = self.index(child)
            return 0 if index is None else self.pane_width(index)

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def fire_configure(self, width: int | None = None) -> None:
            """Fire the ``<Configure>`` a real resize would, optionally resizing."""
            if width is not None:
                self._width = width
            handler = self._bindings.get("<Configure>")
            if handler is not None:
                handler(None)

        def after(self, delay: int, callback) -> int:
            after_id = self._next_after_id
            self._next_after_id += 1
            self._after_ids.append(after_id)
            self.after_callbacks.append((delay, callback))
            return after_id

        def after_cancel(self, after_id: int) -> None:
            with suppress(ValueError):
                index = self._after_ids.index(after_id)
                self._after_ids.pop(index)
                self.after_callbacks.pop(index)

        def run_after(self, index: int = 0) -> None:
            """Run the pending timer at *index*, as Tk would when it fires.

            The id list is kept aligned with the callback list (both are appended
            to together and popped from together), so the id comes off the same
            position — a cancelled timer must not make the two drift apart.
            """
            self._after_ids.pop(index)
            _delay, callback = self.after_callbacks.pop(index)
            callback()

        def winfo_width(self) -> int:
            return self._width

        def winfo_reqwidth(self) -> int:
            return self._width

        def winfo_height(self) -> int:
            return self._height

        def _item(self, index):
            resolved = self.index(index)
            return None if resolved is None else self._items[resolved]

    class Button:
        instances: ClassVar[list["FakeTtk.Button"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self.text = kwargs.pop("text", None)
            self.command = kwargs.pop("command", None)
            self.style = kwargs.pop("style", None)
            self.state = kwargs.pop("state", "normal")
            self._options = dict(kwargs)
            self.packed = False
            self._pack_options: dict[str, object] = {}
            FakeTtk.Button.instances.append(self)

        def pack(self, *_args, **kwargs) -> None:
            self.packed = True
            self._pack_options = dict(kwargs)
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def configure(self, **kwargs) -> None:
            """Mirror ttk's ``configure`` for the handful of knobs we touch."""
            for key in ("text", "command", "style", "state"):
                if key in kwargs:
                    setattr(self, key, kwargs.pop(key))
            self._options.update(kwargs)

        def cget(self, option: str):
            """Read back an option, as Tk's ``cget`` does."""
            if option == "text":
                return self.text
            return self._options.get(option)

    class Treeview:
        instances: ClassVar[list["FakeTtk.Treeview"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self._options = dict(kwargs)
            self._items: dict[str, dict[str, object]] = {}
            self._bindings: dict[str, object] = {}
            self._headings: dict[str, object] = {}
            self._columns: dict[str, dict[str, object]] = {}
            self._selection: list[str] = []
            self._width = 1
            self.element = "Treeitem.text"
            self.packed = False
            self._pack_options: dict[str, object] = {}
            # Pending ``after`` timers. A real tree inherits ``Misc.after``, and
            # the fitter debounces its resize refit on the table itself, so the
            # timer has to be recordable here for a test to fire it.
            self.after_callbacks: list[tuple[int, object]] = []
            self._after_ids: list[int] = []
            self._next_after_id = 0
            FakeTtk.Treeview.instances.append(self)

        def after(self, delay: int, callback) -> int:
            after_id = self._next_after_id
            self._next_after_id += 1
            self._after_ids.append(after_id)
            self.after_callbacks.append((delay, callback))
            return after_id

        def after_cancel(self, after_id: int) -> None:
            with suppress(ValueError):
                index = self._after_ids.index(after_id)
                self._after_ids.pop(index)
                self.after_callbacks.pop(index)

        def run_after(self, index: int = 0) -> None:
            """Run the pending timer at *index*, as Tk would when it fires.

            The id list is kept aligned with the callback list (both are appended
            to together and popped from together), so the id comes off the same
            position — a cancelled timer must not make the two drift apart.
            """
            self._after_ids.pop(index)
            _delay, callback = self.after_callbacks.pop(index)
            callback()

        def heading(self, column: str, **kwargs) -> None:
            self._headings[column] = dict(kwargs)

        def column(self, column: str, **kwargs) -> None:
            """Record the pushed options so layout tests can assert on them."""
            self._columns[column] = dict(kwargs)

        def column_requests(self) -> dict[str, dict[str, object]]:
            """Return the full last push to each column, ``width`` included.

            A tree asks Tk for its *minimums* when it is built and is then pushed
            the widths its content needs, so the requested box (``width`` and
            ``minwidth``) is what a test needs to read, not just the last width.
            """
            return {name: dict(options) for name, options in self._columns.items()}

        def column_widths(self) -> dict[str, int]:
            """Return the last width pushed to each column."""
            return {name: int(options.get("width", 0)) for name, options in self._columns.items()}

        def winfo_width(self) -> int:
            """Report the width tests set, like Tk reports the mapped width."""
            return self._width

        def tag_configure(self, _tag: str, **_kwargs) -> None:
            pass

        def configure(self, cnf=None, **kwargs) -> None:
            """Record options the way Tk does: a mapping, keywords, or both."""
            if isinstance(cnf, dict):
                self._options.update(cnf)
            self._options.update(kwargs)

        def cget(self, option: str):
            """Read back an option, as Tk's ``cget`` does."""
            return self._options.get(option, "")

        def insert(self, parent: str, index: int, iid=None, text="", values=(), tags=(), **kwargs):
            item_id = iid if iid is not None else f"item{len(self._items)}"
            entry = {"parent": parent, "text": text, "values": list(values), "tags": list(tags)}
            entry.update(kwargs)
            entry.update({k: v for k, v in kwargs.items()})
            self._items[item_id] = entry
            return item_id

        def item(self, iid: str, option=None, **kwargs):
            """Read one option, or all of them, as ``ttk.Treeview.item`` does."""
            if kwargs:
                self._items[iid].update(kwargs)
            if option is not None:
                return self._items[iid].get(option)
            return dict(self._items[iid])

        def delete(self, *iids) -> None:
            """Remove rows and every descendant that hangs below them."""
            doomed: set[str] = set(iids)
            for child, entry in list(self._items.items()):
                parent = entry.get("parent")
                while parent is not None:
                    if parent in doomed:
                        doomed.add(child)
                        break
                    parent = self._items.get(parent, {}).get("parent")
            for iid in doomed:
                self._items.pop(iid, None)

        def get_children(self, iid: str = "") -> list[str]:
            return [item_id for item_id, entry in self._items.items() if entry.get("parent") == iid]

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler

        def selection(self) -> list[str]:
            return list(self._selection)

        def selection_set(self, *iids) -> None:
            self._selection = list(iids)

        def identify(self, region: str, _x: int, y: int) -> str:
            """Report which region a coordinate lands in, as Tk does.

            Only the rows and the headings occupy a tree, so a click below the
            last row is on neither — that is what makes "the user clicked the
            empty space under the table" a thing a test can produce.
            """
            if region != "region":
                return ""
            if 0 <= y < _ROW_HEIGHT * len(self.get_children()):
                return "tree"
            return ""

        def identify_element(self, _x: int, _y: int) -> str:
            # Defaults to a row element; tests set ``element`` to
            # "Treeitem.indicator" to simulate a click on the expander triangle.
            return self.element

        def identify_row(self, y: int) -> str | None:
            """Return the item whose row covers *y*, as Tk maps a click to one.

            Rows are a fixed height and start at the top, which is enough to tell
            two rows apart; a click past the last one is on no row at all.
            """
            index = y // _ROW_HEIGHT
            rows = self.get_children()
            if 0 <= index < len(rows):
                return rows[index]
            return None

        def pack(self, *_args, **kwargs) -> None:
            self.packed = True
            # The pack options are the table's own half of the sizing rule: a
            # tree packed with a horizontal fill is stretched to its pane, so
            # its heading row no longer sits over its columns.
            self._pack_options = dict(kwargs)
            # Tk registers a packed slave with its parent, in packing order, and
            # that order decides who takes the shortfall when a container is
            # shorter than the sum of its children.
            if hasattr(self._parent, "children") and self not in self._parent.children:
                self._parent.children.append(self)

        def pack_forget(self) -> None:
            """Forget the table, keeping its parent's other children in place."""
            self.packed = False
            with suppress(ValueError):
                self._parent.children.remove(self)

        def set(self, iid: str, column: str, value: object) -> None:
            entry = self._items[iid]
            entry["values"][int(column)] = value

    class Notebook:
        instances: ClassVar[list["FakeTtk.Notebook"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self.children: list[object] = []
            self._tabs: list[dict[str, object]] = []
            self._selected: int | None = None
            self._bindings: dict[str, object] = {}
            self.packed = False
            FakeTtk.Notebook.instances.append(self)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def bind(self, sequence: str, handler, add: bool | str | None = None) -> None:
            """Bind *handler*, keeping the others when ``add`` is set.

            Tk's ``add="+"`` is what lets two independent parts of a view listen
            to the same sequence — the column fitter's refit and the window
            fitter's "the user resized this" — instead of the second one silently
            replacing the first.
            """
            if add:
                self._bindings.setdefault(sequence, [])
                if not isinstance(self._bindings[sequence], list):
                    self._bindings[sequence] = [self._bindings[sequence]]
                self._bindings[sequence].append(handler)
            elif handler is not None:
                self._bindings[sequence] = handler
            """Record a binding so a test can fire the notebook's virtual event."""
            self._bindings[sequence] = handler

        def add(self, frame, **kwargs) -> None:
            self._tabs.append({"frame": frame, **dict(kwargs)})
            self.children.append(frame)
            if self._selected is None:
                self._selected = 0

        def forget(self, frame) -> None:
            """Remove *frame*'s tab, selecting another one like Tk does.

            Tk moves the selection when the visible tab disappears, so the fake
            does too: a page closing must leave the notebook on some tab, and
            the host asserts which one.
            """
            index = self.index(frame)
            if index is None:
                return
            was_selected = index == self._selected
            self._tabs.pop(index)
            if frame in self.children:
                self.children.remove(frame)
            if not self._tabs:
                self._selected = None
                return
            if was_selected:
                self._selected = min(index, len(self._tabs) - 1)
                self.fire_tab_changed()
            elif self._selected is not None and self._selected > index:
                # A tab above the visible one goes away, so the visible one
                # slides down a row and keeps the selection.
                self._selected -= 1

        def index(self, tab_id) -> int | None:
            """Return the index of a tab (by index or child frame)."""
            if isinstance(tab_id, int):
                return tab_id if 0 <= tab_id < len(self._tabs) else None
            return next(
                (i for i, tab in enumerate(self._tabs) if tab["frame"] is tab_id),
                None,
            )

        def select(self, tab_id=None):
            """Select a tab by index or frame; return the selected widget when asked."""
            if tab_id is None:
                selected = self._tab(self._selected)
                return selected.get("frame") if selected is not None else None
            index = self.index(tab_id)
            if index is not None:
                self._selected = index

        def tab(self, tab_id, option=None, **kwargs):
            """Get/update the options of one tab (by index or child frame)."""
            entry = self._tab(tab_id)
            if entry is None:
                return None
            if kwargs:
                entry.update(kwargs)
            if option is None:
                return dict(entry)
            return entry.get(option)

        def fire_tab_changed(self) -> None:
            """Simulate a user clicking a tab, as Tk's virtual event would."""
            handler = self._bindings.get("<<NotebookTabChanged>>")
            if handler is not None:
                handler(None)

        def _tab(self, tab_id):
            index = self.index(tab_id)
            return self._tabs[index] if index is not None else None


# The height one Treeview row is given, so a fake can map a y coordinate onto a
# row the way Tk does. It only has to be consistent and non-zero.
_ROW_HEIGHT = 20


class FakeRoot:
    def __init__(self):
        self.after_callbacks: list[tuple[int, object]] = []
        self._after_ids: list[int] = []
        self._next_after_id = 0
        self._protocol_handlers: dict[str, object] = {}
        self._bindings: dict[str, object] = {}
        self._funcids: dict[str, tuple[str, object]] = {}
        self._next_funcid = 0
        self.destroyed = False
        # The mapped geometry and the display, both settable by the tests: the
        # dialogs are centred over the root and bounded by the screen.
        self._width = 1
        self._height = 1
        self._screen = (1, 1)
        self._x = 0
        self._y = 0

    def after(self, delay: int, callback) -> int:
        after_id = self._next_after_id
        self._next_after_id += 1
        self._after_ids.append(after_id)
        self.after_callbacks.append((delay, callback))
        return after_id

    def after_cancel(self, after_id: int) -> None:
        try:
            index = self._after_ids.index(after_id)
        except ValueError:
            return
        self._after_ids.pop(index)
        self.after_callbacks.pop(index)

    def mainloop(self) -> None:
        pass

    def protocol(self, name: str, handler) -> None:
        self._protocol_handlers[name] = handler

    def bind(self, sequence: str, handler, add: bool | str | None = None) -> str:
        """Bind on the root, keeping the others when ``add`` is set.

        A click anywhere in the window is delivered to the root's own bindtags,
        which is how a floating window listens for the click that dismisses it.
        The id is the one ``unbind`` takes, so it can take itself off again
        without disturbing whatever else listens to the same sequence.
        """
        if add:
            self._bindings.setdefault(sequence, [])
            if not isinstance(self._bindings[sequence], list):
                self._bindings[sequence] = [self._bindings[sequence]]
            self._bindings[sequence].append(handler)
        elif handler is not None:
            self._bindings[sequence] = handler
        funcid = f"func{self._next_funcid}"
        self._next_funcid += 1
        self._funcids[funcid] = (sequence, handler)
        return funcid

    def unbind(self, sequence: str, funcid: str | None = None) -> str:
        """Remove one binding by id, or the whole sequence when given none."""
        if funcid is None:
            self._bindings.pop(sequence, None)
            return ""
        removed = self._funcids.pop(funcid, None)
        if removed is None:
            return ""
        bound_sequence, handler = removed
        current = self._bindings.get(bound_sequence)
        if isinstance(current, list):
            if handler in current:
                current.remove(handler)
            if not current:
                self._bindings.pop(bound_sequence, None)
        elif current is handler:
            self._bindings.pop(bound_sequence, None)
        return ""

    def fire(self, sequence: str, event=None) -> None:
        """Deliver *sequence* to whatever the root is listening for."""
        handler = self._bindings.get(sequence)
        if isinstance(handler, list):
            for one in list(handler):
                one(event)
        elif handler is not None:
            handler(event)

    def destroy(self) -> None:
        self.destroyed = True

    def title(self, _value: str) -> None:
        pass

    def geometry(self, _value: str) -> None:
        pass

    def nametowidget(self, name):
        """Return the widget a path names; the fakes hand out the widget itself."""
        return name

    def winfo_x(self) -> int:
        """Report the window's left edge, as a window manager would."""
        return self._x

    def winfo_y(self) -> int:
        """Report the window's top edge, as a window manager would."""
        return self._y

    def minsize(self, *_args) -> None:
        pass

    def winfo_rootx(self) -> int:
        """Report the root's position, as a window manager would."""
        return 0

    def winfo_rooty(self) -> int:
        """Report the root's position, as a window manager would."""
        return 0

    def winfo_width(self) -> int:
        """Report the root's mapped width; tests set it to place a dialog."""
        return self._width

    def winfo_height(self) -> int:
        """Report the root's mapped height; tests set it to place a dialog."""
        return self._height

    def winfo_screenwidth(self) -> int:
        """Report the width of the display the root is on."""
        return self._screen[0]

    def winfo_screenheight(self) -> int:
        """Report the height of the display the root is on."""
        return self._screen[1]

    def configure(self, **_kwargs) -> None:
        pass


class SyncThread:
    """Runs the worker synchronously on ``start()`` instead of on a real thread."""

    def __init__(self, *, target=None, args=(), kwargs=None, **_extra):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self) -> None:
        if self.target:
            self.target(*self.args, **self.kwargs)


class HoldingThread:
    """Captures threads so tests can run their payloads explicitly."""

    instances: ClassVar[list["HoldingThread"]] = []

    def __init__(self, *, target=None, **kwargs):
        self.target = target
        self.started = False
        HoldingThread.instances.append(self)

    def start(self) -> None:
        self.started = True


class SyncThreadPoolExecutor:
    """Inline stand-in for ``ThreadPoolExecutor`` (callables run in submit order).

    The suite patches ``threading.Thread`` with :class:`SyncThread`, so a real
    executor would run its worker loop *inline* on the calling thread and
    block forever on ``work_queue.get()``. This fake executes submitted
    callables eagerly and hands back completed futures, which is enough for
    ``as_completed`` semantics and keeps log-fetch order deterministic.
    """

    def __init__(self, max_workers: int = 1, **_kwargs):
        self.max_workers = max_workers

    def __enter__(self) -> "SyncThreadPoolExecutor":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def submit(self, fn, /, *args, **kwargs):
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        pass


class FakeMessagebox:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []
        self._yesno = False
        self._yesnocancel = None
        self._yesnocancel_messages: list[str] = []

    def showerror(self, _title, message, **_kwargs) -> None:
        self.errors.append(message)

    def showwarning(self, _title, message, **_kwargs) -> None:
        self.warnings.append(message)

    def showinfo(self, _title, message, **_kwargs) -> None:
        self.infos.append(message)

    def askyesno(self, *args, **_kwargs) -> bool:
        return self._yesno

    def askyesnocancel(self, *_args, **_kwargs) -> bool | None:
        self._yesnocancel_messages.append(_args[1] if len(_args) > 1 else "")
        return self._yesnocancel


def fire(widget, sequence: str, event=None):
    """Deliver *sequence* to every handler a fake widget is listening for.

    A widget that bound the same sequence twice holds a *list* of handlers — that
    is what ``add="+"`` means — so firing it means calling all of them, and this
    helper is what stops a test from silently exercising only the last one.

    The last handler's return value is handed back, as Tk does: a handler that
    returns ``"break"`` is how a view stops a key from reaching the toplevel.
    """
    handler = widget._bindings.get(sequence)
    if handler is None:
        return None
    if not isinstance(handler, list):
        return handler(event)
    result = None
    for one in list(handler):
        result = one(event)
    return result


def make_workspace(tmp_path: Path, name: str, port: int) -> Workspace:
    return Workspace(
        id=f"ws-{name.lower()}",
        name=name,
        workflows_dir=tmp_path / name,
        port=port,
        db=DbConfig(DbMode.MANAGED),
    )


def _safe_git_row_status(_workspace):
    """Return a default status without ever touching real git in tests."""
    from n8n_launcher.workspaces.display import GitRowStatus

    return GitRowStatus()


def _drain_queue(app) -> None:
    while not app.events.empty():
        app.events.get_nowait()


def next_event(app):
    return app.app.events.get_nowait()


ACTIVE_CHIP = ("#064e3b", "#34d399")
INACTIVE_CHIP = ("#7f1d1d", "#fca5a5")
WARN_CHIP = ("#78350f", "#fcd34d")


def all_labels(tk_fake=FakeTk) -> list[FakeTk.Label]:
    """Return every ``tk.Label`` built against *tk_fake*, in creation order."""
    return list(tk_fake.Label.instances)


def row_label(app, workspace_id: str) -> FakeTk.Label:
    return app.app._rows[workspace_id][1]


def row_full_name(app, workspace_id: str) -> str:
    return app.app._rows[workspace_id][0].full_name


def row_text(app, workspace_id: str) -> str:
    return row_label(app, workspace_id)._options["text"]


def row_chip_pack(app, workspace_id: str, attr: str) -> dict[str, object]:
    """Return the ``pack`` options of one row chip (its gap, its own padding)."""
    return row_chip(app, workspace_id, attr)._pack_options


def row_chip(app, workspace_id: str, attr: str):
    return getattr(app.app._rows[workspace_id][0], attr)


def row_chip_text(app, workspace_id: str, attr: str) -> str:
    return row_chip(app, workspace_id, attr)._options["text"]


def row_chip_colors(app, workspace_id: str, attr: str) -> tuple[str, str]:
    chip = row_chip(app, workspace_id, attr)
    return chip._options["bg"], chip._options["fg"]


def row_action_button(app, workspace_id: str):
    frame = app.app._rows[workspace_id][0]
    return frame.action_button


def row_action_text(app, workspace_id: str) -> str:
    return row_action_button(app, workspace_id).text


@contextmanager
def _rebase(widget_class, base=FakeTk.Frame):
    """Temporarily swap *widget_class*'s ``tk`` base for a fake one.

    A panel captures ``tk.Frame`` at import time, so patching the module-level
    ``tk``/``ttk`` is not enough to build one without a real Tk root.
    """
    original = widget_class.__bases__
    widget_class.__bases__ = (base,)
    try:
        yield widget_class
    finally:
        widget_class.__bases__ = original


@contextmanager
def fake_monitoring_panel_bases():
    """Rebind ``MonitoringPanel``'s ``tk.Frame`` base to :class:`FakeTk.Frame`.

    Same trick as :func:`fake_runs_panel_bases`: the base class is swapped for
    the test and restored after.
    """
    from n8n_launcher.gui.monitoring import MonitoringPanel

    with _rebase(MonitoringPanel) as panel:
        yield panel


@contextmanager
def fake_server_panel_bases():
    """Rebind ``ServerPanel``'s ``tk.Frame`` base to :class:`FakeTk.Frame`."""
    from n8n_launcher.gui.monitoring import ServerPanel

    with _rebase(ServerPanel) as panel:
        yield panel


@contextmanager
def fake_server_page_bases():
    """Rebind the server page's ``tk.Frame`` base to :class:`FakeTk.Frame`.

    The page and the :class:`ServerPanel` it owns both capture their base class
    at import time, so both have to be swapped to build one on the fakes.
    """
    from n8n_launcher.gui.monitoring import ServerPanel
    from n8n_launcher.gui.server_page import ServerPage

    with ExitStack() as stack:
        for cls in (ServerPage, ServerPanel):
            stack.enter_context(_rebase(cls))
        yield ServerPage


@contextmanager
def fake_ci_page_bases():
    """Rebind the CI page's ``tk.Frame`` bases to :class:`FakeTk.Frame`.

    The page and the selection panel it owns both capture their base class at
    import time, so both have to be swapped to build one on the fakes.
    """
    from n8n_launcher.gui.ci_page import CiPage, CiSelectionPanel

    with ExitStack() as stack:
        for cls in (CiPage, CiSelectionPanel):
            stack.enter_context(_rebase(cls))
        yield CiPage


@contextmanager
def fake_board_bases():
    """Rebind ``Card``'s ``tk.Frame`` base to :class:`FakeTk.Frame`.

    A card is a frame the dock builds, and it captures its base class at import
    time, so building one on the fakes means swapping it for the duration.
    """
    from n8n_launcher.gui.board import Card

    with _rebase(Card):
        yield Card


@contextmanager
def fake_runs_panel_bases():
    """Rebind ``RunsPanel``'s ``tk.Frame`` base to :class:`FakeTk.Frame`.

    ``RunsPanel`` captures its base class at import time, so patching the
    module-level ``tk``/``ttk`` is not enough to build one without a real Tk
    root. The base is swapped for the duration of the test and restored after,
    keeping the production class untouched outside the ``with`` block.
    """
    from n8n_launcher.gui.ci_runs import RunsPanel

    original = RunsPanel.__bases__
    RunsPanel.__bases__ = (FakeTk.Frame,)
    try:
        yield RunsPanel
    finally:
        RunsPanel.__bases__ = original


@contextmanager
def fake_dialog_bases():
    """Rebind ``Dialog``'s ``tk.Toplevel`` base to :class:`FakeTk.Toplevel`.

    A dialog is a subclass (it is a ``Toplevel`` that opens itself), so it
    captures the real base class at import time: patching the module-level
    ``tk``/``ttk`` is not enough to build one without a real Tk root. Same
    trade as :func:`fake_board_bases`, one base deeper.
    """
    from n8n_launcher.gui.dialog import Dialog

    with _rebase(Dialog, FakeTk.Toplevel) as dialog_class:
        yield dialog_class
