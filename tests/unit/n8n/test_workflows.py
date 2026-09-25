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


def test_sync_runner_import_skips_by_export_id_even_when_renamed(tmp_path: Path) -> None:
    api = MagicMock()
    api.list_workflows.return_value = [{"id": "42", "name": "Meteo"}]
    # The file name carries the workflow id; the name inside was re-exported as
    # something else, so only the id suffix can catch the duplicate.
    (tmp_path / "Meteo_jour-42.json").write_text(
        '{"id": "42", "name": "Daily forecast", "nodes": [], "connections": {}}',
        encoding="utf-8",
    )
    runner = SyncRunner(api, tmp_path)

    report = runner.import_all()

    assert report.pushed == 0
    assert report.skipped == 1
    api.create_workflow.assert_not_called()


def test_sync_runner_export_refreshes_pipelines_and_root_mirror(tmp_path: Path) -> None:
    pipelines = tmp_path / "n8nPipelines"
    root = tmp_path
    api = MagicMock()
    api.list_workflows.return_value = [
        {"id": "1", "name": "Meteo"},
        {"id": "2", "name": "Nightly"},
    ]
    api.get_workflow.side_effect = [
        {"id": "1", "name": "Meteo", "nodes": []},
        {"id": "2", "name": "Nightly", "nodes": []},
    ]
    # Pre-existing launcher-named stale export and an unrelated root file.
    (root / "Meteo-1.json").write_text("stale", encoding="utf-8")
    (root / "Gone-9.json").write_text("orphan", encoding="utf-8")
    (root / "package.json").write_text('{"name": "app"}', encoding="utf-8")
    runner = SyncRunner(api, pipelines)

    report = runner.export_all(mirror=root)

    assert report.pulled == 2
    assert (pipelines / "Meteo-1.json").exists()
    assert (pipelines / "Nightly-2.json").exists()
    for path in (root / "Meteo-1.json", root / "Nightly-2.json"):
        assert path.read_text(encoding="utf-8").startswith('{\n  "id"')
    # Launcher-owned orphan cleaned, user file untouched.
    assert not (root / "Gone-9.json").exists()
    assert (root / "package.json").exists()


def test_sync_runner_export_preserves_unmanaged_pipeline_json(tmp_path: Path) -> None:
    pipelines = tmp_path / "n8nPipelines"
    pipelines.mkdir()
    custom = pipelines / "custom.json"
    custom.write_text("user owned", encoding="utf-8")
    directory_export = pipelines / "Archived-2.json"
    directory_export.mkdir()
    stale = pipelines / "Gone-9.json"
    stale.write_text("stale", encoding="utf-8")
    api = MagicMock()
    api.list_workflows.return_value = [{"id": "1", "name": "Meteo"}]
    api.get_workflow.return_value = {"id": "1", "name": "Meteo", "nodes": []}
    runner = SyncRunner(api, pipelines)

    runner.export_all()

    assert not stale.exists()
    assert custom.read_text(encoding="utf-8") == "user owned"
    assert directory_export.is_dir()


def test_sync_runner_export_without_mirror_leaves_root_untouched(tmp_path: Path) -> None:
    pipelines = tmp_path / "n8nPipelines"
    root = tmp_path
    (root / "outsider-42.json").write_text("user owned", encoding="utf-8")
    api = MagicMock()
    api.list_workflows.return_value = [{"id": "1", "name": "Meteo"}]
    api.get_workflow.return_value = {"id": "1", "name": "Meteo", "nodes": []}
    runner = SyncRunner(api, pipelines)

    runner.export_all()

    assert (pipelines / "Meteo-1.json").exists()
    assert (root / "outsider-42.json").read_text(encoding="utf-8") == "user owned"
