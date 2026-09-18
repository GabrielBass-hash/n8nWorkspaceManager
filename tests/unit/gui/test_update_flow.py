"""GUI update-flow tests: scheduling, offer, download, install and error paths."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig
from n8n_launcher.platform.updater import Asset, Release, parse_version

from helpers import FakeRoot, _drain_queue  # noqa: E402
from n8n_launcher.gui import LauncherApp


TEST_ASSET = Asset(
    name="n8n-launcher-macos.dmg",
    url="https://example.test/n8n-launcher-macos.dmg",
    size=42,
)


def make_test_release() -> Release:
    return Release(
        tag_name="v1.0.03",
        version=parse_version("v1.0.03"),
        assets={TEST_ASSET.name: TEST_ASSET},
    )


def update_worker_patches(target, *, writable: bool):
    return [
        patch("n8n_launcher.gui.update_flow.updater.fetch_latest_release", return_value=make_test_release()),
        patch("n8n_launcher.gui.update_flow.updater.current_version", return_value="1.0.02"),
        patch("n8n_launcher.gui.update_flow.updater.install_target", return_value=target),
        patch("n8n_launcher.gui.update_flow.updater.compatible_asset", return_value=TEST_ASSET),
        patch("n8n_launcher.gui.update_flow.os.access", return_value=writable),
    ]


def run_update_worker(app, target, *, writable: bool) -> None:
    _drain_queue(app.app)
    with ExitStack() as stack:
        with patch("n8n_launcher.gui.update_flow.updater.cleanup_stale"):
            for cm in update_worker_patches(target, writable=writable):
                stack.enter_context(cm)
            app.app.update_flow.check()


def next_event(app):
    return app.app.events.get_nowait()


def test_update_check_scheduled_when_frozen(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    root = FakeRoot()
    with patch(
        "n8n_launcher.gui.update_flow.updater.install_target",
        return_value=tmp_path / "n8n-launcher",
    ):
        LauncherApp(store, MagicMock(), MagicMock(), root=root, browser_opener=MagicMock())

    assert any(delay == 1500 for delay, _ in root.after_callbacks)


def test_update_check_not_scheduled_from_source(gui_mocks, tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    root = FakeRoot()
    with patch("n8n_launcher.gui.update_flow.updater.install_target", return_value=None):
        LauncherApp(store, MagicMock(), MagicMock(), root=root, browser_opener=MagicMock())

    assert not any(delay == 1500 for delay, _ in root.after_callbacks)


def test_update_worker_posts_offer_through_queue(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    callback, error = next_event(app)
    assert error is None
    ask = MagicMock(return_value=False)
    with patch("n8n_launcher.gui.update_flow.messagebox.askyesno", ask), patch(
        "n8n_launcher.gui.update_flow.updater.download_asset"
    ) as download_asset:
        callback()

    ask.assert_called_once()
    download_asset.assert_not_called()


def test_update_worker_stays_silent_when_network_fails(app, tmp_path) -> None:
    _drain_queue(app.app)
    with patch(
        "n8n_launcher.gui.update_flow.updater.fetch_latest_release",
        side_effect=RuntimeError("offline"),
    ), patch(
        "n8n_launcher.gui.update_flow.updater.install_target",
        return_value=tmp_path / "n8n-launcher",
    ), patch("n8n_launcher.gui.update_flow.updater.cleanup_stale"):
        app.app.update_flow.check()

    assert app.app.events.empty()


def test_update_worker_shows_link_when_not_writable(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)

    callback, error = next_event(app)
    assert error is None
    callback()

    status = app.app._status_label
    assert "Nouvelle version" in status._options["text"]
    assert "<Button-1>" in status._bindings
    assert status._options["cursor"] == "hand2"


def test_update_link_opens_release_page(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)
    callback, _error = next_event(app)
    callback()

    with patch("n8n_launcher.gui.update_flow.webbrowser.open") as webbrowser_open:
        app.app._status_label._bindings["<Button-1>"](None)

    webbrowser_open.assert_called_once_with("https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest")


def test_set_status_resets_update_link(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=False)
    callback, _error = next_event(app)
    callback()

    app.app.set_status("")

    assert "<Button-1>" not in app.app._status_label._bindings
    assert app.app._status_label._options["cursor"] == ""
    assert app.app._status_label._options["fg"] == "#94a3b8"


def test_update_accept_flow_downloads_installs_and_relaunches(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    script = tmp_path / "apply_update.sh"
    run_update_worker(app, target, writable=True)

    ask = MagicMock(side_effect=[True, True])
    with patch("n8n_launcher.gui.update_flow.messagebox.askyesno", ask), patch(
        "n8n_launcher.gui.update_flow.updater.download_asset"
    ) as download_asset, patch(
        "n8n_launcher.gui.update_flow.updater.installer_script", return_value=script
    ) as installer_script, patch(
        "n8n_launcher.gui.update_flow.updater.spawn_installer"
    ) as spawn_installer:
        app.app._drain_events()

    download_asset.assert_called_once()
    args, kwargs = download_asset.call_args
    assert args[0] == TEST_ASSET.url
    assert kwargs["expected_size"] == TEST_ASSET.size
    assert callable(kwargs["progress"])
    installer_script.assert_called_once()
    spawn_installer.assert_called_once_with(script)
    assert app.app.root.destroyed
    assert app.app._closed


def test_update_offer_declined_skips_download(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    with patch("n8n_launcher.gui.update_flow.messagebox.askyesno", return_value=False), patch(
        "n8n_launcher.gui.update_flow.updater.download_asset"
    ) as download_asset:
        app.app._drain_events()

    download_asset.assert_not_called()
    assert not app.app.root.destroyed


def test_update_download_failure_surfaces_error(app, tmp_path) -> None:
    target = tmp_path / "n8n-launcher.app"
    run_update_worker(app, target, writable=True)

    with patch("n8n_launcher.gui.update_flow.messagebox.askyesno", return_value=True), patch(
        "n8n_launcher.gui.update_flow.updater.download_asset",
        side_effect=RuntimeError("500 boom"),
    ) as download_asset:
        app.app._drain_events()

    download_asset.assert_called_once()
    assert app.mocks.messagebox.errors == ["Téléchargement impossible : 500 boom"]
    assert not app.app.root.destroyed