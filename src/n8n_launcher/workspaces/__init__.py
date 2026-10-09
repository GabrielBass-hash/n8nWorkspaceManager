"""Workspace orchestration: CRUD and start/stop lifecycle.

Entry: :class:`~n8n_launcher.workspaces.manager.WorkspaceManager`
(``manager.py``), the card rules in ``status.py``,
:class:`~n8n_launcher.workspaces.close.CloseSequence` (``close.py``),
``render_harness`` (``ci.py``).
Gotcha: ``ensure_serving()`` asks Docker, not the persisted state — a stack
started outside the launcher (or after a crash) is opened, never started twice.
Map: ``docs/architecture.md``.
"""
