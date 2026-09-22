from pathlib import Path
from unittest.mock import MagicMock

from n8n_launcher.n8n.workflows import SyncRunner


def test_sync_runner_pulls_workflows_and_stops(tmp_path: Path) -> None:
    api = MagicMock()
    api.list_workflows.return_value = [{"id": "one", "name": "My workflow"}]
    api.get_workflow.return_value = {"id": "one", "name": "My workflow", "nodes": []}
    runner = SyncRunner(api, tmp_path, interval=0.01)

    report = runner.sync_once()
    assert report.pulled == 1
    assert (tmp_path / "My_workflow-one.json").exists()

    runner.start()
    runner.stop()
    assert not runner.is_running()


def test_sync_runner_imports_local_workflow_exports(tmp_path: Path) -> None:
    api = MagicMock()
    api.list_workflows.return_value = []
    (tmp_path / "flow.json").write_text(
        '{"id": "local-1", "name": "Meteo", "nodes": [], "connections": {}, '
        '"settings": {}, "createdAt": "2024-01-01", "description": "x"}',
        encoding="utf-8",
    )
    runner = SyncRunner(api, tmp_path)

    report = runner.import_all()

    assert report.pushed == 1
    api.create_workflow.assert_called_once_with(
        {"name": "Meteo", "nodes": [], "connections": {}, "settings": {}}
    )


def test_sync_runner_import_defaults_settings_and_strips_server_fields(tmp_path: Path) -> None:
    api = MagicMock()
    api.list_workflows.return_value = []
    (tmp_path / "flow.json").write_text(
        '{"id": "local-1", "name": "Meteo", "nodes": [], "connections": {}, '
        '"active": true, "triggerCount": 1, "shared": []}',
        encoding="utf-8",
    )
    runner = SyncRunner(api, tmp_path)

    report = runner.import_all()

    assert report.pushed == 1
    api.create_workflow.assert_called_once_with(
        {"name": "Meteo", "nodes": [], "connections": {}, "settings": {}}
    )


def test_sync_runner_import_skips_unknown_and_non_workflow_files(tmp_path: Path) -> None:
    api = MagicMock()
    api.list_workflows.return_value = [{"id": "existing", "name": "Meteo"}]
    (tmp_path / "Meteo-existing.json").write_text(
        '{"id": "existing", "name": "Meteo", "nodes": [], "connections": {}}',
        encoding="utf-8",
    )
    (tmp_path / "plain-config.json").write_text('{"owner": "someone"}', encoding="utf-8")
    runner = SyncRunner(api, tmp_path)

    report = runner.import_all()

    assert report.pushed == 0
    assert report.skipped == 2
    api.create_workflow.assert_not_called()
