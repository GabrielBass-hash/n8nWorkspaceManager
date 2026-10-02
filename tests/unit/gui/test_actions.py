"""The background action layer, driven synchronously through an injected executor."""

from __future__ import annotations

from pathlib import Path

from n8n_launcher.core.models import DbConfig, DbMode, Workspace, WorkspaceState
from n8n_launcher.gui.actions import WorkspaceActions
from n8n_launcher.workspaces.dialogs import CreatePlan
from n8n_launcher.workspaces.manager import Reachable


class _Manager:
    """Record every action and optionally fail it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._fail = fail

    def _record(self, name: str, *args: object, **kwargs: object) -> Workspace:
        self.calls.append((name, args, kwargs))
        if self._fail:
            raise RuntimeError(f"{name} a échoué")
        return _workspace()

    def ensure_serving(self, workspace_id: str) -> Reachable:
        self.calls.append(("ensure_serving", (workspace_id,), {}))
        if self._fail:
            raise RuntimeError("ensure_serving a échoué")
        return Reachable(workspace=_workspace(), started=True)

    def stop(self, workspace_id: str) -> Workspace:
        return self._record("stop", workspace_id)

    def stop_with_sync(self, workspace_id: str) -> Workspace:
        return self._record("stop_with_sync", workspace_id)

    def delete(self, workspace_id: str) -> Workspace:
        return self._record("delete", workspace_id)

    def create(self, name: str, workflows_dir: Path, *, db: DbConfig | None = None) -> Workspace:
        return self._record("create", name, workflows_dir, db=db)


def _workspace() -> Workspace:
    return Workspace(
        id="ws",
        name="Mon workspace",
        workflows_dir=Path("/tmp/ws"),
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
        state=WorkspaceState.STOPPED,
    )


def _sync_executor(work, done, fail) -> None:  # type: ignore[no-untyped-def]
    """Run the task inline so the assertions have no race."""
    try:
        result = work()
    except Exception as exc:
        fail(exc)
    else:
        done(result)


def _actions(manager: _Manager) -> WorkspaceActions:
    return WorkspaceActions(manager, executor=_sync_executor)


def test_open_calls_the_manager(qt_app) -> None:
    manager = _Manager()
    _actions(manager).open(_workspace())
    assert manager.calls == [("ensure_serving", ("ws",), {})]


def test_stop_calls_the_manager(qt_app) -> None:
    """A manual stop exports and pushes: the work a close sequence would do."""
    manager = _Manager()
    _actions(manager).stop(_workspace())
    assert manager.calls == [("stop_with_sync", ("ws",), {})]


def test_delete_calls_the_manager(qt_app) -> None:
    manager = _Manager()
    _actions(manager).delete(_workspace())
    assert manager.calls == [("delete", ("ws",), {})]


def test_create_passes_the_plan_and_the_folder(qt_app) -> None:
    manager = _Manager()
    plan = CreatePlan(name="Nouveau", db=DbConfig(mode=DbMode.NONE))
    _actions(manager).create(plan, Path("/tmp/new"))
    assert manager.calls == [
        ("create", ("Nouveau", Path("/tmp/new")), {"db": DbConfig(mode=DbMode.NONE)})
    ]


def test_busy_is_announced_around_a_task(qt_app) -> None:
    actions = _actions(_Manager())
    states: list[bool] = []
    actions.busyChanged.connect(states.append)
    actions.open(_workspace())
    assert states == [True, False]


def test_open_announces_the_reachable_workspace(qt_app) -> None:
    actions = _actions(_Manager())
    ready: list[object] = []
    actions.ready.connect(ready.append)

    actions.open(_workspace())

    assert ready == [Reachable(workspace=_workspace(), started=True)]


def test_a_failure_is_reported_with_a_title(qt_app) -> None:
    actions = _actions(_Manager(fail=True))
    failures: list[tuple[str, str]] = []
    actions.failed.connect(lambda title, message: failures.append((title, message)))
    actions.open(_workspace())
    assert failures == [("Ouverture impossible", "ensure_serving a échoué")]


def test_creation_announces_the_new_workspace(qt_app) -> None:
    actions = _actions(_Manager())
    created: list[object] = []
    actions.created.connect(created.append)
    actions.create(CreatePlan(name="Nouveau", db=DbConfig(mode=DbMode.NONE)), Path("/tmp/new"))
    assert created and isinstance(created[0], Workspace)


def test_a_failed_creation_does_not_announce_a_workspace(qt_app) -> None:
    actions = _actions(_Manager(fail=True))
    created: list[object] = []
    actions.created.connect(created.append)
    actions.create(CreatePlan(name="Nouveau", db=DbConfig(mode=DbMode.NONE)), Path("/tmp/new"))
    assert created == []
