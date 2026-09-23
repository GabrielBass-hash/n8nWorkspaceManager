"""Cross-OS structural parity of the Tkinter widget tree.

Reads the three ``structure-<os>.json`` snapshots produced by
``test_structure.py`` (one per OS, written by the CI ``test`` matrix job) and
fails when a widget exists on one OS but not on another — e.g. a checkbox
only created on Windows. Locally, where at most the current OS's snapshot
exists, the test is skipped with a clear message: a missing file is never a
failure.

The parity rule is deliberately broad: the union of every widget path seen on
any OS must be present on all three, so an untracked widget that only appears
on a single OS is still caught. :data:`WATCHED_PATHS` is the explicit,
documented registry of the widgets the app considers essential; adding a
widget there (see README) makes its absence loud even when the union check
would already trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The essential widgets that must exist on every OS. Paths are the friendly,
# alias-based logical names produced by ``test_structure.py`` (e.g.
# ``root.status_label``, ``creation_dialog.git_enabled``). When the tree
# drifts, run ``test_structure.py`` once per OS to regenerate the snapshots
# and copy the exact path seen in ``structure-<os>.json``.
OS_NAMES = ("linux", "windows", "macos")

WATCHED_PATHS = (
    # Main window — app-owned widgets (friendly names from the structure test)
    "root.header",
    "root.header.title",
    "root.header.subtitle",
    "root.status_label",
    "root.!frame4.list_canvas",
    "root.!frame4.list_canvas.workspace_list",
    # Empty-state creation card
    "root.!frame4.list_canvas.workspace_list.empty_card",
    "root.!frame4.list_canvas.workspace_list.empty_card.empty_badge",
    "root.!frame4.list_canvas.workspace_list.empty_card.empty_title",
    "root.!frame4.list_canvas.workspace_list.empty_card.empty_subtitle",
    # One rendered workspace row
    "root.!frame4.list_canvas.workspace_list.row",
    "root.!frame4.list_canvas.workspace_list.row.name_label",
    "root.!frame4.list_canvas.workspace_list.row.dirty_dot",
    "root.!frame4.list_canvas.workspace_list.row.action_button",
    "root.!frame4.list_canvas.workspace_list.row.db_chip",
    "root.!frame4.list_canvas.workspace_list.row.git_chip",
    "root.!frame4.list_canvas.workspace_list.row.ci_chip",
    "root.!frame4.list_canvas.workspace_list.row.port_chip",
    "root.!frame4.list_canvas.workspace_list.row.pipelines_chip",
    # Creation dialog — the workflow checkboxes parity exists to protect
    "root.creation_dialog.db_managed_radio",
    "root.creation_dialog.db_none_radio",
    "root.creation_dialog.git_enabled",
    "root.creation_dialog.github_create",
    "root.creation_dialog.!frame.cancel_button",
    "root.creation_dialog.!frame.create_button",
    # CI credentials dialog
    "root.credentials_dialog.credential_GitHub",
    "root.credentials_dialog.!frame.copy_json_button",
    "root.credentials_dialog.!frame.confirm_pasted_button",
    "root.credentials_dialog.!frame.cancel_button",
)


def _all_snapshot_paths(directory: Path) -> dict[str, set[str]]:
    """Load every snapshot present under *directory*, keyed by OS name."""
    by_os: dict[str, set[str]] = {}
    for os_name in OS_NAMES:
        path = directory / f"structure-{os_name}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        by_os[os_name] = {widget["path"] for widget in payload["widgets"]}
    return by_os


def _missing_paths(path: str, by_os: dict[str, set[str]]) -> list[str]:
    """Return the OS names where *path* is absent."""
    return [os_name for os_name, paths in by_os.items() if path not in paths]


def test_widget_parity_across_os(structure_dir: Path) -> None:
    """Every widget known on one OS must exist on the other two."""
    by_os = _all_snapshot_paths(structure_dir)
    present = [name for name in OS_NAMES if name in by_os]
    if len(present) != len(OS_NAMES):
        looked = ", ".join(str(structure_dir / f"structure-{os_name}.json") for os_name in OS_NAMES)
        pytest.skip(
            "parité structurelle: snapshots incomplets "
            f"(seulement {', '.join(present) or 'aucun'} trouvé dans {structure_dir}). "
            "Lancer test_structure.py sur linux, windows et macos, ou passer "
            "--structure-dir vers le dossier des 3 fichiers. Fichiers cherchés: {looked}"
        )

    union = set().union(*by_os.values())
    failures = [
        f"{path}: absent sur {', '.join(_missing_paths(path, by_os))}"
        for path in sorted(union)
        if _missing_paths(path, by_os)
    ]
    for path in WATCHED_PATHS:
        missing = _missing_paths(path, by_os)
        if missing:
            failures.append(f"WATCHED_PATHS « {path} »: absent sur {', '.join(missing)}")

    assert not failures, (
        "Widget(s) présent(s) sur un OS mais pas sur les autres:\n- " + "\n- ".join(failures)
    )
