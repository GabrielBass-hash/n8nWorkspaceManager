"""Entry-point tests: first-launch routing and corrupt-config backup."""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

pytest.importorskip("tkinter")

from n8n_launcher.__main__ import (
    _backup_unreadable_config,
    _center,
    _signal_shutdown,
    _start_monitoring,
    main,
    stop_all,
)
from n8n_launcher.core.config import ConfigStore


class RootStub:
    """Minimal root exposing the geometry surface the entry point uses."""

    def __init__(self):
        self._geometry_calls: list[str] = []
        self.destroyed = False

    def geometry(self, value: str) -> None:
        self._geometry_calls.append(value)

    def update_idletasks(self) -> None:
        pass

    def winfo_screenwidth(self) -> int:
        return 7680

    def winfo_screenheight(self) -> int:
        return 2160

    def destroy(self) -> None:
        self.destroyed = True


def test_center_uses_responsive_window_size() -> None:
    root = RootStub()

    _center(root)

    assert root._geometry_calls[0] == "1280x820"
    assert root._geometry_calls[1] == "+3200+446"


def test_backup_unreadable_config_preserves_file_and_warns(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.path.write_text("{broken json", encoding="utf-8")
    messagebox = MagicMock()

    with patch("n8n_launcher.__main__.messagebox", messagebox):
        _backup_unreadable_config(store)

    assert not store.path.exists()
    backups = list(tmp_path.glob("launcher.db.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken json"
    messagebox.showwarning.assert_called_once()


def _patch_main(store: ConfigStore, wizard_value):
    """Return a namespace wiring ``main`` to *store* and a fake root.

    The patches stay un-entered: each test drives them through an
    ``ExitStack`` so extra patches (e.g. ``LauncherApp``) can be added.
    """
    root = RootStub()
    messagebox = MagicMock()
    wizard = MagicMock(return_value=wizard_value)
    return SimpleNamespace(
        root=root,
        messagebox=messagebox,
        wizard=wizard,
        patches=(
            patch("n8n_launcher.__main__.ConfigStore", return_value=store),
            patch("n8n_launcher.__main__.tk.Tk", return_value=root),
            patch("n8n_launcher.__main__.run_interactive_first_launch", wizard),
            patch("n8n_launcher.__main__.messagebox", messagebox),
            patch("n8n_launcher.__main__.resolve_docker_command", return_value="docker"),
            patch("n8n_launcher.__main__.DockerManager"),
        ),
    )


def _enter(ctx) -> ExitStack:
    stack = ExitStack()
    for patch_call in ctx.patches:
        stack.enter_context(patch_call)
    return stack


def test_stop_all_continues_after_one_workspace_fails() -> None:
    store = MagicMock()
    store.load.return_value = SimpleNamespace(
        workspaces=[SimpleNamespace(id="first"), SimpleNamespace(id="second")]
    )
    docker = MagicMock()
    manager = MagicMock()
    manager.stop.side_effect = [RuntimeError("stop failed"), None]

    with patch("n8n_launcher.__main__.WorkspaceManager", return_value=manager) as manager_type:
        stop_all(store, docker)

    manager_type.assert_called_once_with(store, docker)
    assert manager.stop.call_args_list == [call("first"), call("second")]


def test_stop_all_does_nothing_when_config_cannot_be_loaded() -> None:
    store = MagicMock()
    store.load.side_effect = RuntimeError("unreadable")
    manager = MagicMock()

    with patch("n8n_launcher.__main__.WorkspaceManager", return_value=manager):
        stop_all(store, MagicMock())

    manager.stop.assert_not_called()


def test_signal_shutdown_stops_workspaces_then_exits() -> None:
    store = MagicMock()
    docker = MagicMock()

    with (
        patch("n8n_launcher.__main__.stop_all") as stop,
        pytest.raises(SystemExit) as excinfo,
    ):
        _signal_shutdown(store, docker, 15, None)

    assert excinfo.value.code == 0
    stop.assert_called_once_with(store, docker)


def test_start_monitoring_installs_the_event_store(tmp_path: Path) -> None:
    from n8n_launcher.monitoring.store import EventStore

    with patch("n8n_launcher.__main__.logs_dir", return_value=tmp_path):
        monitor = _start_monitoring()

    try:
        assert isinstance(monitor, EventStore)
        assert monitor.path == tmp_path / "events.db"
        assert any(event.message == "Surveillance active" for event in monitor.read_events())
    finally:
        assert monitor is not None
        monitor.close()


def test_start_monitoring_degrades_to_stderr_when_unwritable() -> None:
    with (
        patch("n8n_launcher.__main__.bootstrap_logging", side_effect=OSError("read-only")),
        patch("n8n_launcher.__main__.logging") as logging_module,
    ):
        assert _start_monitoring() is None

    logging_module.basicConfig.assert_called_once()


def test_main_passes_the_monitor_to_the_app(tmp_path: Path) -> None:
    from n8n_launcher.core.models import AppConfig

    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    ctx = _patch_main(store, wizard_value=None)
    monitor = MagicMock()

    with _enter(ctx) as stack:
        launcher = stack.enter_context(
            patch(
                "n8n_launcher.__main__.LauncherApp",
                return_value=SimpleNamespace(run=MagicMock()),
            )
        )
        stack.enter_context(patch("n8n_launcher.__main__._start_monitoring", return_value=monitor))
        main()

    assert launcher.call_args.kwargs["monitor"] is monitor
    monitor.close.assert_called_once()


def test_main_missing_config_runs_wizard_without_backup(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx):
        main()

    ctx.wizard.assert_called_once()
    assert ctx.root.destroyed
    ctx.messagebox.showwarning.assert_not_called()
    assert not list(tmp_path.glob("launcher.db.corrupt-*"))


def test_main_corrupt_config_is_backed_up_before_wizard(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.path.write_text("{broken json", encoding="utf-8")
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx):
        main()

    ctx.wizard.assert_called_once()
    assert ctx.root.destroyed
    ctx.messagebox.showwarning.assert_called_once()
    backups = list(tmp_path.glob("launcher.db.corrupt-*"))
    assert len(backups) == 1


def test_main_valid_config_skips_wizard(tmp_path: Path) -> None:
    from n8n_launcher.core.models import AppConfig

    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    ctx = _patch_main(store, wizard_value=None)

    with _enter(ctx) as stack:
        launcher = stack.enter_context(patch("n8n_launcher.__main__.LauncherApp"))
        main()

    ctx.wizard.assert_not_called()
    ctx.messagebox.showwarning.assert_not_called()
    assert ctx.root.destroyed is False
    launcher.return_value.run.assert_called_once()
