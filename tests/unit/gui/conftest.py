"""GUI unit-test fixtures: shared mocks and the ``app`` harness."""

import importlib
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ``src/n8n_launcher/gui`` imports ``tkinter`` at module level, but an
# interpreter without Tk support (e.g. a bare system CPython on Ubuntu) cannot
# even import it. Skip the whole GUI suite there instead of failing collection;
# every CI matrix runner (and a venv built on uv's managed Python) ships Tk.
pytest.importorskip("tkinter")

# Re-export for test modules that import helpers by name from this dir.
from helpers import (
    FakeMessagebox,
    FakeRoot,
    FakeTk,
    FakeTtk,
    SyncThread,
    SyncThreadPoolExecutor,
    _safe_git_row_status,
    fake_board_bases,
    fake_ci_page_bases,
    fake_dialog_bases,
    fake_monitoring_panel_bases,
    fake_runs_panel_bases,
    fake_server_page_bases,
    make_workspace,
)

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, WorkspaceState
from n8n_launcher.gui import LauncherApp


@pytest.fixture
def gui_mocks():
    mocks = SimpleNamespace(tk=FakeTk(), ttk=FakeTtk(), messagebox=FakeMessagebox())
    mocks.health_ok = SimpleNamespace(ok=True)
    FakeTtk.Button.instances.clear()

    # Entered through an ExitStack: one ``with`` holding every fake would exceed
    # the compiler's block budget (CO_MAXBLOCKS) and fail to compile.
    with ExitStack() as stack:
        stack.enter_context(fake_monitoring_panel_bases())
        stack.enter_context(fake_ci_page_bases())
        stack.enter_context(fake_runs_panel_bases())
        stack.enter_context(fake_server_page_bases())
        stack.enter_context(fake_board_bases())
        stack.enter_context(fake_dialog_bases())
        # The dashboard, the CI page and the journal build their own widgets, so
        # the app's fakes have to reach those modules too or a real Tk root would
        # be created. ``dialog`` is in the list for the same reason as ``app``:
        # the shared ``Dialog`` base creates the action bar and its buttons.
        for module in (
            "app",
            "board",
            "monitoring",
            "ci_page",
            "ci_runs",
            "server_page",
            "dialog",
            "dialogs",
            "ci_edit",
        ):
            target = importlib.import_module(f"n8n_launcher.gui.{module}")
            for kind, fake in (("tk", mocks.tk), ("ttk", mocks.ttk)):
                # A module only binds the toolkit names it uses — ``ci_edit`` asks
                # ``tk`` for its fields but no longer names ``ttk`` — so patch the
                # ones it has rather than every one. A *missing module* still
                # raises, which is the typo this is meant to catch.
                if hasattr(target, kind):
                    stack.enter_context(patch.object(target, kind, fake))
        for module in ("app", "close", "update_flow"):
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.messagebox", mocks.messagebox))
        for module in ("app", "close", "update_flow"):
            stack.enter_context(patch(f"n8n_launcher.gui.{module}.threading.Thread", SyncThread))
        stack.enter_context(
            patch("n8n_launcher.gui.app.requests.get", return_value=mocks.health_ok)
        )
        stack.enter_context(patch("n8n_launcher.gui.app.time.sleep"))
        stack.enter_context(patch("n8n_launcher.gui.close.N8nApiClient"))
        stack.enter_context(patch("n8n_launcher.gui.close.SyncRunner"))
        stack.enter_context(
            patch("n8n_launcher.gui.app.ThreadPoolExecutor", SyncThreadPoolExecutor)
        )
        stack.enter_context(
            patch(
                "n8n_launcher.workspaces.display.git_row_status", side_effect=_safe_git_row_status
            )
        )
        yield mocks


@pytest.fixture
def app(gui_mocks, tmp_path: Path):
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    running = make_workspace(tmp_path, "Running", 5678)
    running.state = WorkspaceState.RUNNING
    stopped = make_workspace(tmp_path, "Stopped", 5680)
    manager.list.return_value = [running, stopped]
    docker = MagicMock()
    browser = MagicMock()
    launcher = LauncherApp(store, manager, docker, root=FakeRoot(), browser_opener=browser)
    return SimpleNamespace(app=launcher, manager=manager, browser=browser, mocks=gui_mocks)
