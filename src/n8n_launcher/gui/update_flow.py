"""Update-flow controller: check, offer, download and self-install."""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox
from typing import cast

from ..core.paths import updates_dir
from ..platform import updater
from ..platform.updater import Asset, Release
from .theme import ACCENT


class UpdateController:
    """Check for a newer launcher release and drive the update.

    The launch-time check runs on a daemon thread; results are routed back
    through the app event queue. Tk-bound side effects are limited to the
    injected ``root``/``status_label`` so the flow stays unit-testable.
    """

    def __init__(
        self,
        *,
        root: tk.Tk,
        events: queue.Queue,
        set_status: Callable[[str], None],
        status_label: tk.Label | None,
        is_closed: Callable[[], bool],
        finish_close: Callable[[], None],
    ) -> None:
        self._root = root
        self._events = events
        self._set_status = set_status
        self._status_label = status_label
        self._is_closed = is_closed
        self._finish_close = finish_close

    def setup(self) -> None:
        """Schedule the launch-time check when the app runs from a bundle."""
        if updater.install_target() is not None:
            with contextlib.suppress(Exception):
                self._root.after(1500, self.check)

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
        if os.access(target.parent, os.W_OK):
            self._events.put((lambda: self._offer(release, asset), None))
        else:
            self._events.put((lambda: self._show_link(release), None))

    def _show_link(self, release: Release) -> None:
        """Point the status bar at the release page when the app is not writable.

        Clicking the message opens ``releases/latest`` in the browser; the
        binding is reset by the next ``set_status`` call.
        """
        if self._is_closed():
            return
        self._set_status(f"Nouvelle version {release.tag_name} disponible — cliquer pour ouvrir")
        label = self._status_label
        if label is None:
            return
        try:
            label.config(cursor="hand2", fg=ACCENT)
            label.bind("<Button-1>", lambda _event: webbrowser.open(updater.release_page_url()))
        except Exception:
            pass

    def _offer(self, release: Release, asset: Asset) -> None:
        if self._is_closed():
            return
        if not messagebox.askyesno(
            "Mise à jour disponible",
            f"Une nouvelle version de n8n Launcher est disponible :\n\n"
            f"{updater.current_version()} → {release.tag_name}\n\n"
            "Voulez-vous la télécharger et l'installer ?\n"
            "L'application redémarrera automatiquement.",
            parent=self._root,
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
        """Stream the asset to ``dest`` and report a throttle progress message."""
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
            self._events.put(
                (
                    lambda: messagebox.showerror("Mise à jour", message, parent=self._root),
                    None,
                )
            )
            return
        self._events.put((lambda: self._confirm_install(dest), None))

    def _confirm_install(self, dest: Path) -> None:
        if self._is_closed():
            return
        if not messagebox.askyesno(
            "Mise à jour prête",
            "La nouvelle version est téléchargée.\n"
            "Redémarrer n8n Launcher maintenant pour l'appliquer ?",
            parent=self._root,
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
            messagebox.showerror("Mise à jour", str(exc), parent=self._root)
            return
        self._finish_close()
