"""Close-sequence controller: export workflows, Git sync, then stop n8n."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from ..core.models import Workspace, WorkspaceState
from ..n8n.api import N8nApiClient
from ..n8n.workflows import SyncRunner
from ..workspaces.manager import WorkspaceManager


class CloseController:
    """Run the ordered close flow for every workspace.

    Each step spawns a daemon worker thread and posts the continuation back
    through ``post`` for the Tk event loop. Every Tk interaction (message
    boxes, status bar, root destruction) is passed the injected ``root`` so
    the flow stays unit-testable.
    """

    def __init__(
        self,
        *,
        root: tk.Tk,
        manager: WorkspaceManager,
        set_status: Callable[[str], None],
        post: Callable[[Callable[[], None], Exception | None], None],
        refresh: Callable[[], None],
        set_closing: Callable[[bool], None],
        is_closed: Callable[[], bool],
        finish_close: Callable[[], None],
    ) -> None:
        self._root = root
        self.manager = manager
        self._set_status = set_status
        self._post = post
        self._refresh = refresh
        self._set_closing = set_closing
        self._is_closed = is_closed
        self._finish_close = finish_close

    def begin(self) -> None:
        """Run the ordered close flow; finish immediately when nothing runs."""
        workspaces = self.manager.list()
        if not workspaces:
            self._finish_close()
            return
        self._set_closing(True)
        self._set_status("Fermeture : synchronisation des workflows puis arrêt de n8n…")
        try:
            self.manager.reconcile_all()
        except Exception:
            pass
        self._next(self.manager.list())

    def _next(self, workspaces: list[Workspace]) -> None:
        if not workspaces:
            self._finish_close()
            return
        workspace, rest = workspaces[0], workspaces[1:]
        if workspace.state is WorkspaceState.RUNNING and workspace.api_key:
            self._sync(workspace, rest)
        else:
            self._stop(workspace, rest)

    def _sync(self, workspace: Workspace, rest: list[Workspace]) -> None:
        self._set_status(f"Fermeture : synchronisation de « {workspace.name} »…")

        def worker() -> None:
            try:
                self._export(workspace)
                self.manager.sync_git(workspace, push=True)
            except Exception as exc:
                self._post(
                    (lambda exc=exc: self._ask_sync_retry(workspace, rest, exc)), None
                )
            else:
                if workspace.git_push_failed:
                    self._post(lambda: self._warn_push_failed(workspace), None)
                self._post(lambda: self._stop(workspace, rest), None)

        threading.Thread(target=worker, name="n8n-close-sync", daemon=True).start()

    def _warn_push_failed(self, workspace: Workspace) -> None:
        if self._is_closed():
            return
        messagebox.showwarning(
            "Synchronisation Git",
            f"Les workflows de « {workspace.name} » ont été sauvegardés, mais le push\n"
            "vers le dépôt distant a échoué (connexion ? permissions ?).\n"
            "Les changements restent commités localement.",
            parent=self._root,
        )

    def _export(self, workspace: Workspace) -> None:
        api = N8nApiClient(f"http://127.0.0.1:{workspace.port}/api/v1", workspace.api_key)
        pipelines_dir = workspace.workflows_dir / "n8nPipelines"
        SyncRunner(api, pipelines_dir).export_all()

    def _stop(self, workspace: Workspace, rest: list[Workspace]) -> None:
        self._set_status(f"Fermeture : arrêt de « {workspace.name} »…")

        def worker() -> None:
            try:
                self.manager.stop(workspace.id)
            except Exception as exc:
                self._post(
                    (lambda exc=exc: self._ask_stop_failed(workspace, rest, exc)), None
                )
            else:
                self._post(lambda: self._next(rest), None)

        threading.Thread(target=worker, name="n8n-close-stop", daemon=True).start()

    def _ask_sync_retry(
        self, workspace: Workspace, rest: list[Workspace], error: Exception
    ) -> None:
        if self._is_closed():
            return
        choice = messagebox.askyesnocancel(
            "Synchronisation impossible",
            f"L'export des workflows de « {workspace.name} » a échoué :\n{error}\n\n"
            "« Oui » = réessayer l'export\n"
            "« Non » = arrêter sans synchroniser\n"
            "« Annuler » = garder la fenêtre ouverte.",
            parent=self._root,
        )
        if choice is True:
            self._sync(workspace, rest)
        elif choice is False:
            self._stop(workspace, rest)
        else:
            self._abort()

    def _ask_stop_failed(
        self, workspace: Workspace, rest: list[Workspace], error: Exception
    ) -> None:
        if self._is_closed():
            return
        choice = messagebox.askyesnocancel(
            "Arrêt impossible",
            f"L'arrêt de « {workspace.name} » a échoué :\n{error}\n\n"
            "« Oui » = réessayer l'arrêt\n"
            "« Non » = ignorer et continuer\n"
            "« Annuler » = garder la fenêtre ouverte.",
            parent=self._root,
        )
        if choice is True:
            self._stop(workspace, rest)
        elif choice is False:
            self._next(rest)
        else:
            self._abort()

    def _abort(self) -> None:
        self._set_closing(False)
        self._refresh()
        self._set_status("Fermeture annulée.")