"""GUI unit-test fixtures: shared mocks and the ``app`` harness."""

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

    with (
        patch("n8n_launcher.gui.app.tk", mocks.tk),
        patch("n8n_launcher.gui.app.ttk", mocks.ttk),
        patch("n8n_launcher.gui.app.messagebox", mocks.messagebox),
        patch("n8n_launcher.gui.close.messagebox", mocks.messagebox),
        patch("n8n_launcher.gui.update_flow.messagebox", mocks.messagebox),
        patch("n8n_launcher.gui.app.threading.Thread", SyncThread),
        patch("n8n_launcher.gui.close.threading.Thread", SyncThread),
        patch("n8n_launcher.gui.update_flow.threading.Thread", SyncThread),
        patch("n8n_launcher.gui.app.requests.get", return_value=mocks.health_ok),
        patch("n8n_launcher.gui.app.time.sleep"),
        patch("n8n_launcher.gui.close.N8nApiClient"),
        patch("n8n_launcher.gui.close.SyncRunner"),
        patch(
            "n8n_launcher.gui.app.ThreadPoolExecutor",
            SyncThreadPoolExecutor,
        ),
        patch("n8n_launcher.gui.display.git_row_status", side_effect=_safe_git_row_status),
    ):
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
