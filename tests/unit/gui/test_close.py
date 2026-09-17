"""GUI close-flow tests: stop-and-export sequence and push-failure warnings."""

from unittest.mock import MagicMock, call, patch

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig

from helpers import FakeRoot  # noqa: E402
from n8n_launcher.gui import LauncherApp


def test_close_wired_on_window_protocol(app) -> None:
    assert "WM_DELETE_WINDOW" in app.app.root._protocol_handlers


def test_on_close_stops_and_syncs_workspaces_then_destroys(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.app.on_close()
    app.app._drain_events()

    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])
    assert app.app.root.destroyed
    assert app.app._closed


def test_on_close_blocks_when_sync_fails_and_declines_close(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"
    app.mocks.messagebox._yesnocancel = None

    from n8n_launcher.gui import close as close_module

    close_module.SyncRunner.return_value.export_all.side_effect = RuntimeError("sync boom")
    app.app.on_close()
    app.app._drain_events()

    assert not app.app.root.destroyed
    assert app.app._closing is False
    assert "sync boom" in app.mocks.messagebox._yesnocancel_messages[0]
    app.manager.stop.assert_not_called()


def test_on_close_retries_sync_then_destroys(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"
    app.mocks.messagebox._yesnocancel = True

    from n8n_launcher.gui import close as close_module

    close_module.SyncRunner.return_value.export_all.side_effect = [RuntimeError("sync boom"), None]
    app.app.on_close()
    app.app._drain_events()

    assert app.app.root.destroyed
    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])


def test_on_close_destroys_when_no_workspaces(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = MagicMock()
    manager.list.return_value = []

    launcher = LauncherApp(store, manager, MagicMock(), root=FakeRoot(), browser_opener=MagicMock())
    launcher.on_close()

    assert launcher.root.destroyed


def test_on_close_warns_when_push_failed_during_sync(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    def fail_push(workspace, *, push: bool = True) -> None:
        workspace.git_push_failed = True

    app.manager.sync_git.side_effect = fail_push
    app.app.on_close()
    app.app._drain_events()

    assert app.mocks.messagebox.warnings, "a push-failure warning should be shown"
    assert "synchronisation" in app.mocks.messagebox.warnings[0].lower()
    app.manager.stop.assert_has_calls([call("ws-running"), call("ws-stopped")])
    assert app.app.root.destroyed


def test_on_close_does_not_warn_when_push_ok(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.manager.sync_git.side_effect = lambda ws, **kwargs: None
    app.app.on_close()
    app.app._drain_events()

    assert app.mocks.messagebox.warnings == []
    assert app.app.root.destroyed


def test_close_sync_reports_status(app) -> None:
    running = app.manager.list.return_value[0]
    running.api_key = "key-1"

    app.app.close_flow._sync(running, [])

    assert app.app._status_label._options["text"] == (
        f"Fermeture : synchronisation de « {running.name} »…"
    )
    app.app._drain_events()