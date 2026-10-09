"""Update flow: check for a newer launcher release, offer it, download, self-install.

Every check is best effort and every outcome is a *question asked to the user*.
Both facts belong to this module: an update check must never delay or disturb a
launch, and a user who does not want the update is not an error. What the user
is asked is therefore injected rather than drawn — :meth:`UpdateController.setup`
schedules :meth:`check`, and the caller supplies ``on_offer`` / ``on_error`` /
``on_status_link`` for however it puts a question or a message on screen. A
dialog, a prompt or a log line all drive the same flow.

All the network and filesystem work already lives in :mod:`.updater`; this is the
ordering around it — run the check off the launch path, route the answer back to
the caller's thread, download on a worker, and only restart once the asset has
been handed to the installer.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ..core.paths import updates_dir
from . import updater
from .updater import Asset, Release

#: How long after launch the automatic check fires. Long enough that the window
#: is up and usable, short enough that the offer lands while it is still fresh.
LAUNCH_CHECK_DELAY_MS = 1500


class UpdateController:
    """Check for a newer launcher release and drive the update.

    The launch-time check runs on a daemon thread; results are routed back
    through the ``events`` queue so they are applied on the caller's own thread
    (the GUI's event loop, a CLI's prompt). ``is_closed`` guards every step that
    would otherwise raise into a window that no longer exists.
    """

    def __init__(
        self,
        *,
        events: queue.Queue,
        set_status: Callable[[str], None],
        on_offer: Callable[[str, str], bool],
        on_error: Callable[[str, str], None],
        on_status_link: Callable[[str, str], None],
        schedule: Callable[[int, Callable[[], None]], None],
        is_closed: Callable[[], bool],
        finish_close: Callable[[], None],
    ) -> None:
        """Bind the event queue, the caller's questions and its close guard."""
        self._events = events
        self._set_status = set_status
        self._on_offer = on_offer
        self._on_error = on_error
        self._on_status_link = on_status_link
        self._schedule = schedule
        self._is_closed = is_closed
        self._finish_close = finish_close

    def setup(self) -> None:
        """Schedule the launch-time check when the app runs from a bundle.

        A source checkout has no install target, so there is nothing to replace
        there; the failure path of :meth:`check` is not even entered.
        """
        if updater.install_target() is not None:
            with contextlib.suppress(Exception):
                self._schedule(LAUNCH_CHECK_DELAY_MS, self.check)

    def check(self) -> None:
        """Kick off the update check on a background thread (never blocks launch)."""
        if self._is_closed():
            return
        threading.Thread(
            target=self._worker,
            name="n8n-launcher-update",
            daemon=True,
        ).start()

    def _worker(self) -> None:
        """Fetch the latest release and route it to an offer or a status link.

        Any failure (offline, rate limit, malformed payload, wrong platform or
        no newer version) is deliberately swallowed — an update check must
        never disturb the user.
        """
        updater.cleanup_stale()
        try:
            release = updater.fetch_latest_release()
        except Exception:
            return
        target = updater.install_target()
        if target is None or not updater.release_is_newer(release, updater.current_version()):
            return
        asset = updater.compatible_asset(release)
        if asset is None:
            return
        if os.access(target, os.W_OK):
            self._events.put((lambda: self._offer(release, asset), None))
        else:
            self._events.put((lambda: self._show_link(release), None))

    def _show_link(self, release: Release) -> None:
        """Point the status bar at the release page when the app is not writable.

        Clicking the message opens ``releases/latest`` in the browser, so the
        user gets the same offer as a writable install, just delivered by hand.
        """
        if self._is_closed():
            return
        self._on_status_link(
            f"Nouvelle version {release.tag_name} disponible — cliquer pour ouvrir",
            updater.release_page_url(),
        )

    def _offer(self, release: Release, asset: Asset) -> None:
        """Ask whether to install, then download on a worker thread."""
        if self._is_closed():
            return
        if not self._on_offer(
            "Mise à jour disponible",
            f"Une nouvelle version de n8n Launcher est disponible :\n\n"
            f"{updater.current_version()} -> {release.tag_name}\n\n"
            "Voulez-vous la télécharger et l'installer ?\n"
            "L'application redémarrera automatiquement.",
        ):
            return
        self._set_status(f"Téléchargement de la mise à jour {release.tag_name}…")
        dest = updates_dir() / asset.name
        threading.Thread(
            target=self._download,
            args=(asset, dest),
            name="n8n-launcher-update-dl",
            daemon=True,
        ).start()

    def _download(self, asset: Asset, dest: Path) -> None:
        """Stream the asset to *dest* and report a throttle progress message."""
        last_megabyte = 0

        def progress(received: int) -> None:
            nonlocal last_megabyte
            megabyte = received // (1024 * 1024)
            if megabyte != last_megabyte:
                last_megabyte = megabyte
                self._events.put(
                    (
                        lambda mb=megabyte: self._set_status(
                            f"Téléchargement de la mise à jour… {mb} Mo"
                        ),
                        None,
                    )
                )

        try:
            updater.download_asset(asset.url, dest, expected_size=asset.size, progress=progress)
        except Exception as exc:
            message = f"Téléchargement impossible : {exc}"
            self._events.put((lambda: self._on_error("Mise à jour", message), None))
            return
        self._events.put((lambda: self._confirm_install(dest), None))

    def _confirm_install(self, dest: Path) -> None:
        """Ask whether to restart now, and hand the installer its script if so."""
        if self._is_closed():
            return
        if not self._on_offer(
            "Mise à jour prête",
            "La nouvelle version est téléchargée.\n"
            "Redémarrer n8n Launcher maintenant pour l'appliquer ?",
        ):
            self._set_status("")
            return
        try:
            target = updater.install_target()
            # check() is only planned (and the worker only ever offers an
            # update) when install_target() is not None, so target cannot be
            # None at this point; the cast lets the None case keep flowing into
            # installer_script's own failure path exactly as before.
            script = updater.installer_script(dest, cast("Path", target))
            updater.spawn_installer(script)
        except Exception as exc:
            self._on_error("Mise à jour", str(exc))
            return
        self._finish_close()


__all__ = ["LAUNCH_CHECK_DELAY_MS", "UpdateController"]
