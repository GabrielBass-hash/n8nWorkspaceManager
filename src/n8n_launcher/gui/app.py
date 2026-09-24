"""Tkinter desktop interface for workspace lifecycle actions."""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Protocol, cast

import requests

from ..core.config import ConfigStore
from ..core.models import Workspace, WorkspaceState
from ..core.paths import browser_app_dir
from ..core.throttle import Throttle
from ..docker.manager import DockerManager
from ..git.manager import git_seed_remote
from ..github import auth
from ..github.api import GitHubClient, GitHubError
from ..platform.browser import open_app, open_url
from ..workspaces import ci, ci_runs
from ..workspaces.ci_runs import RunsSnapshot
from ..workspaces.manager import WorkspaceError, WorkspaceManager
from . import ci_edit, display
from .ci_runs import RunsPanel
from .close import CloseController
from .dialogs import (
    CreatePlan,
    GitHubCreatePlan,
    GitHubRepoPick,
    _repo_name_from,
    default_creation_db,
    prompt_clone_dest,
    prompt_clone_plan,
    prompt_create_dir,
    prompt_create_plan,
    prompt_create_source,
    prompt_db_config,
    prompt_git_config,
    prompt_git_remote,
    prompt_github_create,
    prompt_github_repo_picker,
    prompt_github_token,
    prompt_server_config,
)
from .theme import (
    ACCENT,
    ACCENT_ACTIVE,
    APP_BACKGROUND,
    BORDER,
    BORDER_STRONG,
    CHIP_ACTIVE,
    CHIP_INACTIVE,
    CHIP_NEUTRAL,
    CHIP_WARN,
    FONT_EMPTY_BADGE,
    FONT_EMPTY_TITLE,
    FONT_META,
    FONT_PILL,
    FONT_ROWS,
    FONT_STATUS,
    FONT_SUBTITLE,
    FONT_TITLE,
    ROW_SELECTED_BG,
    STATE_POLL_MS,
    SURFACE,
    SURFACE_ACTIVE,
    SURFACE_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    configure_fonts,
    state_label,
)
from .update_flow import UpdateController

logger = logging.getLogger(__name__)

# The main window is sized as a clamped fraction of the physical screen (see
# ``window_size``): a fixed 980x600 geometry is a postage stamp on
# high-resolution displays (e.g. 7680x2160) and an oversized box on small
# laptops. Fonts stay point-sized, so the UI reflows with ``tk scaling``.
# ``DEFAULT_WINDOW_*`` are the fallback geometry when screen metrics are
# unavailable (a metric of 1 pixel means Tk never mapped the window).
MIN_WINDOW_WIDTH = 720
MIN_WINDOW_HEIGHT = 460
MAX_WINDOW_WIDTH = 1280
MAX_WINDOW_HEIGHT = 820
WINDOW_WIDTH_FRACTION = 0.6
WINDOW_HEIGHT_FRACTION = 0.72
DEFAULT_WINDOW_WIDTH = 980
DEFAULT_WINDOW_HEIGHT = 600

# Concurrent workspace starts are bounded by a semaphore: allowing eight
# "Démarrer" clicks to spawn eight ``docker compose up`` at once would thresh
# on image pulls and saturate the daemon. Three in flight is a reasonable
# ceiling for a desktop box.
_MAX_CONCURRENT_STARTS = 3

# Ceiling on simultaneously-running background actions (every ``_run_async``
# worker). Threads are created per action, but the docker/git/ssh subprocess
# work behind them is the scarce resource: at ~100 workspaces the pool must
# not pile hundreds of ``docker compose`` invocations onto one daemon.
_MAX_BACKGROUND_ACTIONS = 8

# Freshness window for a cached CI runs snapshot: the "Déroulement" tab
# auto-polls every few seconds while a run is in flight, and each poll would
# re-list runs (plus re-download the focus run's logs). A snapshot younger
# than this is served straight from the cache.
CI_RUNS_TTL_SECONDS = 30.0

# Worker threads used to download the focus run's job logs in parallel; the
# GitHub jobs API is fast but log bodies are large, so four downloads in
# flight keep the dialog from stalling on a single slow body.
_CI_LOG_WORKERS = 4


def window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    """Pick the main-window geometry as a clamped fraction of the screen."""
    width = (
        round(screen_width * WINDOW_WIDTH_FRACTION) if screen_width > 1 else DEFAULT_WINDOW_WIDTH
    )
    height = (
        round(screen_height * WINDOW_HEIGHT_FRACTION)
        if screen_height > 1
        else DEFAULT_WINDOW_HEIGHT
    )
    width = min(max(width, MIN_WINDOW_WIDTH), MAX_WINDOW_WIDTH)
    height = min(max(height, MIN_WINDOW_HEIGHT), MAX_WINDOW_HEIGHT)
    return width, height


def open_n8n_app(url: str, profile_dir: Path) -> None:
    """Open n8n in an isolated app-mode browser window for a workspace."""
    open_app(url, profile_dir=profile_dir)


def _api_router_mounted(response: requests.Response) -> bool:
    """Return True when the response proves the n8n public API router is up.

    n8n serves ``/api/v1/*`` with 404 while its HTTP app is still warm
    restarting after boot. 200/401/403 mean the router is mounted and reacting
    (401 merely flags a missing or unusable API key). Unit-test fakes expose
    only ``.ok``; an ok response is treated as mounted.
    """
    status = getattr(response, "status_code", 200 if response.ok else 404)
    return status in (200, 401, 403)


class RowFrame(Protocol):
    """Description of a workspace row.

    Never instantiated or subclassed: a row stays a plain ``tk.Frame`` created
    against whatever ``tk`` module is in effect (the unit-test ``FakeTk``
    substitutes that module, and subclassing the real ``tkinter.Frame`` at
    import time would bypass the swap). ``_build_row`` bridges the structure
    with a single ``cast``.
    """

    name_label: tk.Label
    db_chip: tk.Label
    git_chip: tk.Label
    ci_chip: tk.Label
    server_chip: tk.Label
    port_chip: tk.Label
    pipelines_chip: tk.Label
    action_button: ttk.Button
    dirty_dot: tk.Label

    def pack(self, **kwargs: object) -> None: ...
    def bind(
        self,
        sequence: str,
        func: Callable[[object], None] | None,
        add: str | bool = ...,
    ) -> object: ...
    def config(self, **kwargs: object) -> object: ...
    def destroy(self) -> None: ...


class LauncherApp:
    def __init__(
        self,
        config_store: ConfigStore,
        workspace_manager: WorkspaceManager,
        docker: DockerManager,
        *,
        root: tk.Tk | None = None,
        browser_opener: Callable[[str, Path], None] = open_n8n_app,
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
        # Re-entrancy guard for the creation wizard: a double-click on the
        # list's free area fires two ``Button-1`` events and must open the
        # dialog only once.
        self._creating = False
        self._rows: dict[str, tuple[RowFrame, tk.Label]] = {}
        self._row_order: list[str] = []
        self._selected_id: str | None = None
        self._launching: set[str] = set()
        self._start_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_STARTS)
        # Shared budget for background workers (see _MAX_BACKGROUND_ACTIONS).
        self._background_throttle = Throttle(_MAX_BACKGROUND_ACTIONS)
        # True while a publish is in flight: a second "Publier sur le serveur…"
        # (double-click or re-entry from the context menu) must not overlap the
        # running deployment's git push or marker polling.
        self._deploying = False
        self._status_label: tk.Label | None = None
        self._subtitle: tk.Label | None = None
        self._menu: tk.Menu | None = None
        # GitHub token for the REST calls (Actions runs, repo creation). It is
        # resolved silently from the OS Git credential helper / ``gh`` CLI — no
        # manual configuration — and only prompted for as a last resort. The
        # runs cache is keyed by workspace id so reopening the CI dialog shows
        # the last fetched snapshot instantly.
        self._ci_token: str | None = None
        self._ci_token_declined = False
        self._ci_runs_cache: dict[str, ci_runs.RunsSnapshot] = {}
        # Mirrors ``_poll_in_flight`` for the run panel: the auto-refresh must
        # never stack overlapping fetch workers when a poll takes longer than
        # the cadence (5s while a run is in flight).
        self._ci_runs_fetch_in_flight = False
        # One keep-alive session for every GitHub call (run lists, jobs, logs):
        # a fresh ``requests.Session`` per request would pay a TCP+TLS
        # handshake each time. The runs TTL lets the auto-polling panel serve
        # a snapshot younger than ``_CI_RUNS_TTL_SECONDS`` from the cache
        # instead of spending the API budget on every 5s tick.
        self._github_session = requests.Session()
        self._ci_runs_fetched_at: dict[str, float] = {}
        self._apply_theme()
        self._configure_root()
        self._build_ui()
        self.refresh()
        self.root.after(100, self._drain_events)
        self._poll_states()
        self.close_flow = CloseController(
            root=self.root,
            manager=self.workspace_manager,
            set_status=self.set_status,
            post=lambda callback, error=None: self.events.put((callback, error)),
            refresh=self.refresh,
            set_closing=self._set_closing,
            is_closed=lambda: self._closed,
            finish_close=self._finish_close,
        )
        self.update_flow = UpdateController(
            root=self.root,
            events=self.events,
            set_status=self.set_status,
            status_label=self._status_label,
            is_closed=lambda: self._closed,
            finish_close=self._finish_close,
        )
        self.update_flow.setup()

    def run(self) -> None:
        self.root.mainloop()

    def _set_closing(self, value: bool) -> None:
        self._closing = value

    def _apply_theme(self) -> None:
        try:
            # Named fonts must exist before any widget or style resolves them;
            # the family is probed from the running Tk (case-insensitively) to
            # avoid the silent ``fixed`` fallback that made the UI unreadable.
            configure_fonts(self.root)
            style = ttk.Style(self.root)
            style.theme_use("clam")
            style.configure(".", font=FONT_META, background=APP_BACKGROUND)
            style.configure("TFrame", background=APP_BACKGROUND)

            # Primary CTA (Démarrer, Créer, Valider)
            style.configure(
                "Accent.TButton",
                background=ACCENT,
                foreground="#ffffff",
                bordercolor=ACCENT,
                focuscolor=ACCENT,
                font=FONT_PILL,
                padding=(10, 3),
            )
            style.map(
                "Accent.TButton",
                background=[("active", ACCENT_ACTIVE), ("disabled", BORDER)],
                foreground=[("disabled", TEXT_MUTED)],
            )

            # Secondary / neutral buttons (Arrêter, Annuler, etc.)
            style.configure(
                "Secondary.TButton",
                background=BORDER,
                foreground=TEXT_PRIMARY,
                bordercolor=BORDER,
                focuscolor=BORDER,
                font=FONT_PILL,
                padding=(10, 3),
            )
            style.map(
                "Secondary.TButton",
                background=[("active", SURFACE_HOVER), ("disabled", SURFACE)],
                foreground=[("disabled", TEXT_MUTED)],
            )

            # Surface button (CI "Enregistrer")
            style.configure(
                "Surface.TButton",
                background=SURFACE_HOVER,
                foreground=TEXT_PRIMARY,
                bordercolor=BORDER,
                focuscolor=SURFACE_HOVER,
                font=FONT_PILL,
                padding=(10, 4),
            )
            style.map(
                "Surface.TButton",
                background=[("active", SURFACE_ACTIVE)],
            )

            # Treeview — dark background for CI dialogs.
            # The clam theme sizes tree rows from the font metrics at style-build
            # time; the point-sized named fonts grow with ``tk scaling`` (DPI),
            # so on HiDPI the fixed default row height ends up shorter than the
            # text and the lines of list dialogs (repo picker, CI trees) collapse
            # onto each other. Measure the registered font's real line height and
            # enforce it as an explicit rowheight.
            try:
                import tkinter.font as tkfont

                rowheight = tkfont.nametofont(FONT_META).metrics("linespace") + 6
            except Exception:
                rowheight = 24
            style.configure(
                "Treeview",
                background=SURFACE,
                fieldbackground=SURFACE,
                foreground=TEXT_PRIMARY,
                bordercolor=BORDER,
                font=FONT_META,
                rowheight=max(20, rowheight),
            )
            style.configure(
                "Treeview.Heading",
                background=BORDER,
                foreground=TEXT_PRIMARY,
                font=FONT_META,
            )
            style.map(
                "Treeview",
                background=[("selected", ROW_SELECTED_BG)],
                foreground=[("selected", TEXT_PRIMARY)],
            )
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
            self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
            self.root.configure(bg=APP_BACKGROUND)
        except Exception:
            pass
        if self._owns_root:
            try:
                width, height = window_size(
                    self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                )
                self.root.geometry(f"{width}x{height}")
                self.root.update_idletasks()
                x = (self.root.winfo_screenwidth() - width) // 2
                y = max((self.root.winfo_screenheight() - height) // 3, 0)
                self.root.geometry(f"+{x}+{y}")
            except Exception:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        accent_bar = tk.Frame(self.root, bg=ACCENT, height=4)
        accent_bar.pack(fill="x", side="top")
        with contextlib.suppress(Exception):
            accent_bar.pack_propagate(False)

        header = ttk.Frame(self.root, padding=(18, 14, 18, 10))
        header.pack(fill="x", side="top")
        title = tk.Label(
            header,
            text="n8n Launcher",
            bg=APP_BACKGROUND,
            fg=TEXT_PRIMARY,
            font=FONT_TITLE,
            anchor="w",
        )
        title.pack(side="left", fill="x", expand=True)
        self._subtitle = tk.Label(
            header,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_SUBTITLE,
            anchor="w",
        )
        self._subtitle.pack(side="left", fill="x")

        status_border = tk.Frame(self.root, bg=BORDER_STRONG, height=1)
        status_border.pack(fill="x", side="bottom")
        self._status_label = tk.Label(
            self.root,
            text="",
            bg=APP_BACKGROUND,
            fg=TEXT_MUTED,
            font=FONT_STATUS,
            anchor="w",
            padx=20,
            pady=6,
        )
        self._status_label.pack(fill="x", side="bottom")

        card = tk.Frame(self.root, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True, padx=18, pady=(8, 16))
        self._list_canvas = tk.Canvas(card, bg=SURFACE, highlightthickness=0)
        self._list_canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.workspace_list = tk.Frame(self._list_canvas, bg=SURFACE)
        self._list_window = self._list_canvas.create_window(
            0, 0, window=self.workspace_list, anchor="nw"
        )
        self.workspace_list.bind("<Return>", lambda _event: self.launch_selected())
        self.workspace_list.bind("<Button-1>", self._on_list_click)

        # Scrolling with the mouse wheel only (no visible scrollbar): bindings
        # on the list also fire for wheel events over its child rows, so both
        # the frame and the surrounding canvas are covered.
        for widget in (self._list_canvas, self.workspace_list):
            widget.bind("<MouseWheel>", self._on_mousewheel)
            widget.bind("<Button-4>", self._on_wheel_linux)
            widget.bind("<Button-5>", self._on_wheel_linux)
        self._list_canvas.bind("<Configure>", self._on_canvas_resize)
        # The canvas owns the dead zone *below* the last row (the inner frame's
        # height collapses to the rows' own height), so it needs the creation
        # binding too — otherwise that visible free area swallows clicks.
        self._list_canvas.bind("<Button-1>", self._on_list_click)

        self._empty_state = self._build_empty_state()
        self._create_affordance = self._build_create_affordance()
        self._toggle_empty_state()

    def _build_context_menu(self, workspace: Workspace) -> None:
        """Rebuild the row context menu, gating the server actions to deployment."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Ouvrir n8n", command=self.launch_selected)
        menu.add_command(label="Ouvrir le dossier", command=self.open_workflows)
        if workspace.server.enabled:
            menu.add_separator()
            menu.add_command(label="Publier sur le serveur…", command=self.publish_selected)
            menu.add_command(label="Désactiver le serveur", command=self.disable_server_selected)
        menu.add_separator()
        menu.add_command(label="Configurer Git…", command=self.configure_git_selected)
        menu.add_command(
            label="Configurer les tests GitHub Actions…",
            command=self.configure_ci_selected,
        )
        menu.add_command(
            label="Gérer les credentials CI…",
            command=self.configure_ci_credentials_selected,
        )
        menu.add_command(
            label="Ouvrir les Actions GitHub…",
            command=self.open_ci_actions,
        )
        menu.add_command(
            label="Désactiver les tests CI",
            command=self.disable_ci_selected,
        )
        menu.add_command(
            label="Configurer le token GitHub…",
            command=self.configure_github_token,
        )
        menu.add_command(
            label="Configurer le serveur…",
            command=self.configure_server_selected,
        )
        menu.add_separator()
        menu.add_command(label="Supprimer", command=self._delete_selected)
        self._menu = menu

    def _on_mousewheel(self, event: tk.Event) -> None:
        """Scroll the list on Windows/macOS wheel deltas."""
        self._list_canvas.yview_scroll(-int(event.delta) // 120, "units")

    def _on_wheel_linux(self, event: tk.Event) -> None:
        """Scroll the list on Linux wheel buttons (4 = up, 5 = down)."""
        direction = -1 if getattr(event, "num", 0) == 4 else 1
        self._list_canvas.yview_scroll(direction, "units")

    def _on_canvas_resize(self, event: tk.Event) -> None:
        """Keep the inner list frame as wide (and, when empty, as tall) as the canvas."""
        self._list_canvas.itemconfigure(self._list_window, width=event.width)
        if not self._row_order:
            self._list_canvas.itemconfigure(self._list_window, height=event.height)

    def _update_scrollregion(self) -> None:
        """Refresh the scrollable bounds after rows are rebuilt."""
        self._list_canvas.config(scrollregion=self._list_canvas.bbox("all"))

    def _build_empty_state(self) -> tk.Frame:
        """Build the centered creation card shown when the workspace list is empty.

        The card sits in ``workspace_list``, which is stretched to the full
        canvas height by ``_on_canvas_resize`` while the list is empty, so the
        card stays centered in the visible area (instead of being clipped to a
        pixel-tall strip the way the old watermark label was).
        """
        card = tk.Frame(
            self.workspace_list,
            bg=SURFACE_HOVER,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=28,
            pady=22,
        )
        badge = tk.Label(
            card,
            text="+",
            bg=ACCENT,
            fg=APP_BACKGROUND,
            font=FONT_EMPTY_BADGE,
            width=2,
            height=1,
        )
        badge.pack(pady=(0, 10))
        title = tk.Label(
            card,
            text="Créer un workflow",
            bg=SURFACE_HOVER,
            fg=TEXT_PRIMARY,
            font=FONT_EMPTY_TITLE,
        )
        title.pack()
        subtitle = tk.Label(
            card,
            text="Dossier local ou clonage depuis Git",
            bg=SURFACE_HOVER,
            fg=TEXT_MUTED,
            font=FONT_META,
            pady=4,
        )
        subtitle.pack()

        # Every child is clickable so the whole card opens the creation flow;
        # the hover feedback shifts the card to the pressed surface tone.
        for widget in (card, badge, title, subtitle):
            widget.bind("<Button-1>", lambda _event: self.prompt_create_workflow())
            widget.bind("<Enter>", self._on_empty_enter)
            widget.bind("<Leave>", self._on_empty_leave)
            with contextlib.suppress(Exception):
                widget.config(cursor="hand2")
        self._empty_widgets = (card, badge, title, subtitle)
        self._empty_hover_widgets = (card, title, subtitle)
        return card

    def _on_empty_enter(self, _event: tk.Event) -> None:
        """Brighten the empty-state card while hovered."""
        for widget in self._empty_hover_widgets:
            with contextlib.suppress(Exception):
                widget.config(bg=SURFACE_ACTIVE)

    def _on_empty_leave(self, _event: tk.Event) -> None:
        """Restore the empty-state card colors after hover ends."""
        for widget in self._empty_hover_widgets:
            with contextlib.suppress(Exception):
                widget.config(bg=SURFACE_HOVER)

    def _build_create_affordance(self) -> tk.Frame:
        """Build the quiet "+" strip appended under the last workspace row.

        Once rows exist the big empty-state card is hidden, so this strip is
        the only visible hint that the free area is clickable. It is *not*
        packed here: ``_toggle_empty_state`` packs it after the freshly rebuilt
        rows on every refresh, which keeps it last (and therefore centered in
        the space under the last row, scrolling with the content).
        """
        strip = tk.Frame(self.workspace_list, bg=SURFACE)
        badge = tk.Label(
            strip,
            text="+",
            bg=SURFACE,
            fg=TEXT_MUTED,
            font=FONT_EMPTY_BADGE,
        )
        badge.pack(anchor="center", padx=8, pady=6)
        for widget in (strip, badge):
            widget.bind("<Button-1>", lambda _event: self.prompt_create_workflow())
            with contextlib.suppress(Exception):
                widget.config(cursor="hand2")
        return strip

    def _toggle_empty_state(self) -> None:
        """Swap the big empty-state card and the quiet "+" strip.

        The card shows only while the list is empty (the canvas window keeps an
        explicit height so the card is centered); once rows exist the height is
        reset to ``0`` so Tk falls back to the content's own requested height,
        and the "+" strip is re-packed after the rebuilt rows — forgetting it
        first so a refresh never leaves it stranded above the new rows.
        """
        if not self._row_order:
            self._empty_state.place(relx=0.5, rely=0.5, anchor="center")
            self._create_affordance.pack_forget()
        else:
            self._empty_state.place_forget()
            with contextlib.suppress(Exception):
                self._list_canvas.itemconfigure(self._list_window, height=0)
            self._create_affordance.pack_forget()
            self._create_affordance.pack(fill="x", padx=3, pady=(8, 6))

    def _build_row(self, workspace: Workspace) -> tuple[RowFrame, tk.Label]:
        if workspace.id == self._selected_id:
            row_bg = ROW_SELECTED_BG
            border = ACCENT
        else:
            row_bg = SURFACE
            border = BORDER
        row = tk.Frame(
            self.workspace_list,
            bg=row_bg,
            highlightthickness=1,
            highlightbackground=border,
        )
        row.pack(fill="x", pady=4, padx=3)

        name_label = tk.Label(
            row,
            text=workspace.name,
            bg=row_bg,
            fg=TEXT_PRIMARY,
            font=FONT_ROWS,
            anchor="w",
            padx=8,
        )

        git_status = display.git_row_status(workspace)

        dirty_dot = self._chip(
            row,
            text="•" if git_status.dirty else "",
            palette=CHIP_WARN if git_status.dirty else (SURFACE, SURFACE),
        )
        if git_status.dirty:
            dirty_dot.pack(side="left", padx=(8, 0))

        name_label.pack(side="left", fill="x", expand=True)

        if workspace.id in self._launching:
            action_text = "Démarrage…"
            action_command = None
            action_state = "disabled"
        elif workspace.state in (WorkspaceState.STOPPED, WorkspaceState.ERROR):
            # The primary action gets the accent style so the main CTA stands out.
            action_text = "Démarrer"
            action_command = self._make_toggle_action(workspace.id)
            action_state = "normal"
        else:
            action_text = "Arrêter"
            action_command = self._make_toggle_action(workspace.id)
            action_state = "normal"
        action_button = ttk.Button(
            row,
            text=action_text,
            style="Accent.TButton"
            if workspace.state in (WorkspaceState.STOPPED, WorkspaceState.ERROR)
            and workspace.id not in self._launching
            else "Secondary.TButton",
            cursor="arrow" if action_state == "disabled" else "hand2",
            state=action_state,
            command=action_command,  # type: ignore[arg-type]  # ttk accepts None for an empty command; typeshed only allows str/callable
        )
        action_button.pack(side="right", padx=(6, 0))

        port_chip = self._chip(row, text=f":{workspace.port}", palette=CHIP_NEUTRAL)
        port_chip.pack(side="right", padx=(6, 0))

        db_palette = CHIP_ACTIVE if display.db_connected(workspace) else CHIP_INACTIVE
        db_chip = self._chip(row, text=display.db_label(workspace), palette=db_palette)
        db_chip.pack(side="right", padx=(6, 0))
        db_chip.configure(cursor="hand2")
        db_chip.bind("<Button-1>", lambda _event, wid=workspace.id: self._on_db_chip_click(wid))
        self._attach_tooltip(
            db_chip,
            "Base PostgreSQL locale gérée"
            if display.db_connected(workspace)
            else "Aucune base de données",
        )

        git_chip = self._chip(
            row,
            text=display.git_row_label(git_status),
            palette=self._git_chip_palette(git_status),
        )
        git_chip.pack(side="right", padx=(6, 0))
        git_chip.configure(cursor="hand2")
        git_chip.bind("<Button-1>", lambda _event, wid=workspace.id: self._on_git_chip_click(wid))
        self._attach_tooltip(git_chip, git_status.tooltip)

        ci_chip = self._chip(row, text="CI", palette=self._ci_chip_palette(workspace))
        ci_chip.pack(side="right", padx=(6, 0))
        ci_chip.configure(cursor="hand2")
        ci_chip.bind("<Button-1>", lambda _event, wid=workspace.id: self._on_ci_chip_click(wid))
        self._attach_tooltip(ci_chip, display.ci_tooltip(workspace))

        server_chip = self._chip(
            row,
            text=display.server_label(workspace),
            palette=self._server_chip_palette(workspace),
        )
        server_chip.pack(side="right", padx=(6, 0))
        server_chip.configure(cursor="hand2")
        server_chip.bind(
            "<Button-1>", lambda _event, wid=workspace.id: self._on_server_chip_click(wid)
        )
        self._attach_tooltip(server_chip, display.server_tooltip(workspace))

        pipelines = display.pipelines_count(workspace.workflows_dir)
        pipelines_chip = self._chip(row, text=str(pipelines), palette=CHIP_NEUTRAL)
        pipelines_chip.pack(side="right", padx=(6, 0))

        name_label.bind("<Button-1>", lambda _event, wid=workspace.id: self._select_row(wid))
        name_label.bind(
            "<Double-Button-1>",
            lambda _event, wid=workspace.id: self._handle_double(wid),
        )
        name_label.bind(
            "<Button-3>",
            lambda event, wid=workspace.id: self._show_context_menu(event, wid),
        )
        row.bind(
            "<Double-Button-1>",
            lambda _event, wid=workspace.id: self._handle_double(wid),
        )
        try:
            row.bind("<Enter>", lambda _event, wid=workspace.id: self._on_row_enter(wid))
            row.bind("<Leave>", lambda _event, wid=workspace.id: self._on_row_leave(wid))
        except Exception:
            pass

        frame = cast(RowFrame, row)
        frame.name_label = name_label
        frame.db_chip = db_chip
        frame.git_chip = git_chip
        frame.ci_chip = ci_chip
        frame.port_chip = port_chip
        frame.pipelines_chip = pipelines_chip
        frame.action_button = action_button
        frame.dirty_dot = dirty_dot
        frame.server_chip = server_chip
        return frame, name_label

    @staticmethod
    def _chip(
        parent: tk.Widget,
        text: str,
        *,
        palette: tuple[str, str],
        font: str | tuple[str, int, str] = FONT_PILL,
        padx: int = 8,
        pady: int = 2,
    ) -> tk.Label:
        bg, fg = palette
        return tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            anchor="center",
            padx=padx,
            pady=pady,
        )

    @staticmethod
    def _git_chip_palette(status: display.GitRowStatus) -> tuple[str, str]:
        if not status.is_repo or status.push_failed:
            return CHIP_INACTIVE
        if status.dirty or status.diverged:
            return CHIP_WARN
        return CHIP_ACTIVE

    @staticmethod
    def _ci_chip_palette(workspace: Workspace) -> tuple[str, str]:
        """Color the CI chip: neutral when off, warn when on but nothing runs."""
        if not workspace.git.ci_enabled:
            return CHIP_NEUTRAL
        provided = ci.provided_credentials(workspace.git.ci_credentials)
        counts = ci.ci_counts(workspace.workflows_dir, provided)
        if counts["selected"] == 0 or counts["selected_eligible"] < counts["selected"]:
            return CHIP_WARN
        return CHIP_ACTIVE

    @staticmethod
    def _server_chip_palette(workspace: Workspace) -> tuple[str, str]:
        """Color the server chip: neutral when off, warn on last deploy failure."""
        if not workspace.server.enabled:
            return CHIP_NEUTRAL
        if workspace.server_last_error:
            return CHIP_WARN
        return CHIP_ACTIVE

    def _attach_tooltip(self, widget: tk.Label, text: str) -> None:
        """Show *text* in a small frameless window while hovering the widget."""
        if not text:
            return
        tip: tk.Toplevel | None = None

        def on_enter(_event: tk.Event) -> None:
            nonlocal tip
            if tip is not None:
                return
            try:
                x = widget.winfo_rootx() + 12
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                tip = tk.Toplevel(self.root)
                tip.wm_overrideredirect(True)
                label = tk.Label(
                    tip,
                    text=text,
                    bg=SURFACE_HOVER,
                    fg=TEXT_PRIMARY,
                    relief="solid",
                    borderwidth=1,
                    font=FONT_META,
                    padx=8,
                    pady=4,
                )
                label.pack()
                tip.geometry(f"+{x}+{y}")
            except Exception:
                tip = None

        def on_leave(_event: tk.Event) -> None:
            nonlocal tip
            if tip is None:
                return
            # The tip window can disappear under the cursor (e.g. the dialog it
            # belongs to is closed while a leave event fires); destroying a
            # widget from a torn-down interpreter raises ``invalid command
            # name``, which must never surface from a hover handler.
            with contextlib.suppress(Exception):
                tip.destroy()
            tip = None

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _make_toggle_action(self, workspace_id: str) -> Callable[[], None]:
        """Build a zero-argument callback bound to a row button.

        Tk callbacks take no arguments, so the workspace id is captured
        eagerly in a closure instead of a bare lambda.
        """
        return lambda: self.toggle_from_row(workspace_id)

    def toggle_from_row(self, workspace_id: str) -> None:
        """Start (and open) or stop the workspace bound to a row button."""
        if self._closing:
            return
        workspace = next(
            (item for item in self.workspace_manager.list() if item.id == workspace_id),
            None,
        )
        if workspace is None:
            return
        if workspace.state in (WorkspaceState.STOPPED, WorkspaceState.ERROR):
            self._launch_workspace(workspace)
        else:
            self._stop_with_sync(workspace)

    def _stop_with_sync(self, workspace: Workspace) -> None:
        """Export + Git-sync a workspace, then stop it (row-stop path).

        Mirrors the close sequence so a manual stop also persists the latest
        n8n state to the repository; without this, closing the app afterwards
        skipped the already-stopped workspace and the export was lost.
        """
        workspace_id = workspace.id
        self.set_status(f"Arrêt de « {workspace.name} » — synchronisation…")

        def action() -> None:
            self.workspace_manager.stop_with_sync(workspace_id)

        def on_stopped() -> None:
            # Re-read the persisted flag: the object captured above predates the
            # git operations and would otherwise show a stale value.
            current = next(
                (item for item in self.workspace_manager.list() if item.id == workspace_id),
                None,
            )
            if current is not None and current.git_push_failed:
                messagebox.showwarning(
                    "Synchronisation Git",
                    f"Les workflows de « {current.name} » ont été synchronisés, mais le push\n"
                    "vers le dépôt distant a échoué (connexion ? permissions ?).\n"
                    "Les changements restent commités localement.",
                    parent=self.root,
                )
            self.set_status(f"« {workspace.name} » arrêté.")
            display.invalidate_git_status(current if current is not None else workspace)
            self.refresh()

        self._run_async(action, on_success=on_stopped)

    def _refresh_after_git_change(self, workspace: Workspace, status: str) -> None:
        """Drop the cached git probe for a workspace, repaint, and set a status.

        Called on the main thread once a background git operation (configure,
        push, CI toggle…) has completed so the next render re-probes instead of
        reusing the stale 3-second cache entry.
        """
        display.invalidate_git_status(workspace)
        self.refresh()
        self.set_status(status)

    def refresh(self) -> None:
        if self._closed:
            return
        workspaces = self.workspace_manager.list()
        if [workspace.id for workspace in workspaces] == self._row_order:
            # Same rows in the same order: refresh the widgets in place
            # instead of destroying and rebuilding every frame (and re-probing
            # git) on each poll tick.
            for workspace in workspaces:
                try:
                    self._update_row(workspace)
                except Exception:
                    logger.exception(
                        "Skipping unrefreshable workspace %s (%s)", workspace.id, workspace.name
                    )
            self._toggle_empty_state()
            self._update_subtitle(len(workspaces))
            self._update_scrollregion()
            return
        for frame, _label in self._rows.values():
            frame.destroy()
        self._rows = {}
        self._row_order = []
        for workspace in workspaces:
            try:
                row = self._build_row(workspace)
            except Exception:
                # A single unhealthy workspace must never blank the whole
                # list: log and skip it so the remaining rows still render.
                logger.exception(
                    "Skipping unrenderable workspace %s (%s)", workspace.id, workspace.name
                )
                continue
            self._rows[workspace.id] = row
            self._row_order.append(workspace.id)
        if self._selected_id is not None and self._selected_id not in self._rows:
            self._selected_id = None
        self._apply_selection_styles()
        self._toggle_empty_state()
        self._update_subtitle(len(workspaces))
        self._update_scrollregion()

    def _update_subtitle(self, count: int) -> None:
        """Refresh the header counter with the current workspace count."""
        if self._subtitle is None:
            return
        if count == 0:
            self._subtitle.config(text="Aucun workflow — cliquez pour en créer un")
        else:
            self._subtitle.config(text=f"{count} workflow{'s' if count != 1 else ''}")

    def _update_row(self, workspace: Workspace) -> None:
        """Refresh an existing row's widgets in place.

        Recomputes the same texts, palettes, tooltips and action button state
        ``_build_row`` would, without recreating the frame. Tooltip bindings
        are re-attached (``bind`` without ``add`` replaces the previous
        handler), so hovering always shows current data.
        """
        row = self._rows[workspace.id][0]
        row.name_label.config(text=workspace.name)
        git_status = display.git_row_status(workspace)

        row.dirty_dot.config(text="•" if git_status.dirty else "")
        if git_status.dirty:
            row.dirty_dot.config(bg=CHIP_WARN[0], fg=CHIP_WARN[1])
            row.dirty_dot.pack(side="left", padx=(8, 0))
        else:
            row.dirty_dot.config(bg=SURFACE, fg=SURFACE)
            row.dirty_dot.pack_forget()

        if workspace.id in self._launching:
            action_text, accent, action_state, action_command = (
                "Démarrage…",
                False,
                "disabled",
                None,
            )
        elif workspace.state in (WorkspaceState.STOPPED, WorkspaceState.ERROR):
            action_text, accent, action_state = "Démarrer", True, "normal"
            action_command = self._make_toggle_action(workspace.id)
        else:
            action_text, accent, action_state = "Arrêter", False, "normal"
            action_command = self._make_toggle_action(workspace.id)
        row.action_button.configure(
            text=action_text,
            style="Accent.TButton" if accent else "Secondary.TButton",
            state=action_state,
            command=action_command,  # type: ignore[arg-type]
            cursor="arrow" if action_state == "disabled" else "hand2",
        )

        row.port_chip.config(text=f":{workspace.port}")
        row.pipelines_chip.config(text=str(display.pipelines_count(workspace.workflows_dir)))

        db_connected_now = display.db_connected(workspace)
        row.db_chip.config(
            text=display.db_label(workspace),
            bg=CHIP_ACTIVE[0] if db_connected_now else CHIP_INACTIVE[0],
            fg=CHIP_ACTIVE[1] if db_connected_now else CHIP_INACTIVE[1],
        )
        self._attach_tooltip(
            row.db_chip,
            "Base PostgreSQL locale gérée" if db_connected_now else "Aucune base de données",
        )

        git_palette = self._git_chip_palette(git_status)
        row.git_chip.config(
            text=display.git_row_label(git_status),
            bg=git_palette[0],
            fg=git_palette[1],
        )
        self._attach_tooltip(row.git_chip, git_status.tooltip)

        ci_palette = self._ci_chip_palette(workspace)
        row.ci_chip.config(text="CI", bg=ci_palette[0], fg=ci_palette[1])
        self._attach_tooltip(row.ci_chip, display.ci_tooltip(workspace))

        server_palette = self._server_chip_palette(workspace)
        row.server_chip.config(
            text=display.server_label(workspace),
            bg=server_palette[0],
            fg=server_palette[1],
        )
        self._attach_tooltip(row.server_chip, display.server_tooltip(workspace))

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
        with contextlib.suppress(Exception):
            frame.name_label.config(bg=background)

    def _on_row_enter(self, workspace_id: str) -> None:
        self._set_row_background(workspace_id, hover=True)

    def _on_row_leave(self, workspace_id: str) -> None:
        self._set_row_background(workspace_id, hover=False)

    def _on_list_click(self, _event: tk.Event) -> None:
        """Open the creation wizard from any click on the list's free area.

        Covers the frame's row gaps, the canvas dead zone under the last row
        and the "+" strip: clicking empty space always means "create" — there
        is no background deselection anymore. Row internals keep their own
        bindings (select / launch / context menu) and never reach here.
        """
        self.prompt_create_workflow()

    def _apply_selection_styles(self) -> None:
        for workspace_id in self._rows:
            self._set_row_background(workspace_id, hover=False)

    def set_status(self, text: str) -> None:
        if self._status_label is not None:
            # Reset any update-link styling added by the update controller: the
            # bar points at the release page only while that message is shown.
            self._status_label.config(text=text, cursor="", fg=TEXT_MUTED)
            with contextlib.suppress(Exception):
                self._status_label.unbind("<Button-1>")

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
            with contextlib.suppress(Exception):
                self.root.after(STATE_POLL_MS, self._poll_states)
            return
        self._poll_in_flight = True
        threading.Thread(target=worker, name="n8n-launcher-poll", daemon=True).start()
        with contextlib.suppress(Exception):
            self.root.after(STATE_POLL_MS, self._poll_states)

    def _select_row(self, workspace_id: str) -> None:
        self._selected_id = workspace_id
        self._apply_selection_styles()
        try:
            workspace = self._selected()
        except ValueError:
            return
        self.set_status(f"{workspace.name} · :{workspace.port} · {state_label(workspace.state)}")

    def _handle_double(self, workspace_id: str) -> None:
        # A double-click fires twice; the second event must not restart a
        # workspace whose launch is already in flight.
        if workspace_id in self._launching:
            return
        self._select_row(workspace_id)
        self.launch_selected()

    def _show_context_menu(self, event: tk.Event, workspace_id: str | None = None) -> None:
        if workspace_id is not None:
            self._select_row(workspace_id)
        elif self._selected_id is not None:
            self._select_row(self._selected_id)
        try:
            workspace = self._selected()
        except ValueError:
            return
        self._build_context_menu(workspace)
        if self._menu is not None:
            self._menu.tk_popup(event.x_root, event.y_root)

    def prompt_create_workflow(self) -> None:
        """Ask the source first, then route to the matching creation flow.

        Guarded against re-entrancy: a double-click on the free area fires two
        ``Button-1`` events, and the second must not open a second wizard while
        the first one is still waiting on its dialog.
        """
        if self._creating:
            return
        self._creating = True
        try:
            source = prompt_create_source(self.root)
            if source is None:
                return
            if source == "clone":
                self._clone_workspace_flow()
                return
            workflows_dir = prompt_create_dir(self.root)
            if workflows_dir is None:
                return
            default_db = default_creation_db(workflows_dir)
            plan = prompt_create_plan(self.root, workflows_dir, default_db)
            if plan is None:
                return
            self._create_from_plan(plan, workflows_dir)
        finally:
            self._creating = False

    def _clone_workspace_flow(self) -> None:
        """Clone a repo into a new workspace.

        Prefers the linked GitHub account: the resolved token lists the
        user's repositories and the picker dialog lets them choose one
        without typing a URL. Falls back to the manual URL dialog when no
        token is available or GitHub is unreachable. The token, when used,
        only travels through the one-shot tokenized clone URL — the
        workspace's recorded remote stays clean.
        """
        token = self._ensure_ci_token()
        repos: list[dict[str, Any]] = []
        gh_client: GitHubClient | None = None
        if token:
            gh_client = GitHubClient(token, session=self._github_session)
            try:
                repos = gh_client.list_user_repos()
            except GitHubError as exc:
                messagebox.showwarning(
                    "n8n Launcher",
                    f"Impossible de lister vos dépôts GitHub : {exc}\n"
                    "Vous pouvez saisir une URL manuellement.",
                    parent=self.root,
                )

        clone_url: str | None = None
        branch: str | None = None
        if repos:
            branches_loader: Callable[[str], list[str]] | None = (
                (lambda full_name: gh_client.list_repo_branches(full_name))
                if gh_client is not None
                else None
            )
            pick: GitHubRepoPick | None = prompt_github_repo_picker(
                self.root,
                repos=repos,
                branches_loader=branches_loader,
            )
            if pick is None:
                return
            clone_url, branch = pick.clone_url, pick.branch
        else:
            plan = prompt_clone_plan(self.root)
            if plan is None:
                return
            clone_url, branch = plan.url, plan.branch

        repo_name = clone_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
        dest = prompt_clone_dest(self.root, _repo_name_from(repo_name))
        if dest is None:
            return

        def action() -> None:
            self.workspace_manager.clone_from_git(clone_url, dest, branch=branch, token=token)

        self._run_async(action, on_success=self._refresh_with_selection)

    def _create_from_plan(self, plan: CreatePlan, workflows_dir: Path) -> None:
        """Create the workspace from a creation plan, initializing git if asked."""
        github_plan = None
        if plan.git_enabled and plan.github_create:
            # A cancelled token dialog degrades to a local-only git repo; the
            # workspace is still created. The token is prefilled from the
            # resolved Git/gh credential so the user just confirms.
            github_plan = prompt_github_create(self.root, plan.name, token=self._ensure_ci_token())

        def action() -> None:
            workspace = self.workspace_manager.create(plan.name.strip(), workflows_dir, db=plan.db)
            if plan.git_enabled:
                if github_plan is not None:
                    self._create_github_and_configure(workspace, github_plan)
                else:
                    self.workspace_manager.git_init_workspace(
                        workspace, remote_url=plan.git_url or None
                    )

        self._run_async(
            action,
            on_success=self._refresh_with_selection,
        )

    def configure_git_selected(self) -> None:
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        current_remote = self.workspace_manager.git_remote_url(workspace)
        if current_remote is None:
            choice = prompt_git_config(self.root, workspace.name)
            if choice is None:
                return
            if choice.create_github:
                self._prompt_github_and_configure(workspace)
                return
            remote_url = choice.remote_url
        else:
            remote_url = prompt_git_remote(self.root, workspace.name, current_remote)
            if remote_url is None:
                return

        def action() -> None:
            if display.git_repo_status(workspace.workflows_dir):
                self.workspace_manager.configure_git(
                    workspace, remote_url=remote_url.strip() if remote_url else None
                )
            else:
                self.workspace_manager.git_init_workspace(
                    workspace, remote_url=remote_url.strip() if remote_url else None
                )

        self._run_async(
            action,
            on_success=lambda: self._refresh_after_git_change(
                workspace, f"Git configuré pour « {workspace.name} »."
            ),
        )

    def _prompt_github_and_configure(self, workspace: Workspace) -> None:
        """Ask for GitHub creation settings, then create the repo in a thread."""
        plan = prompt_github_create(self.root, workspace.name, token=self._ensure_ci_token())
        if plan is None:
            return

        def action() -> None:
            self._create_github_and_configure(workspace, plan)

        self._run_async(
            action,
            on_success=lambda: self._refresh_after_git_change(
                workspace, f"Dépôt GitHub configuré pour « {workspace.name} »."
            ),
        )

    def _create_github_and_configure(self, workspace: Workspace, plan: GitHubCreatePlan) -> str:
        """Create a repository on GitHub and wire it as the workspace remote.

        Runs off the main thread (network + git). The token is used only for
        the GitHub API call and one seed push; this flow never writes it to the
        config. Returns the created remote URL.
        """
        client = GitHubClient(plan.token, session=self._github_session)
        remote_url = client.create_repo(
            plan.name.strip(),
            private=plan.private,
            description=f"Workspace n8n « {workspace.name} »",
        )
        if display.git_repo_status(workspace.workflows_dir):
            self.workspace_manager.configure_git(workspace, remote_url=remote_url)
        else:
            self.workspace_manager.git_init_workspace(workspace, remote_url=remote_url)
        git_seed_remote(workspace.workflows_dir, remote_url, plan.token)
        return remote_url

    def _on_git_chip_click(self, workspace_id: str) -> None:
        """Clicking the git chip opens Git configuration for that workspace."""
        if self._closing:
            return
        self._select_row(workspace_id)
        self.configure_git_selected()

    def _on_ci_chip_click(self, workspace_id: str) -> None:
        """Clicking the CI chip opens the CI test configuration dialog."""
        if self._closing:
            return
        self._select_row(workspace_id)
        self.configure_ci_selected()

    def configure_server_selected(self) -> None:
        """Ask for the server settings, then install the listener on it."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        server = prompt_server_config(
            self.root,
            workspace.name,
            workspace.server,
            f"n8n-launcher/{workspace.id}",
        )
        if server is None:
            return

        def action() -> None:
            # Install first so a failed connection never persists a broken
            # config; a re-run is idempotent on both sides.
            self.workspace_manager.install_server(workspace, server)
            self.workspace_manager.update(workspace.id, server=server)

        def on_success() -> None:
            self.set_status(f"Serveur configuré pour « {workspace.name} ».")
            self.refresh()

        self._run_async(action, on_success=on_success)

    def publish_selected(self) -> None:
        """Deploy the current exports to the configured server."""
        if self._closing or self._deploying:
            return
        workspace = self._selected_or_warn()
        if workspace is None or not workspace.server.enabled:
            return

        def on_success() -> None:
            self._deploying = False
            self._refresh_after_git_change(
                workspace, f"Pipelines publiées pour « {workspace.name} »."
            )

        def on_error() -> None:
            self._deploying = False
            self.refresh()

        self._deploying = True
        self.set_status(f"Publication de « {workspace.name} »…")
        self._run_async(
            lambda: self.workspace_manager.publish(workspace),
            on_success=on_success,
            on_error=on_error,
        )

    def disable_server_selected(self) -> None:
        """Turn the server deployment off for the selected workspace."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        if not workspace.server.enabled:
            messagebox.showinfo(
                "n8n Launcher",
                "Le serveur n'est pas configuré pour ce workspace.",
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "Désactiver le serveur",
            "Cesser de publier vers le serveur ?\n"
            "Les fichiers déjà déployés sur le serveur sont conservés.",
            parent=self.root,
        )
        if not confirmed:
            return

        def on_success() -> None:
            self.set_status(f"Serveur désactivé pour « {workspace.name} ».")
            self.refresh()

        self._run_async(
            lambda: self.workspace_manager.disable_server(workspace),
            on_success=on_success,
        )

    def _on_server_chip_click(self, workspace_id: str) -> None:
        """Clicking the server chip opens the server configuration dialog."""
        if self._closing:
            return
        self._select_row(workspace_id)
        self.configure_server_selected()

    def configure_ci_selected(self) -> None:
        """Enable CI if needed, then open the pipeline selection dialog."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        if not display.ci_enabled(workspace):
            self._enable_ci_selected(workspace)
        else:
            self._show_ci_dialog(workspace)

    def _enable_ci_selected(self, workspace: Workspace) -> None:
        """Generate the CI harness (async) then let the user pick pipelines."""

        def action() -> None:
            self.workspace_manager.enable_ci(workspace)

        def on_success() -> None:
            display.invalidate_git_status(workspace)
            self.refresh()
            current = self._reload_workspace(workspace.id)
            if current is not None:
                self._show_ci_dialog(current)

        self._run_async(action, on_success=on_success)

    def _show_ci_dialog(self, workspace: Workspace) -> None:
        """Open the pipeline tree; persist the result through the manager."""
        runs_source: Callable[[], ci_runs.RunsSnapshot] | None = None
        runs_refresh: Callable[[RunsPanel], None] | None = None
        runs_open: Callable[[ci_runs.RunSummary], None] | None = None
        runs_run: Callable[[RunsPanel], None] | None = None
        remote = self.workspace_manager.git_remote_url(workspace)
        if isinstance(remote, str) and remote and ci.github_repo_path(remote):
            # Only a GitHub remote can expose Actions runs; other remotes keep
            # the single-pane dialog.
            runs_source = self._ci_runs_source(workspace)
            runs_refresh = self._ci_runs_refresh(workspace)
            runs_open = self._ci_runs_open()
            runs_run = self._ci_runs_run_cb(workspace)

        result = ci_edit.prompt_ci_workflows(
            self.root,
            workspace,
            runs_source=runs_source,
            runs_refresh=runs_refresh,
            runs_open=runs_open,
            runs_run=runs_run,
        )
        if result is None:
            return
        selected, push = result
        self._run_async(
            lambda: self.workspace_manager.save_ci_selection(workspace, selected, push=push),
            on_success=lambda: self._refresh_after_git_change(
                workspace, f"Sélection des tests CI enregistrée pour « {workspace.name} »."
            ),
        )

    def _ci_runs_source(self, workspace: Workspace) -> Callable[[], ci_runs.RunsSnapshot]:
        """Return a synchronous, I/O-free closure over the cached snapshot."""

        def source() -> ci_runs.RunsSnapshot:
            return self._ci_runs_cache.get(workspace.id, ci_runs.RunsSnapshot(""))

        return source

    def _ci_runs_refresh(self, workspace: Workspace) -> Callable[[RunsPanel], None]:
        """Bind the panel to a background refresh for the given workspace."""
        return lambda panel: self._refresh_ci_runs(workspace, panel)

    def _ci_runs_open(self) -> Callable[[ci_runs.RunSummary], None]:
        """Return a callback opening a run's GitHub page in the browser."""

        def open_run(run: ci_runs.RunSummary) -> None:
            if run.url:
                open_url(run.url)

        return open_run

    def _ci_runs_run_cb(self, workspace: Workspace) -> Callable[[RunsPanel], None]:
        """Bind the panel's "Lancer la CI" button to the dispatch flow."""
        return lambda panel: self._ci_runs_run(workspace, panel)

    def _ci_latest_branch(self, workspace: Workspace) -> str:
        """Branch of the most recent cached run, else ``dev`` as a fallback."""
        snapshot = self._ci_runs_cache.get(workspace.id)
        if snapshot and snapshot.runs and snapshot.runs[0].branch:
            return snapshot.runs[0].branch
        return "dev"

    def _ci_runs_run(self, workspace: Workspace, panel: RunsPanel) -> None:
        """Trigger the CI workflow on a chosen ref, then refresh the panel.

        The ref is asked on the main thread (prefilled with the latest run's
        branch) and the ``workflow_dispatch`` call runs in a background thread
        so the dialog never freezes; the panel is refreshed once GitHub has
        accepted the dispatch.
        """
        if self._closing:
            return
        remote = self.workspace_manager.git_remote_url(workspace)
        repo_path = ci.github_repo_path(remote) if remote else None
        if repo_path is None:
            messagebox.showwarning(
                "n8n Launcher",
                "Aucun dépôt distant GitHub configuré pour ce workspace.",
                parent=self.root,
            )
            return
        # The generated workflow uses ``concurrency: cancel-in-progress``: a
        # second dispatch would cancel the run the user is watching. Refuse
        # while the cached snapshot shows an in-flight run.
        snapshot = self._ci_runs_cache.get(workspace.id)
        if snapshot and any(ci_runs.run_status_is_active(run.status) for run in snapshot.runs):
            messagebox.showwarning(
                "n8n Launcher",
                "Un run CI est déjà en cours pour ce workspace. Attendez son "
                "achèvement avant d'en lancer un autre.",
                parent=self.root,
            )
            return
        token = self._ci_token_for_ui()
        if token is None:
            return
        ref = ci_edit.prompt_run_ci(self.root, ci.WORKFLOW_FILE, self._ci_latest_branch(workspace))
        if ref is None:
            return

        def worker() -> None:
            try:
                GitHubClient(token, session=self._github_session).dispatch_workflow(
                    repo_path, ci.WORKFLOW_FILE, ref=ref
                )
            except GitHubError as exc:
                # Bind the message before scheduling: the ``except`` variable is
                # cleared once the block exits, before the queue is drained.
                message = f"Impossible de lancer la CI : {exc}"

                def notify_error() -> None:
                    messagebox.showerror("n8n Launcher", message, parent=self.root)

                self.events.put((notify_error, None))
                return
            self.events.put((lambda: self.set_status(f"CI lancée sur « {ref} »."), None))
            # Force a real re-fetch: a just-dispatched run must show up now,
            # not whenever the 30-second freshness window expires.
            self.events.put((lambda: self._refresh_ci_runs(workspace, panel, force=True), None))

        threading.Thread(target=worker, name="n8n-launcher-ci-dispatch", daemon=True).start()

    def _ensure_ci_token(self) -> str | None:
        """Return a usable GitHub token, resolving it silently when possible.

        The token comes from the OS Git credential helper (the very credential
        ``git push`` uses) or the ``gh`` CLI, then from an optional override
        remembered earlier in the config. Nothing has to be configured by hand.
        """
        if self._ci_token:
            return self._ci_token
        token = auth.resolve_github_token(self.workspace_manager.github_token())
        if token:
            self._ci_token = token
        return token

    def _ci_token_for_ui(self) -> str | None:
        """Resolve a token for a UI action, prompting once as a last resort.

        Only when Git/``gh`` and the remembered override are all empty does
        this ask the user (and remember the token if they tick the box). A
        cancel is recorded so the same session is not nagged on every action.
        """
        token = self._ensure_ci_token()
        if token is not None:
            return token
        if self._ci_token_declined:
            return None
        plan = prompt_github_token(self.root)
        if plan is None:
            self._ci_token_declined = True
            return None
        self._ci_token = plan.token
        if plan.remember:
            self.workspace_manager.set_github_token(plan.token)
        return plan.token

    def _refresh_ci_runs(
        self, workspace: Workspace, panel: RunsPanel, *, force: bool = False
    ) -> None:
        """Fetch GitHub runs off-thread, then apply the snapshot on the main thread.

        The token is resolved automatically and cached in memory; it is only
        asked for (once per session, and only to remember it if the user wants)
        when no Git/``gh`` credential is available. The worker never touches
        Tk: it hands the rendered snapshot back through the event queue, whose
        drain runs ``panel.apply`` on the main thread. A fetch already in
        progress makes this a no-op — the auto-poll must never stack
        overlapping workers (the panel is refreshed again on the next tick).

        Reads within ``CI_RUNS_TTL_SECONDS`` of the last fetch are served from
        the cache on the main thread: the panel auto-polls every few seconds
        while a run is in flight, and re-listing runs (plus the focus run's
        job logs) on every tick would burn the GitHub API budget for a display
        that barely changed. ``force=True`` bypasses the freshness window
        (used right after a manual dispatch so the new run shows up at once).
        """
        cached = self._ci_runs_cache.get(workspace.id)
        last_fetched = self._ci_runs_fetched_at.get(workspace.id, 0.0)
        if (
            not force
            and cached is not None
            and time.monotonic() - last_fetched < CI_RUNS_TTL_SECONDS
        ):
            self._apply_ci_runs_snapshot(workspace.id, panel, cached)
            return
        if self._ci_runs_fetch_in_flight:
            return
        token = self._ci_token_for_ui()
        if token is None:
            return

        def worker() -> None:
            try:
                snapshot = self._build_ci_runs_snapshot(workspace)
                self.events.put(
                    (
                        lambda snapshot=snapshot: self._apply_ci_runs_snapshot(
                            workspace.id, panel, snapshot
                        ),
                        None,
                    )
                )
            finally:
                # Release the lock on the main thread: the next poll callback
                # runs after the drain, so a fresh fetch is allowed right away.
                self.events.put(
                    (
                        lambda: setattr(self, "_ci_runs_fetch_in_flight", False),
                        None,
                    )
                )

        self._ci_runs_fetch_in_flight = True
        threading.Thread(target=worker, name="n8n-launcher-ci-runs", daemon=True).start()

    def _apply_ci_runs_snapshot(
        self, workspace_id: str, panel: RunsPanel, snapshot: RunsSnapshot
    ) -> None:
        """Cache and render a fetched CI snapshot, always on the main thread.

        The fetch runs in a background thread and can outlive the dialog that
        hosts the panel: a snapshot landing after the user closed the dialog
        must be cached (the next open shows it instantly) but never rendered on
        destroyed widgets — Tk would raise ``invalid command name
        ".!toplevel…!treeview"`` and the drain would surface a spurious error
        dialog.
        """
        self._ci_runs_cache[workspace_id] = snapshot
        self._ci_runs_fetched_at[workspace_id] = time.monotonic()
        try:
            if not panel.winfo_exists():
                return
            panel.apply(snapshot)
        except Exception:
            return

    def configure_github_token(self) -> None:
        """Let the user replace the GitHub token used for the API calls.

        Optional escape hatch: the token is normally resolved from Git/``gh``,
        but this sets (and, when ticked, remembers once) an explicit override.
        """
        plan = prompt_github_token(self.root)
        if plan is None:
            return
        self._ci_token = plan.token
        self._ci_token_declined = False
        if plan.remember:
            self.workspace_manager.set_github_token(plan.token)
        self._ci_runs_cache.clear()
        self._ci_runs_fetched_at.clear()
        self.set_status("Token GitHub mis à jour.")

    def _build_ci_runs_snapshot(self, workspace: Workspace) -> ci_runs.RunsSnapshot:
        """Fetch runs/jobs/logs and normalise them into a snapshot (worker-thread safe).

        Every failure is turned into a snapshot ``error`` so the panel renders a
        readable message instead of the worker dying. Only the run the user is
        watching — the in-flight one, or the newest when nothing runs — has its
        jobs and logs fetched; every other run is just listed. That keeps the
        API budget (and the wait) bounded and stops old failures from flooding
        the panel. The wall-clock fetch time is recorded as ``fetched_at`` so
        the panel can show its freshness.
        """
        remote = self.workspace_manager.git_remote_url(workspace)
        repo_path = ci.github_repo_path(remote) if remote else None
        if repo_path is None:
            return ci_runs.compose_snapshot(
                repo_path="",
                runs=[],
                error="Aucun dépôt GitHub configuré pour ce workspace.",
            )
        token = self._ci_token
        if not token:
            return ci_runs.compose_snapshot(
                repo_path=repo_path, runs=[], error="Token GitHub manquant."
            )
        client = GitHubClient(token, session=self._github_session)
        try:
            runs = client.list_workflow_runs(repo_path)
        except GitHubError as exc:
            return ci_runs.compose_snapshot(repo_path=repo_path, runs=[], error=str(exc))

        raw_jobs: dict[int, list[dict[str, Any]]] = {}
        pipelines: dict[int, list[ci_runs.PipelineResult]] = {}
        # Only the run worth detailing is expanded: an in-flight run (that is
        # exactly what the user watches) or, when nothing is running, the
        # newest one. Every other run stays a compact single row — fetching
        # jobs and logs for all recent runs would only dump old failures the
        # user explicitly does not want to see.
        focus_run = next(
            (run for run in runs if ci_runs.run_status_is_active(str(run.get("status") or ""))),
            None,
        ) or (runs[0] if runs else None)
        if focus_run is not None:
            run_id = int(focus_run.get("id") or 0)
            if run_id:
                try:
                    jobs = client.list_run_jobs(repo_path, run_id)
                except GitHubError:
                    jobs = []
                raw_jobs[run_id] = jobs
                job_ids = [int(job.get("id") or 0) for job in jobs]
                job_ids = [job_id for job_id in job_ids if job_id]
                # Logs are fetched for every job of the focus run: the
                # finished ones (final summary) and the in-flight one (live
                # ``[runner]`` progress lines), so the panel can detail it.
                # Bodies are large, so the downloads run in parallel; the
                # harness is torn down with ``wait=True`` so no late future
                # writes into ``pipelines`` after this method returns.
                with ThreadPoolExecutor(max_workers=_CI_LOG_WORKERS) as pool:
                    futures = {
                        pool.submit(client.fetch_job_logs, repo_path, job_id): job_id
                        for job_id in job_ids
                    }
                    for future in as_completed(futures):
                        job_id = futures[future]
                        try:
                            log_text = future.result()
                        except GitHubError:
                            continue
                        parsed = ci_runs.parse_pipeline_lines(log_text)
                        if parsed:
                            pipelines[job_id] = parsed
        return ci_runs.compose_snapshot(
            repo_path=repo_path,
            runs=runs,
            raw_jobs=raw_jobs,
            pipelines=pipelines,
            fetched_at=datetime.now().astimezone().isoformat(),
        )

    def configure_ci_credentials_selected(self) -> None:
        """Open the credentials dialog (requires a running workspace)."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        ci_edit.prompt_ci_credentials(self.root, self.workspace_manager, workspace)
        self.refresh()

    def open_ci_actions(self) -> None:
        """Open the GitHub Actions page of the workspace's repository."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        remote = self.workspace_manager.git_remote_url(workspace)
        repo_path = ci.github_repo_path(remote) if remote else None
        if repo_path is None:
            messagebox.showwarning(
                "n8n Launcher",
                "Aucun dépôt distant GitHub configuré pour ce workspace.",
                parent=self.root,
            )
            return
        open_url(ci.actions_url(repo_path))

    def disable_ci_selected(self) -> None:
        """Disable CI for the selected workspace, keeping its selection."""
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        if not display.ci_enabled(workspace):
            messagebox.showinfo(
                "n8n Launcher",
                "Les tests GitHub Actions ne sont pas activés pour ce workspace.",
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            "Désactiver les tests CI",
            "Désactiver les tests GitHub Actions ?\n"
            "La sélection de pipelines (tests.json) sera conservée.",
            parent=self.root,
        )
        if not confirmed:
            return
        self._run_async(
            lambda: self.workspace_manager.disable_ci(workspace),
            on_success=lambda: self._refresh_after_git_change(
                workspace, f"Tests GitHub Actions désactivés pour « {workspace.name} »."
            ),
        )

    def _reload_workspace(self, workspace_id: str) -> Workspace | None:
        """Return the freshest persisted version of a workspace (or None)."""
        return next(
            (item for item in self.workspace_manager.list() if item.id == workspace_id),
            None,
        )

    def _on_db_chip_click(self, workspace_id: str) -> None:
        """Clicking the DB chip opens database configuration for that workspace."""
        if self._closing:
            return
        self._select_row(workspace_id)
        self.configure_db_selected()

    def configure_db_selected(self) -> None:
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        db_config = prompt_db_config(self.root, workspace.db)
        if db_config is None:
            return

        def action() -> None:
            self.workspace_manager.configure_db(workspace, db_config)

        self._run_async(
            action,
            on_success=lambda: self.set_status(
                f"Base de données configurée pour « {workspace.name} »."
            ),
        )

    def launch_selected(self) -> None:
        # Multiple workspaces may start at once (bounded by ``_start_slots``);
        # only the app closing blocks a launch.
        if self._closing:
            return
        workspace = self._selected_or_warn()
        if workspace is None:
            return
        self._launch_workspace(workspace)

    def _launch_workspace(self, workspace: Workspace) -> None:
        if workspace.id in self._launching:
            return
        self._launching.add(workspace.id)
        url = f"http://127.0.0.1:{workspace.port}"
        self.set_status(f"Lancement de « {workspace.name} » — attente que n8n réponde…")
        self.refresh()

        def action() -> None:
            # Bound the number of simultaneous boots: the slot is held for the
            # whole ``up`` + health check and freed even on failure.
            self._start_slots.acquire()
            try:
                self._ensure_running(workspace)
            finally:
                self._start_slots.release()

        def on_success() -> None:
            self._launching.discard(workspace.id)
            display.invalidate_git_status(workspace)
            self.refresh()
            self.browser_opener(url, browser_app_dir(workspace.id))

        def on_error() -> None:
            self._launching.discard(workspace.id)
            self.refresh()

        self._run_async(action, on_success=on_success, on_error=on_error)

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
        self.workspace_manager.ensure_running(current.id, on_ready=self._wait_until_healthy)
        if needs_bootstrap:
            email = self.config_store.load().owner_email

            def notify_owner(email: str = email) -> None:
                messagebox.showinfo(
                    "n8n Launcher",
                    f"n8n préconfiguré. Connectez-vous avec {email} "
                    "(mot de passe défini à l'installation).",
                    parent=self.root,
                )

            self.events.put((notify_owner, None))

    def _wait_until_healthy(
        self, port: int, *, interval: float = 2.0, timeout: float = 120.0
    ) -> None:
        """Wait until n8n is usable, not just listening.

        ``/healthz`` turns 200 long before n8n is ready: right after boot n8n
        performs an internal warm restart during which every route answers 404
        (transient observed on n8n 2.33.x). Polling ``/api/v1/workflows`` until
        the router responds with a "mounted" status (200/401/403) closes that
        window, so the owner bootstrap and workflow import that follow never
        land on a spurious 404.
        """
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{port}/healthz"
        api_url = f"http://127.0.0.1:{port}/api/v1/workflows"
        while True:
            try:
                response = requests.get(url, timeout=2.0)
                healthy = response.ok
            except requests.RequestException:
                healthy = False
            if healthy:
                try:
                    api_response = requests.get(api_url, timeout=2.0)
                except requests.RequestException:
                    api_response = None
                # 200/401/403: the public API router is mounted and answering
                # (401 simply means this request carried no usable key).
                if api_response is not None and _api_router_mounted(api_response):
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"n8n did not become ready on {url} within {timeout:.0f}s")
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
            ""
            if current.state is WorkspaceState.STOPPED
            else "\n(Docker sera arrêté au préalable.)"
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

        def on_success() -> None:
            # The worker stopped the stack and deleted the config: the
            # selection is cleared and the list refreshed here, on the
            # main thread, never on the worker (which must not touch Tk state).
            self._selected_id = None
            self.refresh()

        self._run_async(action, on_success=on_success)

    def _run_async(
        self,
        action: Callable[[], Any],
        *,
        on_success: Callable[[], None] | None = None,
        on_error: Callable[[], None] | None = None,
    ) -> None:
        def worker() -> None:
            try:
                # Serialize the heavy subprocess work (docker compose, git,
                # ssh, GitHub) behind the shared budget set at startup.
                self._background_throttle.run(action)
            except Exception as exc:
                self.events.put((on_error or self.refresh, exc))
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
        with contextlib.suppress(Exception):
            self.root.after(100, self._drain_events)

    def on_close(self) -> None:
        if self._closing:
            return
        self.close_flow.begin()

    def _finish_close(self) -> None:
        self._closed = True
        self.root.destroy()
