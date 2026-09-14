"""Tkinter desktop interface for workspace lifecycle actions."""

from __future__ import annotations

import os
import platform
import queue
import secrets
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

import requests

from .api_client import N8nApiClient
from .browser import open_app
from .config import ConfigStore
from .docker_manager import DockerManager
from .models import DbConfig, DbMode, Workspace, WorkspaceState
from .sync_runner import SyncRunner
from .workspace_info import (
    db_config_for_folder,
    db_connected,
    db_label,
    git_repo_status,
    pipelines_count,
)
from .workspace_manager import WorkspaceError, WorkspaceManager

APP_BACKGROUND = "#0f172a"
SURFACE = "#1e293b"
SURFACE_HOVER = "#263449"
BORDER = "#334155"
ROW_SELECTED_BG = "#1e2f4f"
ROW_DELETE_BG = "#450a0a"
ROW_DELETE_ACTIVE = "#7f1d1d"
ROW_DELETE_FG = "#f87171"
TEXT_PRIMARY = "#f1f5f9"
TEXT_MUTED = "#94a3b8"
ACCENT = "#3b82f6"
ACCENT_ACTIVE = "#2563eb"
ACCENT_HOVER = "#60a5fa"

STATUS_STYLE = {
    WorkspaceState.STOPPED: ("#334155", "#cbd5e1"),
    WorkspaceState.STARTING: ("#78350f", "#fcd34d"),
    WorkspaceState.RUNNING: ("#064e3b", "#34d399"),
    WorkspaceState.STOPPING: ("#78350f", "#fcd34d"),
    WorkspaceState.ERROR: ("#7f1d1d", "#fca5a5"),
}

CHIP_ACTIVE = ("#064e3b", "#34d399")
CHIP_INACTIVE = ("#7f1d1d", "#fca5a5")
CHIP_NEUTRAL = ("#334155", "#cbd5e1")

WATERMARK_COLOR = "#2b3950"

STATE_LABELS = {
    WorkspaceState.STOPPED: "Arrêté",
    WorkspaceState.STARTING: "Démarrage",
    WorkspaceState.RUNNING: "En cours",
    WorkspaceState.STOPPING: "Arrêt",
    WorkspaceState.ERROR: "Erreur",
}

STATE_POLL_MS = 5000


def state_label(state: WorkspaceState) -> str:
    return STATE_LABELS.get(state, state.value)


def _font_family() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Helvetica Neue"
    if system == "Windows":
        return "Segoe UI"
    return "DejaVu Sans"


FONT_BASE = _font_family()
FONT_TITLE = (FONT_BASE, 15, "bold")
FONT_SUBTITLE = (FONT_BASE, 9)
FONT_ROWS = (FONT_BASE, 11, "bold")
FONT_META = (FONT_BASE, 10)
FONT_STATUS = (FONT_BASE, 9)
FONT_PILL = (FONT_BASE, 9, "bold")
FONT_WATERMARK = (FONT_BASE, 44)


class LauncherApp:
    def __init__(
        self,
        config_store: ConfigStore,
        workspace_manager: WorkspaceManager,
        docker: DockerManager,
        *,
        root: tk.Tk | None = None,
        browser_opener: Callable[[str], None] = open_app,
    ) -> None:
        self.config_store = config_store
        self.workspace_manager = workspace_manager
        self.docker = docker
        self._owns_root = root is None
        self.root = root or tk.Tk()
        self.browser_opener = browser_opener
        self.events: queue.Queue[tuple[Callable[[], None], Exception | None]] = queue.Queue()
        self._closing = False
        self._closed = False
        self._poll_in_flight = False
        self._rows: dict[str, tuple[tk.Frame, tk.Label]] = {}
        self._row_order: list[str] = []
        self._selected_id: str | None = None
        self._launching: str | None = None
        self._status_label: tk.Label | None = None
        self._subtitle: tk.Label | None = None
        self._menu: tk.Menu | None = None
        self._apply_theme()
        self._configure_root()
        self._build_ui()
        self.refresh()
        self.root.after(100, self._drain_events)
        self._poll_states()

    def run(self) -> None:
        self.root.mainloop()

    def _apply_theme(self) -> None:
        try:
            style = ttk.Style(self.root)
            style.theme_use("clam")
            style.configure(".", font=FONT_META, background=APP_BACKGROUND)
            style.configure("TFrame", background=APP_BACKGROUND)
            style.configure(
                "TButton",
                background=BORDER,
                foreground=TEXT_PRIMARY,
                bordercolor=BORDER,
                focuscolor=BORDER,
                padding=(14, 8),
            )
            style.map("TButton", background=[("active", SURFACE_HOVER)])
            style.configure(
                "Accent.TButton",
                background=ACCENT,
                foreground="#ffffff",
                bordercolor=ACCENT,
                focuscolor=ACCENT,
                padding=(16, 8),
            )
            style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE)])
        except Exception:
            pass

    def _configure_root(self) -> None:
        self.root.title("n8n Launcher")
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after_idle(self.root.attributes, "-topmost", False)
            self.root.focus_force()
        except Exception:
            pass
        try:
            self.root.minsize(640, 380)
            self.root.configure(bg=APP_BACKGROUND)
        except Exception:
            pass
        if self._owns_root:
            try:
                self.root.geometry("820x460")
                self.root.update_idletasks()
                x = (self.root.winfo_screenwidth() - 820) // 2
                y = max((self.root.winfo_screenheight() - 460) // 3, 0)
                self.root.geometry(f"+{x}+{y}")
            except Exception:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        accent_bar = tk.Frame(self.root, bg=ACCENT, height=4)
        accent_bar.pack(fill="x")
        try:
            accent_bar.pack_propagate(False)
        except Exception:
            pass

        header = ttk.Frame(self.root, padding=(18, 14, 18, 10))
        header.pack(fill="x")
        title = tk.Label(
            header,
            text="n8n Launcher",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_TITLE,
            anchor="w",
        )
        title.pack(fill="x")
        self._subtitle = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_SUBTITLE,
            anchor="w",
        )
        self._subtitle.pack(fill="x")

        card = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=18, pady=(8, 10))
        self.workspace_list = tk.Frame(card, bg=SURFACE)
        self.workspace_list.pack(fill="both", expand=True, padx=8, pady=8)
        self.workspace_list.bind("<Return>", lambda _event: self.launch_selected())
        self.workspace_list.bind("<Button-1>", self._on_empty_area_click)

        watermark = tk.Label(
            self.workspace_list,
            text="+",
            bg=SURFACE,
            fg=WATERMARK_COLOR,
            font=FONT_WATERMARK,
            anchor="center",
        )
        try:
            watermark.place(relx=0.5, rely=0.5, anchor="center")
        except Exception:
            pass
        self._watermark = watermark
        watermark.bind("<Button-1>", lambda _event: self.prompt_create_workflow())

        self._status_label = tk.Label(
            self.root,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_STATUS,
            anchor="w",
            padx=20,
        )
        self._status_label.pack(fill="x", side="bottom")

        self._menu = tk.Menu(self.root, tearoff=0)
        self._menu.add_command(label="Ouvrir n8n", command=self.launch_selected)
        self._menu.add_command(label="Ouvrir le dossier", command=self.open_workflows)
        self._menu.add_separator()
        self._menu.add_command(label="Supprimer", command=self._delete_selected)

    def _build_row(self, workspace: Workspace) -> tuple[tk.Frame, tk.Label]:
        if workspace.id == self._selected_id:
            row_bg = ROW_SELECTED_BG
            border = ACCENT
        else:
            row_bg = SURFACE
            border = BORDER
        frame = tk.Frame(
            self.workspace_list,
            bg=row_bg,
            highlightthickness=1,
            highlightbackground=border,
        )
        frame.pack(fill="x", pady=3, padx=2)

        name_label = tk.Label(
            frame,
            text=workspace.name,
            bg=row_bg,
            fg=TEXT_PRIMARY,
            font=FONT_ROWS,
            anchor="w",
            padx=8,
        )
        name_label.pack(side="left", fill="x", expand=True)

        delete_button = tk.Button(
            frame,
            text="×",
            font=FONT_TITLE,
            bg=ROW_DELETE_BG,
            fg=ROW_DELETE_FG,
            activebackground=ROW_DELETE_ACTIVE,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=6,
            pady=1,
            cursor="hand2",
            command=lambda wid=workspace.id: self.delete_workspace(wid),
        )
        delete_button.pack(side="right", padx=(6, 8), pady=6)

        port_chip = self._chip(
            frame, text=f":{workspace.port}", palette=CHIP_NEUTRAL
        )
        port_chip.pack(side="right", padx=(6, 0))

        status_palette = STATUS_STYLE.get(
            workspace.state, STATUS_STYLE[WorkspaceState.STOPPED]
        )
        status_label = self._chip(
            frame, text=state_label(workspace.state), palette=status_palette
        )
        status_label.pack(side="right", padx=(6, 0))

        db_palette = CHIP_ACTIVE if db_connected(workspace) else CHIP_INACTIVE
        db_chip = self._chip(frame, text=db_label(workspace), palette=db_palette)
        db_chip.pack(side="right", padx=(6, 0))

        git_palette = (
            CHIP_ACTIVE if git_repo_status(workspace.workflows_dir) else CHIP_INACTIVE
        )
        git_chip = self._chip(frame, text="git", palette=git_palette)
        git_chip.pack(side="right", padx=(6, 0))

        pipelines = pipelines_count(workspace.workflows_dir)
        pipelines_chip = self._chip(
            frame, text=str(pipelines), palette=CHIP_NEUTRAL
        )
        pipelines_chip.pack(side="right", padx=(6, 0))

        name_label.bind(
            "<Button-1>", lambda _event, wid=workspace.id: self._select_row(wid)
        )
        name_label.bind(
            "<Double-Button-1>",
            lambda _event, wid=workspace.id: self._handle_double(wid),
        )
        name_label.bind(
            "<Button-3>",
            lambda event, wid=workspace.id: self._show_context_menu(event, wid),
        )
        frame.bind(
            "<Double-Button-1>",
            lambda _event, wid=workspace.id: self._handle_double(wid),
        )
        try:
            frame.bind(
                "<Enter>", lambda _event, wid=workspace.id: self._on_row_enter(wid)
            )
            frame.bind(
                "<Leave>", lambda _event, wid=workspace.id: self._on_row_leave(wid)
            )
        except Exception:
            pass

        frame.name_label = name_label
        frame.status_label = status_label
        frame.db_chip = db_chip
        frame.git_chip = git_chip
        frame.port_chip = port_chip
        frame.pipelines_chip = pipelines_chip
        frame.delete_button = delete_button
        return frame, name_label

    @staticmethod
    def _chip(
        parent,
        text: str,
        *,
        palette: tuple[str, str],
        font: tuple[str, int, str] = FONT_PILL,
        **kwargs,
    ) -> tk.Label:
        bg, fg = palette
        return tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            anchor="center",
            padx=kwargs.pop("padx", 8),
            pady=kwargs.pop("pady", 2),
        )

    def refresh(self) -> None:
        if self._closed:
            return
        for frame, _label in self._rows.values():
            frame.destroy()
        self._rows = {}
        self._row_order = []
        workspaces = self.workspace_manager.list()
        for workspace in workspaces:
            row = self._build_row(workspace)
            self._rows[workspace.id] = row
            self._row_order.append(workspace.id)
        if self._selected_id is not None and self._selected_id not in self._rows:
            self._selected_id = None
        self._apply_selection_styles()
        if self._subtitle is not None:
            count = len(workspaces)
            if count == 0:
                self._subtitle.config(text="Aucun workflow — cliquez pour en créer un")
            else:
                self._subtitle.config(text=f"{count} workflow{'s' if count != 1 else ''}")

    def _set_row_background(self, workspace_id: str, *, hover: bool = False) -> None:
        frame = self._rows.get(workspace_id, (None, None))[0]
        if frame is None:
            return
        selected = workspace_id == self._selected_id
        if selected:
            background, border = ROW_SELECTED_BG, ACCENT
        elif hover:
            background, border = SURFACE_HOVER, BORDER
        else:
            background, border = SURFACE, BORDER
        frame.config(bg=background, highlightbackground=border)
        try:
            frame.name_label.config(bg=background)
        except Exception:
            pass

    def _on_row_enter(self, workspace_id: str) -> None:
        self._set_row_background(workspace_id, hover=True)

    def _on_row_leave(self, workspace_id: str) -> None:
        self._set_row_background(workspace_id, hover=False)

    def _on_empty_area_click(self, _event: tk.Event) -> None:
        """Open the creation dialog only when the list is empty; otherwise deselect."""
        if not self._row_order:
            self.prompt_create_workflow()
        else:
            self._selected_id = None
            self._apply_selection_styles()
            self.set_status("")

    def _apply_selection_styles(self) -> None:
        for workspace_id in self._rows:
            self._set_row_background(workspace_id, hover=False)

    def set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.config(text=text)

    def _poll_states(self) -> None:
        if self._closed:
            return

        def worker() -> None:
            try:
                changed = self.workspace_manager.reconcile_all()
            except Exception as exc:
                self._poll_in_flight = False
                self.events.put((self.refresh, exc))
            else:
                self._poll_in_flight = False
                if changed:
                    self.events.put((self.refresh, None))

        # Skip the cycle if the previous reconcile is still running (docker calls
        # can exceed the 5s poll interval) to avoid stacking worker threads.
        if self._poll_in_flight:
            try:
                self.root.after(STATE_POLL_MS, self._poll_states)
            except Exception:
                pass
            return
        self._poll_in_flight = True
        threading.Thread(target=worker, name="n8n-launcher-poll", daemon=True).start()
        try:
            self.root.after(STATE_POLL_MS, self._poll_states)
        except Exception:
            pass

    def _select_row(self, workspace_id: str) -> None:
        self._selected_id = workspace_id
        self._apply_selection_styles()
        try:
            workspace = self._selected()
        except ValueError:
            return
        self.set_status(f"{workspace.name} · :{workspace.port} · {state_label(workspace.state)}")

    def _handle_double(self, workspace_id: str) -> None:
        self._select_row(workspace_id)
        self.launch_selected()

    def _show_context_menu(self, event, workspace_id: str | None = None) -> None:
        if workspace_id is not None:
            self._select_row(workspace_id)
        elif self._selected_id is not None:
            self._select_row(self._selected_id)
        if self._menu is not None:
            self._menu.tk_popup(event.x_root, event.y_root)

    def prompt_create_workflow(self) -> None:
        name = simpledialog.askstring("Nouveau workflow", "Nom du workflow :", parent=self.root)
        if not name:
            return
        directory = filedialog.askdirectory(
            title="Dossier du workspace (n8nPipelines + db/)", parent=self.root
        )
        if not directory:
            return
        workflows_dir = Path(directory)
        workflows_dir.mkdir(parents=True, exist_ok=True)
        folder_empty = not any(workflows_dir.iterdir())
        database = db_config_for_folder(workflows_dir)
        if database is None and folder_empty:
            choice = messagebox.askyesnocancel(
                "Nouveau workflow",
                "Ce dossier est vide. Quelle base de données pour les workflows ?\n\n"
                "« Oui » = base locale (gérée, schéma + migrations auto)\n"
                "« Non » = base distante (connexion string)\n"
                "« Annuler » = aucune base (workflows sauvegardés en JSON)",
                parent=self.root,
            )
            if choice is True:
                database = self._fresh_managed_db_config()
            elif choice is False:
                connection_string = simpledialog.askstring(
                    "Nouveau workflow", "Connexion string de la base distante :", parent=self.root
                )
                if not connection_string:
                    messagebox.showinfo(
                        "Nouveau workflow",
                        "Création annulée : aucune connexion distante fournie.",
                        parent=self.root,
                    )
                    return
                database = DbConfig(DbMode.EXTERNAL, connection_string=connection_string.strip())
            else:
                database = DbConfig(DbMode.NONE)
        elif database is None:
            database = DbConfig(DbMode.NONE)
        self._run_async(
            lambda: self.workspace_manager.create(name.strip(), workflows_dir, db=database),
            on_success=self._refresh_with_selection,
        )

    @staticmethod
    def _fresh_managed_db_config() -> DbConfig:
        return DbConfig(
            mode=DbMode.MANAGED,
            database_name="data",
            username="n8ndata",
            password=secrets.token_hex(16),
        )

    def launch_selected(self) -> None:
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        if self._launching == workspace.id:
            return
        self._launching = workspace.id
        url = f"http://127.0.0.1:{workspace.port}"

        def action() -> None:
            try:
                self._ensure_running(workspace)
            finally:
                self._launching = None

        self._run_async(
            action,
            on_success=lambda: self.browser_opener(url),
        )

    def open_workflows(self) -> None:
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        path = workspace.workflows_dir
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _ensure_running(self, workspace: Workspace) -> None:
        current = next(
            (item for item in self.workspace_manager.list() if item.id == workspace.id),
            None,
        )
        if current is None:
            raise WorkspaceError(f"Unknown workspace: {workspace.id}")
        needs_bootstrap = not bool(current.api_key)
        self.workspace_manager.ensure_running(current.id)
        self._wait_until_healthy(current.port)
        if needs_bootstrap:
            email = self.config_store.load().owner_email
            self.events.put(
                (
                    lambda email=email: messagebox.showinfo(
                        "n8n Launcher",
                        f"n8n préconfiguré. Connectez-vous avec {email} "
                        "(mot de passe défini à l'installation).",
                        parent=self.root,
                    ),
                    None,
                )
            )

    def _wait_until_healthy(
        self, port: int, *, interval: float = 2.0, timeout: float = 120.0
    ) -> None:
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{port}/healthz"
        while True:
            try:
                response = requests.get(url, timeout=2.0)
                if response.ok:
                    return
            except requests.RequestException:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"n8n did not become ready on {url} within {timeout:.0f}s"
                )
            time.sleep(interval)

    def _selected(self) -> Workspace:
        if self._selected_id is None:
            raise ValueError("Select a workflow first")
        for workspace in self.workspace_manager.list():
            if workspace.id == self._selected_id:
                return workspace
        raise ValueError("Select a workflow first")

    def _selected_or_warn(self) -> Workspace | None:
        try:
            return self._selected()
        except ValueError as exc:
            messagebox.showwarning("n8n Launcher", str(exc), parent=self.root)
            return None

    def _refresh_with_selection(self) -> None:
        self.refresh()
        if self._row_order:
            self._select_row(self._row_order[-1])

    def delete_workspace(self, workspace_id: str) -> None:
        if self._closing:
            return
        current = next(
            (item for item in self.workspace_manager.list() if item.id == workspace_id),
            None,
        )
        if current is None:
            return
        running_note = (
            "" if current.state is WorkspaceState.STOPPED else "\n(Docker sera arrêté au préalable.)"
        )
        confirmed = messagebox.askyesno(
            "Supprimer le workflow",
            f"Retirer « {current.name} » de n8n Launcher ?\n\n"
            f"Le dossier « {current.workflows_dir} » sera conservé sur le disque.{running_note}",
            parent=self.root,
        )
        if not confirmed:
            return
        self._delete_after_confirm(workspace_id)

    def _delete_selected(self) -> None:
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        self.delete_workspace(workspace.id)

    def _delete_after_confirm(self, workspace_id: str) -> None:
        running = any(
            item.id == workspace_id and item.state is not WorkspaceState.STOPPED
            for item in self.workspace_manager.list()
        )

        def action() -> None:
            if running:
                self.workspace_manager.stop(workspace_id)
            self.workspace_manager.delete(workspace_id)
            self._selected_id = None

        self._run_async(action)

    def _run_async(
        self,
        action: Callable[[], None],
        *,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self.events.put((self.refresh, exc))
            else:
                self.events.put((on_success or self.refresh, None))

        threading.Thread(target=worker, name="n8n-launcher-action", daemon=True).start()

    def _drain_events(self) -> None:
        if self._closed:
            return
        try:
            while True:
                callback, error = self.events.get_nowait()
                try:
                    callback()
                    if error:
                        messagebox.showerror("n8n Launcher", str(error), parent=self.root)
                # A raising callback (e.g. browser launch) must not kill the
                # event loop silently — surface it and keep draining.
                except Exception as exc:
                    messagebox.showerror("n8n Launcher", str(exc), parent=self.root)
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._drain_events)
        except Exception:
            pass

    def on_close(self) -> None:
        if self._closing:
            return
        workspaces = self.workspace_manager.list()
        if not workspaces:
            self._finish_close()
            return
        self._closing = True
        self.set_status("Fermeture : synchronisation des workflows puis arrêt de n8n…")
        try:
            self.workspace_manager.reconcile_all()
        except Exception:
            pass
        workspaces = self.workspace_manager.list()
        self._close_next(workspaces)

    def _close_next(self, workspaces: list[Workspace]) -> None:
        if not workspaces:
            self._finish_close()
            return
        workspace, rest = workspaces[0], workspaces[1:]
        if workspace.state is WorkspaceState.RUNNING and workspace.api_key:
            self._close_sync(workspace, rest)
        else:
            self._close_stop(workspace, rest)

    def _close_sync(self, workspace: Workspace, rest: list[Workspace]) -> None:
        def worker() -> None:
            try:
                self._export_workflows(workspace)
            except Exception as exc:
                self.events.put(
                    (lambda exc=exc: self._ask_sync_retry(workspace, rest, exc), None)
                )
            else:
                self.events.put((lambda: self._close_stop(workspace, rest), None))

        threading.Thread(target=worker, name="n8n-close-sync", daemon=True).start()

    def _export_workflows(self, workspace: Workspace) -> None:
        api = N8nApiClient(f"http://127.0.0.1:{workspace.port}/api/v1", workspace.api_key)
        pipelines_dir = workspace.workflows_dir / "n8nPipelines"
        SyncRunner(api, pipelines_dir).export_all()

    def _close_stop(self, workspace: Workspace, rest: list[Workspace]) -> None:
        def worker() -> None:
            try:
                self.workspace_manager.stop(workspace.id)
            except Exception as exc:
                self.events.put(
                    (lambda exc=exc: self._ask_stop_failed(workspace, rest, exc), None)
                )
            else:
                self.events.put((lambda: self._close_next(rest), None))

        threading.Thread(target=worker, name="n8n-close-stop", daemon=True).start()

    def _ask_sync_retry(self, workspace: Workspace, rest: list[Workspace], error: Exception) -> None:
        if self._closed:
            return
        choice = messagebox.askyesnocancel(
            "Synchronisation impossible",
            f"L'export des workflows de « {workspace.name} » a échoué :\n{error}\n\n"
            "« Oui » = réessayer l'export\n"
            "« Non » = arrêter sans synchroniser\n"
            "« Annuler » = garder la fenêtre ouverte.",
            parent=self.root,
        )
        if choice is True:
            self._close_sync(workspace, rest)
        elif choice is False:
            self._close_stop(workspace, rest)
        else:
            self._abort_close()

    def _ask_stop_failed(self, workspace: Workspace, rest: list[Workspace], error: Exception) -> None:
        if self._closed:
            return
        choice = messagebox.askyesnocancel(
            "Arrêt impossible",
            f"L'arrêt de « {workspace.name} » a échoué :\n{error}\n\n"
            "« Oui » = réessayer l'arrêt\n"
            "« Non » = ignorer et continuer\n"
            "« Annuler » = garder la fenêtre ouverte.",
            parent=self.root,
        )
        if choice is True:
            self._close_stop(workspace, rest)
        elif choice is False:
            self._close_next(rest)
        else:
            self._abort_close()

    def _abort_close(self) -> None:
        self._closing = False
        self.refresh()
        self.set_status("Fermeture annulée.")

    def _finish_close(self) -> None:
        self._closed = True
        self.root.destroy()