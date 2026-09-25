"""Shared fakes and helpers for the GUI unit tests."""

from concurrent.futures import Future
from contextlib import contextmanager, suppress
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
            self.destroyed = False
            self._bindings: dict[str, object] = {}

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)
            self.packed = True

        def pack_forget(self) -> None:
            self.packed = False
            self.children.clear()
            with suppress(ValueError):
                self._parent.children.remove(self)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def configure(self, **kwargs) -> None:
            self._options.update(kwargs)

        def destroy(self) -> None:
            self.destroyed = True
            self.children.clear()

        def winfo_exists(self) -> int:
            """Mirror Tk: report 0 as soon as ``destroy`` has run."""
            return 0 if self.destroyed else 1

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def place(self, **kwargs) -> None:
            self._place_options = dict(kwargs)

        def place_forget(self) -> None:
            self._place_options = None

    class Label:
        def __init__(self, parent, **kwargs):
            self._parent = parent
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}

        @property
        def text(self):
            """Mirror Tk: ``text`` is just the option set through ``config``."""
            return self._options.get("text", "")

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)
            self.packed = True

        def pack_forget(self) -> None:
            self.packed = False

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

        def configure(self, **kwargs) -> None:
            self._options.update(kwargs)

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def unbind(self, sequence: str) -> None:
            self._bindings.pop(sequence, None)

        def winfo_rootx(self) -> int:
            return 0

        def winfo_rooty(self) -> int:
            return 0

        def winfo_height(self) -> int:
            return 0

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

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

    class Canvas:
        def __init__(self, _parent, **kwargs):
            self._options = dict(kwargs)
            self._bindings: dict[str, object] = {}
            self._window_id = 0
            self._items: list[object] = []
            self.scrolled = 0

        def pack(self, *_args, **_kwargs) -> None:
            pass

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def create_window(self, _x: int, _y: int, **kwargs) -> int:
            self._window_id += 1
            self._window_kwargs = dict(kwargs)
            self._items.append(kwargs.get("window"))
            return self._window_id

        def itemconfigure(self, _item_id, **kwargs) -> None:
            existing = getattr(self, "_item_kwargs", {})
            self._item_kwargs = {**existing, **kwargs}

        def yview_scroll(self, _n: int, _what: str) -> None:
            self.scrolled += 1

        def bbox(self, _tag: str):
            return (0, 0, 800, 600)

        def config(self, **kwargs) -> None:
            self._options.update(kwargs)

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
            # Pending ``after`` timers, keyed by id (tests drive them manually).
            self._after_callbacks: list[tuple[int, object]] = []
            FakeTk.Toplevel.instances.append(self)

        def wm_overrideredirect(self, _value: bool) -> None:
            pass

        def title(self, _value: str) -> None:
            self.title_text = _value

        def minsize(self, *_args) -> None:
            self._minsize = _args

        def configure(self, **kwargs) -> None:
            self._options.update(kwargs)

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

        def pack(self, *_args, **_kwargs) -> None:
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def bind(self, sequence: str, handler) -> None:
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
            FakeTtk.Button.instances.append(self)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def configure(self, **kwargs) -> None:
            """Mirror ttk's ``configure`` for the handful of knobs we touch."""
            for key in ("text", "command", "style", "state"):
                if key in kwargs:
                    setattr(self, key, kwargs.pop(key))
            self._options.update(kwargs)

    class Radiobutton:
        instances: ClassVar[list["FakeTtk.Radiobutton"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self.text = kwargs.pop("text", None)
            self.value = kwargs.pop("value", None)
            self.variable = kwargs.pop("variable", None)
            self.command = kwargs.pop("command", None)
            self._options = dict(kwargs)
            self.packed = False
            FakeTtk.Radiobutton.instances.append(self)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def select(self) -> None:
            """Mirror ttk: point the shared variable at this button's value."""
            if self.variable is not None:
                self.variable.set(self.value)

    class Treeview:
        instances: ClassVar[list["FakeTtk.Treeview"]] = []

        def __init__(self, _parent, **kwargs):
            self._options = dict(kwargs)
            self._items: dict[str, dict[str, object]] = {}
            self._bindings: dict[str, object] = {}
            self._headings: dict[str, object] = {}
            self._selection: list[str] = []
            self.element = "Treeitem.text"
            self.packed = False
            FakeTtk.Treeview.instances.append(self)

        def heading(self, column: str, **kwargs) -> None:
            self._headings[column] = dict(kwargs)

        def column(self, _column: str, **_kwargs) -> None:
            pass

        def tag_configure(self, _tag: str, **_kwargs) -> None:
            pass

        def configure(self, **kwargs) -> None:
            self._options.update(kwargs)

        def insert(self, parent: str, index: int, iid=None, text="", values=(), tags=(), **kwargs):
            item_id = iid if iid is not None else f"item{len(self._items)}"
            entry = {"parent": parent, "text": text, "values": list(values), "tags": list(tags)}
            entry.update(kwargs)
            entry.update({k: v for k, v in kwargs.items()})
            self._items[item_id] = entry
            return item_id

        def item(self, iid: str, **kwargs):
            """Get all options when no kwargs, else update the stored ones."""
            if kwargs:
                self._items[iid].update(kwargs)
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

        def bind(self, sequence: str, handler) -> None:
            self._bindings[sequence] = handler

        def selection(self) -> list[str]:
            return list(self._selection)

        def selection_set(self, *iids) -> None:
            self._selection = list(iids)

        def identify(self, _region: str, _x: int, _y: int) -> str:
            return "tree"

        def identify_element(self, _x: int, _y: int) -> str:
            # Defaults to a row element; tests set ``element`` to
            # "Treeitem.indicator" to simulate a click on the expander triangle.
            return self.element

        def identify_row(self, _y: int) -> str | None:
            return next(iter(self._items), None)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True

        def set(self, iid: str, column: str, value: object) -> None:
            entry = self._items[iid]
            entry["values"][int(column)] = value

    class Notebook:
        instances: ClassVar[list["FakeTtk.Notebook"]] = []

        def __init__(self, _parent, **kwargs):
            self._parent = _parent
            self.children: list[object] = []
            self._tabs: dict[int, dict[str, object]] = {}
            self._next_index = 0
            self._selected: int | None = None
            self.packed = False
            FakeTtk.Notebook.instances.append(self)

        def pack(self, *_args, **_kwargs) -> None:
            self.packed = True
            if hasattr(self._parent, "children"):
                self._parent.children.append(self)

        def add(self, frame, **kwargs) -> None:
            self._tabs[self._next_index] = {"frame": frame, **dict(kwargs)}
            self._next_index += 1
            self.children.append(frame)
            if self._selected is None:
                self._selected = 0

        def select(self, tab_id=None):
            """Select a tab by index or frame; return the selected widget when asked."""
            if tab_id is None:
                selected = self._tab(self._selected)
                return selected.get("frame") if selected is not None else None
            if not isinstance(tab_id, int):
                tab_id = next(
                    (idx for idx, tab in self._tabs.items() if tab["frame"] is tab_id),
                    tab_id,
                )
            self._selected = tab_id

        def tab(self, tab_id, option=None, **kwargs):
            """Get/update the options of one tab (by index or child frame)."""
            entry = self._tab(tab_id)
            if kwargs:
                entry.update(kwargs)
            if option is None:
                return dict(entry)
            return entry.get(option)

        def _tab(self, tab_id):
            if isinstance(tab_id, int):
                return self._tabs.get(tab_id)
            return next((tab for tab in self._tabs.values() if tab["frame"] is tab_id), None)


class FakeRoot:
    def __init__(self):
        self.after_callbacks: list[tuple[int, object]] = []
        self._protocol_handlers: dict[str, object] = {}
        self.destroyed = False

    def after(self, delay: int, callback) -> None:
        self.after_callbacks.append((delay, callback))

    def mainloop(self) -> None:
        pass

    def protocol(self, name: str, handler) -> None:
        self._protocol_handlers[name] = handler

    def destroy(self) -> None:
        self.destroyed = True

    def title(self, _value: str) -> None:
        pass

    def geometry(self, _value: str) -> None:
        pass

    def minsize(self, *_args) -> None:
        pass

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
    from n8n_launcher.gui.display import GitRowStatus

    return GitRowStatus()


def _drain_queue(app) -> None:
    while not app.events.empty():
        app.events.get_nowait()


def next_event(app):
    return app.app.events.get_nowait()


ACTIVE_CHIP = ("#064e3b", "#34d399")
INACTIVE_CHIP = ("#7f1d1d", "#fca5a5")
WARN_CHIP = ("#78350f", "#fcd34d")


def row_label(app, workspace_id: str) -> FakeTk.Label:
    return app.app._rows[workspace_id][1]


def row_text(app, workspace_id: str) -> str:
    return row_label(app, workspace_id)._options["text"]


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
def fake_workspace_panel_bases():
    """Rebind ``WorkspacePanel``'s ``tk.Frame`` base to :class:`FakeTk.Frame`."""
    from n8n_launcher.gui.monitoring import WorkspacePanel

    with _rebase(WorkspacePanel) as panel:
        yield panel


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
