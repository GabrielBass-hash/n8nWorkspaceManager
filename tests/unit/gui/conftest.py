"""GUI unit-test fixtures: shared mocks and the ``app`` harness."""

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
    fake_ci_page_bases,
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
        # The page host and the CI page build their own widgets, so the app's
        # fakes have to reach those modules too or the real Tk would be created.
        for module in ("app", "monitoring", "pages", "ci_page", "ci_runs", "server_page"):
            for kind, fake in (("tk", mocks.tk), ("ttk", mocks.ttk)):
                stack.enter_context(patch(f"n8n_launcher.gui.{module}.{kind}", fake))
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
            patch("n8n_launcher.gui.display.git_row_status", side_effect=_safe_git_row_status)
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
