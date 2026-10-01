"""The GUI toolkit must stay confined to ``n8n_launcher.gui``.

Phase 1 promised "no toolkit is a dependency" — that promise is gone, but its
useful half survives: a manager, a migration runner or the monitoring store must
still be importable on a headless server. A subprocess is used rather than
``sys.modules`` in-process, because by the time this file runs another test has
already imported PySide6; only a fresh interpreter can prove the import graph.
"""

from __future__ import annotations

import subprocess
import sys

#: One representative module per shipped package, plus the pure helpers.
BUSINESS_MODULES = (
    "n8n_launcher.core.models",
    "n8n_launcher.core.config",
    "n8n_launcher.core.paths",
    "n8n_launcher.workspaces.manager",
    "n8n_launcher.workspaces.status",
    "n8n_launcher.docker.manager",
    "n8n_launcher.git.manager",
    "n8n_launcher.github.api",
    "n8n_launcher.remote.ssh",
    "n8n_launcher.monitoring.store",
    "n8n_launcher.n8n.api",
    "n8n_launcher.gui_utils.text",
)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run *code* in a fresh interpreter that shares this one's environment."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_business_modules_import_without_pulling_in_qt() -> None:
    imports = "".join(f"import {name}\n" for name in BUSINESS_MODULES)
    code = (
        imports
        + "import sys\n"
        + "loaded = sorted(m for m in sys.modules if m.split('.')[0] in {'PySide6', 'shiboken6'})\n"
        + "print(loaded)\n"
        + "raise SystemExit(1 if loaded else 0)\n"
    )

    result = _run(code)

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_gui_package_exposes_the_entry_points() -> None:
    import n8n_launcher.gui as gui

    assert set(gui.__all__) == {"LauncherApp", "prompt_first_launch", "run_gui"}
