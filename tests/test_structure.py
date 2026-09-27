"""Structural snapshot of the real Tkinter widget tree.

Builds the launcher UI with **real** Tk widgets (not the FakeTk stand-ins
used by the unit suite) and walks the tree recursively, capturing only
abstract, screen-independent facts about every widget: its logical path,
its Tk class, its geometry manager, a fixed subset of abstract geometry
options, and its state. Pixels, fonts, exact colours and padding are
deliberately ignored, so the JSON is diff-stable between runs and OSes for
identical code.

The result is written to ``structure-<os>.json`` (under ``--structure-dir``)
and consumed by ``test_parity.py``: a widget present on one OS but not on
the others then fails the ``parity`` CI job. Without a display (e.g. a bare
SSH session on Linux) real Tk cannot open a window and the test skips — its
CI leg always runs under ``xvfb``.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("tkinter")

import tkinter as tk
from tkinter import ttk

# ``tests/`` is not a package, so the parity registry is imported by its module
# name: pytest puts the directory of each test file on ``sys.path`` (its default
# "prepend" import mode), which is what makes this resolve.
from test_parity import WATCHED_PATHS

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.app import LauncherApp
from n8n_launcher.gui.ci_edit import prompt_ci_credentials
from n8n_launcher.gui.dialogs import prompt_create_plan

# OS name embedded in the produced file name: ``sys.platform`` -> snapshot id.
_PLATFORM_NAMES = {"linux": "linux", "win32": "windows", "darwin": "macos"}

# The abstract geometry options kept per manager: everything else (offsets,
# padding weights, exact coordinates) is pixel-level noise for parity.
_GEOMETRY_OPTIONS = {
    "pack": ("side", "fill", "expand", "anchor"),
    "grid": ("row", "column", "sticky", "rowspan", "columnspan"),
    "place": ("relx", "rely", "anchor"),
}

# Only interactive widgets (plus the window title) get a friendly name: their
# text is a hard-coded, OS-independent string and there is at most one of each
# per dialog. Labels are left with Tk's auto names so identical copy (e.g. the
# credentials help text mentioning "GitHub") cannot produce duplicate paths.
_INTERACTIVE_CLASSES = ("Button", "TButton", "Checkbutton", "TCheckbutton", "Radiobutton")

_TEXT_ALIASES: tuple[tuple[str, str], ...] = (
    ("n8n Launcher", "title"),
    ("Activer Git", "git_enabled"),
    ("Créer le dépôt distant", "github_create"),
    ("Locale (PostgreSQL", "db_managed_radio"),
    ("Aucune base", "db_none_radio"),
    ("Copier le JSON", "copy_json_button"),
    ("J'ai collé", "confirm_pasted_button"),
    ("Annuler", "cancel_button"),
    ("Créer", "create_button"),
)


class _ListOnlyManager:
    """Minimal manager for the structure snapshot.

    Only the calls exercised while the static UI is built are implemented;
    any other use would fail loudly instead of silently producing a partial
    tree.
    """

    def __init__(self, workspace: Workspace | None) -> None:
        self._workspace = workspace

    def list(self) -> list[Workspace]:
        return [self._workspace] if self._workspace is not None else []

    def reconcile_all(self) -> bool:
        return False

    def github_token(self) -> str | None:
        return None

    def api_factory(self, _workspace: Workspace, _api_key: str) -> Any:
        """Stub the CI credentials dialog's credential listing."""
        return SimpleNamespace(
            list_credentials=lambda: [{"name": "GitHub", "type": "githubOAuth2Api"}]
        )


def _enable_dpi_awareness() -> None:
    """Request per-monitor DPI awareness before Tk opens on Windows.

    Purely defensive: the snapshot ignores pixels, but Tk's startup scaling
    is still cleaner when the process is DPI-aware. Best-effort — some hosts
    refuse the call, which is fine.
    """
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]


def _friendly_alias(widget: tk.Misc, aliases: dict[int, str], used: dict[int, set[str]]) -> None:
    """Register a readable name for *widget* when its label is recognizable.

    Only interactive widgets (plus the main title label) qualify, and the
    alias is skipped when the same name was already handed out inside the same
    toplevel: an alias must identify exactly one widget, or duplicate
    ``path`` entries would break the snapshot contract.
    """
    try:
        class_name = widget.winfo_class()
        text = str(widget.cget("text"))
    except Exception:
        return
    if class_name not in _INTERACTIVE_CLASSES and text != "n8n Launcher":
        return
    try:
        home = id(widget.winfo_toplevel())
    except Exception:
        home = 0
    names = used.setdefault(home, set())
    for needle, alias in _TEXT_ALIASES:
        if needle in text:
            if alias in names:
                return
            names.add(alias)
            aliases[id(widget)] = alias
            return


def _walk_tree(root: tk.Misc, aliases: dict[int, str]) -> list[dict[str, Any]]:
    """Describe every widget under *root* as an abstract JSON entry.

    ``root`` itself is omitted: the caller passes ``app.root`` and every path
    starts with the ``root.`` prefix.
    """
    widgets: list[dict[str, Any]] = []
    used: dict[int, set[str]] = {}

    def visit(widget: tk.Misc, parent_path: str) -> None:
        _friendly_alias(widget, aliases, used)
        name = aliases.get(id(widget), widget.winfo_name())
        path = f"{parent_path}.{name}" if parent_path else name
        widgets.append(_describe(widget, path))
        for child in widget.winfo_children():
            visit(child, path)

    for child in root.winfo_children():
        visit(child, "root")
    widgets.sort(key=lambda entry: entry["path"])
    return widgets


def _describe(widget: tk.Misc, path: str) -> dict[str, Any]:
    """Return the abstract description of a single widget."""
    manager, options = _geometry(widget)
    return {
        "path": path,
        "class": widget.winfo_class(),
        "geometry_manager": manager,
        "geometry_options": options,
        "state": _state(widget),
    }


def _geometry(widget: tk.Misc) -> tuple[str | None, dict[str, Any]]:
    """Return ``(manager, abstract_options)``; ``(None, {})`` when unmanaged.

    Windows shell out to ``wm`` (top-levels and menus) and embeds report
    ``canvas``; none of those are geometry managers we track. The access is
    lazy via ``getattr`` because menus and the root window do not even carry
    an ``*_info`` method.
    """
    manager = widget.winfo_manager() or None
    if manager not in ("pack", "grid", "place"):
        return None, {}
    info_getter = getattr(widget, f"{manager}_info", None)
    if info_getter is None:
        return None, {}
    raw = info_getter()
    options: dict[str, Any] = {}
    for key in _GEOMETRY_OPTIONS[manager]:
        if key not in raw:
            continue
        value = raw[key]
        if key == "expand":
            options[key] = value == "1"
        elif key in ("row", "column", "rowspan", "columnspan"):
            options[key] = int(value)
        else:
            options[key] = value
    return manager, options


def _state(widget: tk.Misc) -> str:
    """Return the abstract state: normal/disabled/active/checked/unchecked."""
    try:
        class_name = widget.winfo_class()
    except Exception:
        class_name = ""
    if class_name in ("Checkbutton", "TCheckbutton"):
        try:
            value = widget.getvar(widget.cget("variable"))
        except Exception:
            value = None
        if value in (1, "1"):
            return "checked"
        if value in (0, "0", False, None):
            return "unchecked"
    state = getattr(widget, "state", None)
    if state is not None and not isinstance(widget, ttk.Widget):
        # The Wm ``state()`` method on tk top-levels returns the window-manager
        # state as a bare string ("normal"); only ttk widgets return a flags
        # tuple. Reaching it for tk widgets would sort the letters of "normal"
        # into "a+l+m+n+o+r", so ttk state() is handled below instead.
        return str(state())
    if isinstance(widget, ttk.Widget):
        try:
            flags = state()
        except Exception:
            flags = ()
        return "normal" if not flags else "+".join(sorted(flags))
    try:
        return str(widget.cget("state"))
    except Exception:
        return "normal"


def _friendly_names(app: LauncherApp) -> dict[int, str]:
    """Map stable, app-owned widgets to readable logical names.

    Everything the app keeps a handle on (or the row builder exposes) gets a
    stable name so ``WATCHED_PATHS`` in ``test_parity.py`` reads naturally
    instead of carrying Tk's auto-generated ``frame2.label5`` names.

    An auto-name is only stable until the *next* widget is created before it:
    ``!frame`` becomes ``!frame4`` the moment three more frames are packed into
    the same parent, which silently renames every path recorded under it. So the
    window's structure is named here, by handle, and the snapshot's paths do not
    move when an unrelated widget is added.
    """
    aliases: dict[int, str] = {}
    aliases[id(app.root)] = "root"

    def note(widget: tk.Misc | None, name: str) -> None:
        if widget is not None:
            aliases[id(widget)] = name

    # The two rules the window is framed by, and the header. The header is the
    # only ``TFrame`` directly on the root; the two rules are plain frames, kept
    # as handles by the app.
    note(app._accent_bar, "accent_bar")
    note(app._status_border, "status_border")
    for child in app.root.winfo_children():
        if child.winfo_class() == "TFrame":
            aliases[id(child)] = "header"
    note(app._status_label, "status_label")
    note(app._subtitle, "subtitle")

    # The board and its three columns. The dock's own vertical paned window is
    # named too: it is a *sibling* pane of the list and the journal, so its
    # auto-name shifts whenever the number of panes changes — which is exactly
    # when a page opens or closes.
    if app._board is not None:
        note(app._board.pane, "paned")
        note(app._board.list_card, "list_column")
        note(app._board.journal_card, "journal_column")
        note(app._board.dock.pane, "dock")
    note(app.workspace_list, "workspace_list")
    note(app._list_canvas, "list_canvas")
    note(app._empty_state, "empty_card")
    for widget, name in zip(
        app._empty_widgets,
        ("empty_card", "empty_badge", "empty_title", "empty_subtitle"),
        strict=True,
    ):
        note(widget, name)
    for _wid, (row, _label) in app._rows.items():
        note(row, "row")
        note(row.name_label, "name_label")
        note(row.db_chip, "db_chip")
        note(row.git_chip, "git_chip")
        note(row.ci_chip, "ci_chip")
        note(row.server_chip, "server_chip")
        note(row.overflow_chip, "overflow_chip")
        note(row.action_button, "action_button")
        note(row.dirty_dot, "dirty_dot")
    return aliases


@pytest.fixture
def real_tk_root() -> tk.Tk:
    """A genuine Tk root, or a clear skip when no display is available."""
    _enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No display for real Tk widgets ({exc}); run under xvfb on Linux")
    try:
        yield root
    finally:
        root.destroy()


def _build_app(tmp_path: Path, root: tk.Tk) -> LauncherApp:
    """Construct the launcher with one stopped workspace and real widgets."""
    workflows_dir = tmp_path / "demo"
    workflows_dir.mkdir()
    workspace = Workspace(
        id="ws-demo",
        name="Demo",
        workflows_dir=workflows_dir,
        port=5678,
        db=DbConfig(DbMode.MANAGED),
        state=WorkspaceState.STOPPED,
    )
    manager = _ListOnlyManager(workspace)
    with patch.object(LauncherApp, "_poll_states", lambda self: None):
        return LauncherApp(
            SimpleNamespace(),
            manager,
            SimpleNamespace(),
            root=root,
            browser_opener=lambda _url, _profile_dir: None,
        )


def _open_dialogs(root: tk.Tk, tmp_path: Path) -> None:
    """Open the creation and CI-credentials dialogs in place.

    ``wait_window`` is neutralised so each dialog returns immediately while
    staying alive for the snapshot; ``grab_set`` too, so two dialogs can be
    open at once without fighting over the grab. messagebox calls would block
    on a human click, so every entry point is stubbed to a safe default.
    """
    with (
        patch.object(tk.Toplevel, "wait_window", lambda self: None),
        patch.object(tk.Toplevel, "grab_set", lambda self: None),
        patch("tkinter.messagebox.showwarning"),
        patch("tkinter.messagebox.showerror"),
        patch("tkinter.messagebox.showinfo"),
        patch("tkinter.messagebox.askyesno", return_value=False),
        patch("tkinter.messagebox.askyesnocancel", return_value=False),
    ):
        prompt_create_plan(root, tmp_path / "demo", DbConfig(DbMode.MANAGED))
        credentials_workspace = Workspace(
            id="ws-creds",
            name="Creds",
            workflows_dir=tmp_path / "creds",
            port=5679,
            db=DbConfig(DbMode.NONE),
            api_key="test-key",
        )
        prompt_ci_credentials(root, _ListOnlyManager(None), credentials_workspace)


def test_structure_snapshot(real_tk_root: tk.Tk, tmp_path: Path, structure_dir: Path) -> None:
    """Snapshot the widget tree and persist it as ``structure-<os>.json``."""
    app = _build_app(tmp_path, real_tk_root)
    _open_dialogs(real_tk_root, tmp_path)
    real_tk_root.update_idletasks()

    aliases = _friendly_names(app)
    aliases.update(_text_dialog_alias(real_tk_root))
    payload = {
        "os": _PLATFORM_NAMES[sys.platform],
        "widgets": _walk_tree(app.root, aliases),
    }

    structure_dir.mkdir(parents=True, exist_ok=True)
    output = structure_dir / f"structure-{payload['os']}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_every_watched_path_is_a_widget_that_exists(real_tk_root: tk.Tk, tmp_path: Path) -> None:
    """Every ``WATCHED_PATHS`` entry must name a widget this build really creates.

    The parity test cannot check this on its own: it compares three snapshots
    against each other and **skips** unless all three are present, so an entry
    that names a widget nobody builds stays wrong indefinitely. That is not
    hypothetical — ``WATCHED_PATHS`` carried ``root.!frame4.…`` for the whole
    life of the board, an auto-name that stopped existing the day the workspace
    list became the first pane of a paned window. Nothing failed, because a
    registry of *essential* widgets is only worth anything if the entries are
    live.
    """
    app = _build_app(tmp_path, real_tk_root)
    _open_dialogs(real_tk_root, tmp_path)
    real_tk_root.update_idletasks()

    aliases = _friendly_names(app)
    aliases.update(_text_dialog_alias(real_tk_root))
    paths = {entry["path"] for entry in _walk_tree(app.root, aliases)}

    stale = [path for path in WATCHED_PATHS if path not in paths]
    assert not stale, (
        "WATCHED_PATHS names widgets this build does not create: "
        + ", ".join(stale)
        + ". Either the widget was renamed or removed (drop the entry), or it "
        "should exist and does not (fix the build). The paths this build really "
        "creates are in the snapshot just written."
    )


def _text_dialog_alias(root: tk.Tk) -> dict[int, str]:
    """Name the dialog toplevels by their role (creation vs credentials).

    The two open dialogs are identified by their title, which is set from the
    same hard-coded strings on every OS.

    Their action bar is named by **identity, not by text**: it is the frame that
    actually holds the buttons, found by asking the widget tree where the
    buttons live. Matching on a caption instead would be a second, parallel
    naming rule that a reworded button would break silently. The credentials
    table is named the same way — it is the one Treeview the dialog creates, and
    a dialog with two of them is left to Tk's own names rather than being given a
    wrong one.
    """
    aliases: dict[int, str] = {}
    for widget in root.winfo_children():
        try:
            title = str(widget.wm_title())
        except Exception:
            title = ""
        if title == "Nouveau workspace":
            aliases[id(widget)] = "creation_dialog"
        elif title == "Credentials CI":
            aliases[id(widget)] = "credentials_dialog"
            table = _sole_table(widget)
            if table is not None:
                aliases[id(table)] = "credentials_tree"
        else:
            continue
        bar = _action_bar(widget)
        if bar is not None:
            aliases[id(bar)] = "actions"
    return aliases


def _sole_table(dialog: tk.Misc) -> tk.Misc | None:
    """Return the dialog's only table, or ``None`` if that is not a single one."""
    tables = [child for child in dialog.winfo_children() if child.winfo_class() == "Treeview"]
    return tables[0] if len(tables) == 1 else None


def _action_bar(dialog: tk.Misc) -> tk.Misc | None:
    """Return the frame holding *dialog*'s buttons, or ``None`` if there is none.

    A dialog's buttons are packed into one bar so they line up under the fields
    above them. That bar is currently an auto-named frame (``!frame``), so it is
    located by asking the tree: the direct child of the dialog that contains a
    button, and every button must live in that one frame — a dialog with two
    such frames has an action bar this rule cannot speak for, and is left to Tk's
    own names rather than being given a wrong one.
    """
    bars = {id(child) for child in dialog.winfo_children() if child.winfo_class() == "Frame"}
    holders = {
        id(child)
        for child in dialog.winfo_children()
        if child.winfo_class() == "Frame" and _holds_button(child)
    }
    if len(holders) != 1 or not holders <= bars:
        return None
    for child in dialog.winfo_children():
        if id(child) in holders:
            return child
    return None


def _holds_button(widget: tk.Misc) -> bool:
    """Whether *widget* or any descendant is a button."""
    stack = list(widget.winfo_children())
    while stack:
        node = stack.pop()
        if node.winfo_class() in ("Button", "TButton"):
            return True
        stack.extend(node.winfo_children())
    return False
