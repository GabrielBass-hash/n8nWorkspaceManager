"""Unit tests for the close sequence (:mod:`n8n_launcher.workspaces.close`).

The order is the contract: export, then commit/push, then stop — and only then
the next workspace. These tests pin that order and the three answers a caller can
give when a step fails, because a close that reorders its steps loses work
silently, which is the one failure mode a user cannot notice until it is too late.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.workspaces.close import CloseHooks, CloseSequence


def _workspace(
    tmp_path: Path,
    *,
    name: str = "Demo",
    state: WorkspaceState = WorkspaceState.RUNNING,
    api_key: str | None = "key",
) -> Workspace:
    return Workspace(
        id=f"ws-{name}",
        name=name,
        workflows_dir=tmp_path / name,
        port=5678,
        db=DbConfig(DbMode.NONE),
        state=state,
        api_key=api_key,
    )


def _manager(*workspaces: Workspace) -> MagicMock:
    manager = MagicMock()
    manager.list.return_value = list(workspaces)
    return manager


def _sequence(manager: MagicMock, hooks: CloseHooks, calls: list[str]) -> CloseSequence:
    """Build the sequence with the export replaced.

    The export is the one step that opens a socket to a running n8n, so every
    test that reaches the sync path stubs it: what is under test is the *order*
    of the steps and the decisions taken when one of them fails.
    """
    sequence = CloseSequence(manager, hooks)
    sequence._export = lambda _workspace: calls.append("export")  # type: ignore[method-assign]
    return sequence


def _recording_hooks(calls: list[str], **overrides: Any) -> CloseHooks:
    hooks = CloseHooks(on_progress=calls.append)
    for name, value in overrides.items():
        setattr(hooks, name, value)
    return hooks


def test_a_close_with_nothing_running_does_nothing() -> None:
    # Nothing to export, nothing to stop: the sequence must not even announce a
    # close, so a headless caller can tell "no work" from "work done".
    calls: list[str] = []
    manager = _manager()
    assert CloseSequence(manager, _recording_hooks(calls)).run() is True
    assert calls == []
    manager.reconcile_all.assert_not_called()
    manager.stop.assert_not_called()


def test_the_close_reconciles_before_deciding_what_to_export() -> None:
    # The per-workspace decision is made on a *real* state: a workspace marked
    # running that is in fact stopped has no n8n to read from.
    workspace = _workspace(Path("/tmp"), state=WorkspaceState.STOPPED)
    calls: list[str] = []
    CloseSequence(_manager(workspace), _recording_hooks(calls)).run()
    assert calls == [
        "Fermeture : synchronisation des workflows puis arrêt de n8n…",
        "Fermeture : arrêt de « Demo »…",
    ]


def test_a_failed_reconcile_is_not_a_failed_close() -> None:
    # Reconciliation is an optimisation (a truer state list), never a step the
    # close depends on, so its failure must not abort the close.
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp"), state=WorkspaceState.STOPPED))
    manager.reconcile_all.side_effect = RuntimeError("docker unreachable")
    assert CloseSequence(manager, _recording_hooks(calls)).run() is True
    assert any("arrêt de « Demo »" in message for message in calls)


def test_the_steps_run_in_order_export_sync_then_stop() -> None:
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp"), name="A"), _workspace(Path("/tmp"), name="B"))
    sequence = _sequence(manager, _recording_hooks(calls), calls)
    manager.sync_git.side_effect = lambda *_a, **_k: calls.append("sync")

    assert sequence.run() is True
    assert calls == [
        "Fermeture : synchronisation des workflows puis arrêt de n8n…",
        "Fermeture : synchronisation de « A »…",
        "export",
        "sync",
        "Fermeture : arrêt de « A »…",
        "Fermeture : synchronisation de « B »…",
        "export",
        "sync",
        "Fermeture : arrêt de « B »…",
    ]


def test_a_stopped_workspace_is_stopped_without_an_export() -> None:
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp"), state=WorkspaceState.STOPPED))
    sequence = _sequence(manager, _recording_hooks(calls), calls)
    assert sequence.run() is True
    manager.sync_git.assert_not_called()
    assert "export" not in calls


def test_a_running_workspace_without_a_key_is_stopped_without_an_export() -> None:
    # Without the key there is no session to read the workflows back with, so
    # there is nothing to export even though n8n is up.
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp"), api_key=None))
    sequence = _sequence(manager, _recording_hooks(calls), calls)
    assert sequence.run() is True
    manager.sync_git.assert_not_called()
    assert "export" not in calls


def test_a_local_commit_that_could_not_be_pushed_is_reported_but_not_retried() -> None:
    reported: list[Workspace] = []
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp")))
    workspace = manager.list.return_value[0]
    workspace.git_push_failed = True
    hooks = _recording_hooks(calls, on_push_failed=reported.append)

    assert _sequence(manager, hooks, calls).run() is True
    # Reported once, and the close carries on: the work is committed, and the
    # flag is also raised by a stage failure, so nothing is retried over it.
    assert reported == [workspace]
    manager.stop.assert_called_once_with(workspace.id)


def test_a_failed_sync_is_asked_about() -> None:
    calls: list[str] = []
    seen: list[Exception] = []
    manager = _manager(_workspace(Path("/tmp")))
    workspace = manager.list.return_value[0]
    manager.sync_git.side_effect = RuntimeError("git push refused")
    hooks = _recording_hooks(calls, on_sync_failed=lambda w, exc: bool(seen.append(exc)) and False)

    assert _sequence(manager, hooks, calls).run() is True
    # "No" means stop without syncing: the workspace still has to come down.
    assert isinstance(seen[0], RuntimeError)
    manager.stop.assert_called_once_with(workspace.id)


def test_a_failed_sync_can_be_retried() -> None:
    attempts: list[int] = []

    def _sync(*_args: object, **_kwargs: object) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("git push refused")

    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp")))
    manager.sync_git.side_effect = _sync
    hooks = _recording_hooks(calls, on_sync_failed=lambda _w, _e: True)

    assert _sequence(manager, hooks, calls).run() is True
    assert len(attempts) == 2
    assert manager.stop.call_count == 1


def test_declining_to_retry_a_sync_does_not_re_export() -> None:
    exports: list[int] = []
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp")))
    manager.sync_git.side_effect = RuntimeError("git push refused")
    sequence = _sequence(
        manager, _recording_hooks(calls, on_sync_failed=lambda _w, _e: False), calls
    )
    sequence._export = lambda _w: exports.append(1)  # type: ignore[method-assign]
    assert sequence.run() is True
    assert exports == [1]


def test_aborting_a_sync_leaves_the_rest_running() -> None:
    calls: list[str] = []
    manager = _manager(_workspace(Path("/tmp"), name="A"), _workspace(Path("/tmp"), name="B"))
    manager.sync_git.side_effect = RuntimeError("git push refused")
    # The quiet default is "abort": a headless caller that has not said what to do
    # must not quietly stop workspaces it could not synchronise.
    assert _sequence(manager, _recording_hooks(calls), calls).run() is False
    manager.stop.assert_not_called()


def test_a_failed_stop_can_be_retried() -> None:
    attempts: list[int] = []
    calls: list[str] = []

    def _stop(_id: str) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("docker down failed")

    manager = _manager(_workspace(Path("/tmp"), state=WorkspaceState.STOPPED))
    manager.stop.side_effect = _stop
    hooks = _recording_hooks(calls, on_stop_failed=lambda _w, _e: True)
    assert CloseSequence(manager, hooks).run() is True
    assert len(attempts) == 2


def test_ignoring_a_failed_stop_closes_the_others_and_says_so() -> None:
    calls: list[str] = []
    manager = _manager(
        _workspace(Path("/tmp"), name="A", state=WorkspaceState.STOPPED),
        _workspace(Path("/tmp"), name="B", state=WorkspaceState.STOPPED),
    )
    manager.stop.side_effect = [RuntimeError("docker down failed"), None]
    hooks = _recording_hooks(calls, on_stop_failed=lambda _w, _e: False)
    assert CloseSequence(manager, hooks).run() is True
    # Skipped, but never silently: the user is told what did not come down.
    assert "Fermeture : arrêt de « A » ignoré." in calls
    manager.stop.assert_any_call("ws-B")


def test_aborting_a_stop_leaves_the_others_alone() -> None:
    calls: list[str] = []
    manager = _manager(
        _workspace(Path("/tmp"), name="A", state=WorkspaceState.STOPPED),
        _workspace(Path("/tmp"), name="B", state=WorkspaceState.STOPPED),
    )
    manager.stop.side_effect = RuntimeError("docker down failed")
    hooks = _recording_hooks(calls, on_stop_failed=lambda _w, _e: None)
    assert CloseSequence(manager, hooks).run() is False
    manager.stop.assert_called_once_with("ws-A")


def test_the_default_hooks_are_the_quiet_headless_answer() -> None:
    hooks = CloseHooks()
    hooks.on_progress("ignored")
    assert hooks.on_push_failed(_workspace(Path("/tmp"))) is None
    # Neither True nor False: "I have no opinion", which aborts rather than
    # guessing a decision that could lose a user's work.
    assert hooks.on_sync_failed(_workspace(Path("/tmp")), RuntimeError()) is None
    assert hooks.on_stop_failed(_workspace(Path("/tmp")), RuntimeError()) is None
