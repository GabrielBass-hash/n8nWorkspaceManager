"""Visual constants and helpers for the dark Tkinter interface."""

from __future__ import annotations

import platform

from ..core.models import WorkspaceState

APP_BACKGROUND = "#0f172a"
SURFACE = "#1e293b"
SURFACE_HOVER = "#28374d"
SURFACE_ACTIVE = "#31405a"
BORDER = "#334155"
BORDER_STRONG = "#475569"
ROW_SELECTED_BG = "#1e3056"
ROW_DELETE_BG = "#450a0a"
ROW_DELETE_ACTIVE = "#7f1d1d"
ROW_DELETE_FG = "#f87171"
TEXT_PRIMARY = "#f8fafc"
TEXT_MUTED = "#94a3b8"
ACCENT = "#3b82f6"
ACCENT_ACTIVE = "#2563eb"
ACCENT_HOVER = "#60a5fa"
STATUS_BAR_BORDER = "#1e293b"

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
CHIP_WARN = ("#78350f", "#fcd34d")

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
    """Return the French display label for a workspace state."""
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
