"""The notifier turns manager protocol calls into Qt signals.

The last test drives a real :class:`WorkspaceManager`, which is what proves the
notifier actually satisfies the observer protocol rather than merely looking
like it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import QObject

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig, DbConfig, DbMode
from n8n_launcher.gui.notifier import WorkspaceNotifier
from n8n_launcher.workspaces.manager import WorkspaceManager


def test_the_notifier_reemits_the_list_change(qtbot) -> None:
    notifier = WorkspaceNotifier()
    with qtbot.waitSignal(notifier.workspacesChanged):
        notifier.workspaces_changed()


def test_the_notifier_reemits_a_workspace_with_its_payload(qtbot) -> None:
    notifier = WorkspaceNotifier()
    workspace = MagicMock()
    with qtbot.waitSignal(notifier.workspaceChanged) as blocker:
        notifier.workspace_changed(workspace)
    assert blocker.args == [workspace]


def test_the_notifier_is_a_qobject(qtbot) -> None:
    assert isinstance(WorkspaceNotifier(), QObject)


def test_a_manager_drives_the_notifier(qtbot, tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "launcher.db")
    store.save(AppConfig("owner@example.test", "secret", tmp_path))
    manager = WorkspaceManager(store, MagicMock())
    notifier = WorkspaceNotifier()
    manager.add_observer(notifier)

    with qtbot.waitSignal(notifier.workspacesChanged):
        manager.create("Demo", tmp_path / "workflows", db=DbConfig(DbMode.NONE), port=5680)
