"""Update-flow tests: scheduling, offer, download, install and error paths.

Every question the flow asks is injected, so none of these need a display. The
controller is driven through the same queue and callbacks the GUI hands it, and
what is asserted is the *order* — check, then offer, then download, then install
— together with the promise that nothing raises into the caller.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from n8n_launcher.platform.update_flow import LAUNCH_CHECK_DELAY_MS, UpdateController
from n8n_launcher.platform.updater import Asset, Release, parse_version

MODULE = "n8n_launcher.platform.update_flow"

TEST_ASSET = Asset(
    name="n8n-launcher-macos.dmg",
    url="https://example.test/n8n-launcher-macos.dmg",
    size=42,
)


def make_test_release() -> Release:
    """Return a release one version ahead of what the tests pretend to run."""
    return Release(
        tag_name="v1.0.03",
        version=parse_version("v1.0.03"),
        assets={TEST_ASSET.name: TEST_ASSET},
    )


class Probe:
    """Everything a controller was asked, and the answers it is given."""

    def __init__(self) -> None:
        self.accepted = True
        self.asked: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []
        self.statuses: list[str] = []
        self.links: list[str] = []
        self.scheduled: list[tuple[int, object]] = []
        self.closed = False
        self.finished = 0

    def offer(self, title: str, body: str) -> bool:
        self.asked.append((title, body))
        return self.accepted

    def error(self, title: str, body: str) -> None:
        self.errors.append((title, body))

    def link(self, message: str, url: str) -> None:
        self.statuses.append(message)
        self.links.append(url)


def make_controller(**overrides) -> tuple[UpdateController, Probe]:
    """Return a controller wired to a fresh :class:`Probe`."""
    probe = Probe()
    arguments = {
        "events": queue.Queue(),
        "set_status": probe.statuses.append,
        "on_offer": probe.offer,
        "on_error": probe.error,
        "on_status_link": probe.link,
        "schedule": lambda ms, fn: probe.scheduled.append((ms, fn)),
        "is_closed": lambda: probe.closed,
        "finish_close": lambda: setattr(probe, "finished", probe.finished + 1),
    }
    arguments.update(overrides)
    return UpdateController(**arguments), probe


@contextmanager
def release_behind(
    target: Path | None, *, writable: bool, version: str = "1.0.02"
) -> Iterator[None]:
    """Make the check find ``make_test_release()`` as newer and installable."""
    with (
        patch(f"{MODULE}.updater.cleanup_stale"),
        patch(f"{MODULE}.updater.fetch_latest_release", return_value=make_test_release()),
        patch(f"{MODULE}.updater.current_version", return_value=version),
        patch(f"{MODULE}.updater.install_target", return_value=target),
        patch(f"{MODULE}.updater.compatible_asset", return_value=TEST_ASSET),
        patch(f"{MODULE}.os.access", return_value=writable),
    ):
        yield


def _join(name: str) -> None:
    """Wait for the worker thread called *name* to finish."""
    for _ in range(500):
        if not any(t.name == name for t in threading.enumerate()):
            return
        threading.Event().wait(0.01)
    raise AssertionError(f"thread {name} never finished")


def drain(controller: UpdateController) -> None:
    """Apply the continuations already queued — and only those.

    Snapshotting first is what keeps a test in control of the order: a
    continuation that starts a worker would otherwise let that worker post its
    own continuation into the same drain.
    """
    pending = []
    while True:
        try:
            pending.append(controller._events.get_nowait())
        except queue.Empty:
            break
    for callback, error in pending:
        assert error is None
        callback()


def offer_once(controller: UpdateController, tmp_path: Path) -> None:
    """Run one check round that ends with an offer waiting on the queue."""
    with release_behind(tmp_path / "n8n-launcher.app", writable=True):
        controller.check()
        _join("n8n-launcher-update")
    assert not controller._events.empty()


def test_check_is_scheduled_only_from_an_installed_bundle(tmp_path) -> None:
    controller, probe = make_controller()
    with patch(f"{MODULE}.updater.install_target", return_value=tmp_path / "n8n-launcher"):
        controller.setup()
    assert [delay for delay, _ in probe.scheduled] == [LAUNCH_CHECK_DELAY_MS]

    from_source, source_probe = make_controller()
    with patch(f"{MODULE}.updater.install_target", return_value=None):
        from_source.setup()
    assert source_probe.scheduled == []


def test_a_scheduling_failure_never_breaks_the_launch(tmp_path) -> None:
    def explode(_delay: int, _action) -> None:
        raise RuntimeError("no loop yet")

    controller, _probe = make_controller(schedule=explode)
    with patch(f"{MODULE}.updater.install_target", return_value=tmp_path / "n8n-launcher"):
        controller.setup()  # must not raise


def test_a_closed_app_never_starts_a_check() -> None:
    controller, probe = make_controller()
    probe.closed = True
    with patch(f"{MODULE}.updater.fetch_latest_release") as fetch:
        controller.check()
        _join("n8n-launcher-update")
    fetch.assert_not_called()
    assert controller._events.empty()


def test_the_worker_offers_without_asking_anything_itself(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)

    # The worker only queues: the question belongs to the caller's thread.
    assert probe.asked == []
    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset"),
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")
    assert [title for title, _ in probe.asked] == ["Mise à jour disponible"]
    # The body names both ends of the upgrade; the "from" side is the installed
    # version, read live rather than through the check's patches.
    assert "-> v1.0.03" in probe.asked[0][1]


def test_a_declined_offer_never_downloads(tmp_path) -> None:
    controller, probe = make_controller()
    probe.accepted = False
    offer_once(controller, tmp_path)

    with patch(f"{MODULE}.updater.download_asset") as download:
        drain(controller)
    download.assert_not_called()
    assert probe.finished == 0


def test_an_accepted_offer_downloads_the_asset(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)

    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset") as download,
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")

    download.assert_called_once()
    args, kwargs = download.call_args
    assert args == (TEST_ASSET.url, tmp_path / TEST_ASSET.name)
    assert kwargs["expected_size"] == TEST_ASSET.size
    assert callable(kwargs["progress"])
    assert probe.statuses[-1] == "Téléchargement de la mise à jour v1.0.03…"


def test_an_accepted_offer_runs_the_installer_then_closes(tmp_path) -> None:
    controller, probe = make_controller()
    script = tmp_path / "apply_update.sh"
    offer_once(controller, tmp_path)

    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset"),
        patch(f"{MODULE}.updater.installer_script", return_value=script) as make_script,
        patch(f"{MODULE}.updater.spawn_installer") as spawn,
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")
        drain(controller)

    make_script.assert_called_once()
    spawn.assert_called_once_with(script)
    # Only the close *after* a successful hand-off stops the app.
    assert probe.finished == 1


def test_declining_the_restart_prompt_leaves_the_app_alone(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)

    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset"),
        patch(f"{MODULE}.updater.spawn_installer") as spawn,
    ):
        drain(controller)  # accepted: the download starts
        _join("n8n-launcher-update-dl")
        probe.accepted = False  # ... and reconsiders at the restart prompt
        drain(controller)

    spawn.assert_not_called()
    assert probe.finished == 0
    assert probe.statuses[-1] == ""


def test_a_download_failure_is_reported_and_ends_the_flow(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)

    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset", side_effect=RuntimeError("500 boom")),
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")
        drain(controller)

    assert probe.errors == [("Mise à jour", "Téléchargement impossible : 500 boom")]
    assert probe.finished == 0


def test_an_installer_failure_is_reported_and_does_not_close(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)

    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset"),
        patch(f"{MODULE}.updater.installer_script", side_effect=OSError("read-only")),
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")
        drain(controller)

    assert probe.errors == [("Mise à jour", "read-only")]
    assert probe.finished == 0


def test_an_unwritable_install_points_at_the_release_page(tmp_path) -> None:
    controller, probe = make_controller()
    with release_behind(tmp_path / "n8n-launcher.app", writable=False):
        controller.check()
        _join("n8n-launcher-update")

    drain(controller)
    assert probe.asked == []
    assert probe.statuses == ["Nouvelle version v1.0.03 disponible — cliquer pour ouvrir"]
    assert probe.links == [
        "https://github.com/GabrielBass-hash/n8nWorkspaceManager/releases/latest"
    ]


def test_progress_is_announced_once_per_megabyte(tmp_path) -> None:
    controller, probe = make_controller()

    def fake_download(_url, _dest, *, expected_size, progress):
        for received in (0, 512 * 1024, 1024 * 1024, 3 * 1024 * 1024, 3 * 1024 * 1024 + 5):
            progress(received)

    offer_once(controller, tmp_path)
    with (
        patch(f"{MODULE}.updates_dir", return_value=tmp_path),
        patch(f"{MODULE}.updater.download_asset", side_effect=fake_download),
    ):
        drain(controller)
        _join("n8n-launcher-update-dl")
        drain(controller)  # the progress reports reach the bar through the queue

    # 0 and 512 KiB share a megabyte bucket, so only 1 and 3 are announced.
    assert [m for m in probe.statuses if "Mo" in m] == [
        "Téléchargement de la mise à jour… 1 Mo",
        "Téléchargement de la mise à jour… 3 Mo",
    ]


def test_a_closed_app_answers_nothing(tmp_path) -> None:
    controller, probe = make_controller()
    offer_once(controller, tmp_path)
    probe.closed = True
    with patch(f"{MODULE}.updater.download_asset") as download:
        drain(controller)
    download.assert_not_called()
    assert probe.asked == []

    silent, silent_probe = make_controller()
    silent_probe.closed = True
    silent._show_link(make_test_release())
    assert silent_probe.statuses == []
    assert silent_probe.links == []

    closed, closed_probe = make_controller()
    closed_probe.closed = True
    closed._confirm_install(tmp_path / "asset.dmg")
    assert closed_probe.asked == []
    assert closed_probe.finished == 0


def test_the_worker_stays_silent_when_the_network_fails() -> None:
    controller, _probe = make_controller()
    with (
        patch(f"{MODULE}.updater.cleanup_stale"),
        patch(f"{MODULE}.updater.fetch_latest_release", side_effect=RuntimeError("offline")),
    ):
        controller.check()
        _join("n8n-launcher-update")
    assert controller._events.empty()


def test_the_worker_stays_silent_when_nothing_is_newer(tmp_path) -> None:
    controller, _probe = make_controller()
    with release_behind(tmp_path / "n8n-launcher.app", writable=True, version="1.0.03"):
        controller.check()
        _join("n8n-launcher-update")
    assert controller._events.empty()


def test_the_worker_stays_silent_without_an_install_target() -> None:
    controller, probe = make_controller()
    with (
        patch(f"{MODULE}.updater.cleanup_stale"),
        patch(f"{MODULE}.updater.fetch_latest_release", return_value=make_test_release()),
        patch(f"{MODULE}.updater.current_version", return_value="1.0.02"),
        patch(f"{MODULE}.updater.install_target", return_value=None),
    ):
        controller.check()
        _join("n8n-launcher-update")
    assert controller._events.empty()
    assert probe.asked == []


def test_the_worker_stays_silent_without_a_compatible_asset(tmp_path) -> None:
    controller, probe = make_controller()
    with (
        patch(f"{MODULE}.updater.cleanup_stale"),
        patch(f"{MODULE}.updater.fetch_latest_release", return_value=make_test_release()),
        patch(f"{MODULE}.updater.current_version", return_value="1.0.02"),
        patch(f"{MODULE}.updater.install_target", return_value=tmp_path / "n8n-launcher"),
        patch(f"{MODULE}.updater.compatible_asset", return_value=None),
    ):
        controller.check()
        _join("n8n-launcher-update")
    assert controller._events.empty()
    assert probe.asked == []


def test_a_stale_download_never_overwrites_a_later_prompt(tmp_path) -> None:
    """Two offers in a row: the first download's confirmation is still valid."""
    with ExitStack() as stack:
        stack.enter_context(patch(f"{MODULE}.updates_dir", return_value=tmp_path))
        download = stack.enter_context(patch(f"{MODULE}.updater.download_asset"))
        script = tmp_path / "apply_update.sh"
        stack.enter_context(patch(f"{MODULE}.updater.installer_script", return_value=script))
        spawn = stack.enter_context(patch(f"{MODULE}.updater.spawn_installer"))

        controller, probe = make_controller()
        offer_once(controller, tmp_path)
        drain(controller)
        _join("n8n-launcher-update-dl")
        drain(controller)

    spawn.assert_called_once_with(script)
    assert probe.finished == 1
