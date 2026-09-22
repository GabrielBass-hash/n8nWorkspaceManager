"""Visual constants and helpers for the dark Tkinter interface."""

from __future__ import annotations

import tkinter as tk

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


# The UI renders through *named* fonts registered by ``configure_fonts``:
# widgets reference the ``FONT_*`` constants (plain font names), and the actual
# family/size is decided at runtime against the running Tk root. Sizes are in
# *points*, so Tk converts them to pixels through the DPI-aware ``tk scaling``
# factor and the UI reflows responsively instead of scaling pixel values by
# hand.
#
# The family is deliberately resolved at runtime rather than guessed: on Linux
# a hard-coded "DejaVu Sans" misses whenever Tk does not expose that family
# (this build only lists ``liberation sans``), the widget then falls back to
# the tiny ``fixed`` bitmap font, and the requested point size is *ignored*
# entirely — every size renders at ~13px. Probing the interpreter's own family
# list avoids the silent fallback that produced that unreadable UI.
_FONT_SPECS: tuple[tuple[str, int, str | None], ...] = (
    ("Launcher.Title", 17, "bold"),
    ("Launcher.Subtitle", 11, None),
    ("Launcher.Rows", 13, "bold"),
    ("Launcher.Meta", 12, None),
    ("Launcher.Status", 11, None),
    ("Launcher.Pill", 11, "bold"),
    ("Launcher.EmptyTitle", 18, "bold"),
    ("Launcher.EmptyBadge", 22, "bold"),
)

FONT_TITLE = "Launcher.Title"
FONT_SUBTITLE = "Launcher.Subtitle"
FONT_ROWS = "Launcher.Rows"
FONT_META = "Launcher.Meta"
FONT_STATUS = "Launcher.Status"
FONT_PILL = "Launcher.Pill"
FONT_EMPTY_TITLE = "Launcher.EmptyTitle"
FONT_EMPTY_BADGE = "Launcher.EmptyBadge"

# Preferred font families, most-desirable first. The first one reported by the
# running Tk wins, so the table doubles as the platform fallback chain:
# "Segoe UI" and "Helvetica Neue" win on Windows/macOS, popular Linux faces
# follow, and "Liberation Sans" covers Tk builds that only expose the
# ``liberation`` families. Matching is case-insensitive because Linux Tk
# canonicalizes the family list to lowercase.
FONT_FAMILY_ORDER = (
    "Segoe UI",
    "Helvetica Neue",
    "Noto Sans",
    "DejaVu Sans",
    "Ubuntu",
    "Cantarell",
    "Liberation Sans",
    "Arial",
    "Sans",
)


def _registered_font_names(root: tk.Misc) -> set[str]:
    """Return the named fonts already created in *root*'s Tcl interpreter.

    Named fonts are per-interpreter: ``font create`` against one ``tk.Tk()`` is
    invisible in a second one, so idempotency must be probed per root rather
    than against a process-wide flag. A missing/headless ``tk`` (unit-test
    fakers) yields an empty set so the caller falls back to registering.
    """
    try:
        return set(root.tk.call("font", "names"))
    except Exception:
        return set()


def configure_fonts(root: tk.Misc) -> bool:
    """Register the named UI fonts against *root*; True when registered.

    Idempotent per Tcl interpreter: a root that already holds every ``FONT_*``
    named font (the app's own, after a first call) returns ``True`` without
    re-creating them, while a *fresh* root — e.g. the ``tk.Tk()`` the first
    launch wizard owns before ``LauncherApp`` exists — still receives them, so
    the same runtime family detection applies to every interface of the app. If
    no preferred family is available, ``TkDefaultFont`` keeps every widget
    resolvable instead of crashing on a missing font name. Fake/headless roots
    (unit tests) leave the names unregistered — widgets still carry the
    ``FONT_*`` strings and the fakes ignore them.
    """
    try:
        import tkinter.font as tkfont

        known: dict[str, str] = {family.lower(): family for family in tkfont.families(root)}
    except Exception:
        return False
    family = next(
        (known[preferred.lower()] for preferred in FONT_FAMILY_ORDER if preferred.lower() in known),
        "TkDefaultFont",
    )
    registered = _registered_font_names(root)
    if all(name in registered for name, _, _ in _FONT_SPECS):
        return True
    try:
        for name, size, weight in _FONT_SPECS:
            spec: list[object] = ["font", "create", name, "-family", family, "-size", size]
            if weight is not None:
                spec += ["-weight", weight]
            root.tk.call(*spec)
        return True
    except Exception:
        return False
