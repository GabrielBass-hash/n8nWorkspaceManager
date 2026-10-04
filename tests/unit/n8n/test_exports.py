"""The layout rule, stated once: ``n8n/exports.py`` owns the export layout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from n8n_launcher.n8n.exports import (
    CANONICAL_DIRNAME,
    ROOT_BLOCKLIST,
    SKIP_KNOWN_ID,
    SKIP_KNOWN_NAME,
    SKIP_NOT_A_WORKFLOW,
    SKIP_UNREADABLE,
    ExportPlan,
    ImportPlan,
    LocalExport,
    create_payload,
    export_filename,
    export_path,
    iter_local_exports,
    load_export,
    parse_export_id,
    plan_export,
    plan_import,
)


def _workflow(name: str, workflow_id: str = "") -> dict[str, Any]:
    """A minimal n8n export body, shaped like a real one on the extra fields."""
    body: dict[str, Any] = {
        "name": name,
        "nodes": [{"name": "Set", "type": "n8n-nodes-base.set"}],
        "connections": {},
        "settings": {},
    }
    if workflow_id:
        body["id"] = workflow_id
    return body


def _write(path: Path, body: dict[str, Any] | str) -> Path:
    """Write a workflow export, or raw text when given a string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _reference_tree(root: Path) -> Path:
    """Build the mixed layout every consumer has to agree on.

    A canonical export, its stale legacy mirror at the root, a conforming root
    export, a non-conforming root file, a manifest, a malformed JSON, and a
    directory named like an export.
    """
    canonical = root / CANONICAL_DIRNAME
    _write(canonical / "Meteo-42.json", _workflow("Meteo", "42"))
    _write(root / "Meteo-42.json", _workflow("Meteo", "42"))
    _write(root / "Veille-7.json", _workflow("Veille", "7"))
    _write(root / "notes.json", _workflow("Notes"))
    _write(root / "package.json", {"name": "app"})
    _write(root / "broken.json", "{not json")
    (canonical / "Custom.json").write_text("user owned", encoding="utf-8")
    (canonical / "Archived-3.json").mkdir()
    return root


# --- naming -----------------------------------------------------------------


def test_export_filename_sanitises_the_workflow_name() -> None:
    assert export_filename("Mon WS", "42") == "Mon_WS-42.json"
    # ``str.isalnum`` is Unicode-aware, so an accented name survives as-is and
    # only the path separators and spaces become underscores.
    assert export_filename("Météo / jour", "7") == "Météo___jour-7.json"
    assert export_filename("2024-01-02", "7") == "2024-01-02-7.json"


def test_export_filename_falls_back_when_the_name_is_missing() -> None:
    # The fallback is the literal "workflow", not the id: the file name has to
    # stay recognisable as a launcher export whatever the payload holds.
    assert export_filename(None, "42") == "workflow-42.json"
    assert export_filename("", "42") == "workflow-42.json"
    # A name made only of separators is truthy, so it is sanitised, not
    # replaced -- and an all-underscore result is still a conforming name.
    assert export_filename("///", "42") == "___-42.json"


def test_export_path_writes_the_canonical_layout(tmp_path: Path) -> None:
    assert export_path(tmp_path, "Mon WS", "42") == tmp_path / "n8nPipelines" / "Mon_WS-42.json"


def test_parse_export_id_reads_the_trailing_digits() -> None:
    assert parse_export_id("Meteo-42.json") == "42"
    assert parse_export_id("n8nPipelines/Meteo-42.json") == "42"
    assert parse_export_id("Meteo_jour-42.json") == "42"
    assert parse_export_id("n8n-ws-1.json") == "1"


def test_parse_export_id_rejects_files_outside_the_convention() -> None:
    # No name part, no dash, no digits: nothing to compare against an n8n id,
    # and answering None keeps such a file out of the prune's reach.
    assert parse_export_id("42.json") is None
    assert parse_export_id("-42.json") is None
    assert parse_export_id("Meteo.json") is None
    assert parse_export_id("Meteo-abc.json") is None
    assert parse_export_id("notes.txt") is None


# --- enumeration ------------------------------------------------------------


def test_iter_local_exports_prefers_the_canonical_copy(tmp_path: Path) -> None:
    _reference_tree(tmp_path)

    found = list(iter_local_exports(tmp_path))

    # The root mirror of a canonical export is yielded once, from the
    # canonical folder: reading it would import or upload a stale duplicate.
    assert [export.relpath for export in found] == [
        "n8nPipelines/Custom.json",
        "n8nPipelines/Meteo-42.json",
        "Veille-7.json",
        "broken.json",
        "notes.json",
    ]
    assert found[1].path == tmp_path / CANONICAL_DIRNAME / "Meteo-42.json"
    assert found[1].workflow_id == "42"
    assert found[1].name == "Meteo-42.json"


def test_iter_local_exports_keeps_a_non_conforming_root_file(tmp_path: Path) -> None:
    _write(tmp_path / "notes.json", _workflow("Notes"))

    found = list(iter_local_exports(tmp_path))

    # Outside the launcher convention means a user-authored export, not debris.
    assert [(export.name, export.workflow_id) for export in found] == [("notes.json", None)]


def test_iter_local_exports_skips_root_manifests_but_not_canonical_ones(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "package.json", {"name": "app"})
    _write(tmp_path / "Pipfile", {"_meta": {}})
    _write(tmp_path / CANONICAL_DIRNAME / "package.json", {"name": "app"})

    found = list(iter_local_exports(tmp_path))

    # The blocklist is a root-only rule: inside n8nPipelines/ the file is the
    # user's, and ROOT_BLOCKLIST must not silently hide it from a consumer.
    assert [export.relpath for export in found] == [f"{CANONICAL_DIRNAME}/package.json"]
    assert set(ROOT_BLOCKLIST) >= {"package.json", "Pipfile"}


def test_iter_local_exports_ignores_directories_and_missing_canonical(tmp_path: Path) -> None:
    _write(tmp_path / "Veille-7.json", _workflow("Veille", "7"))
    (tmp_path / "Dir-9.json").mkdir()

    found = list(iter_local_exports(tmp_path))

    # A workspace may legitimately have no canonical folder at all; the
    # enumeration degrades to the legacy root instead of raising.
    assert [export.relpath for export in found] == ["Veille-7.json"]


def test_iter_local_exports_reports_every_field(tmp_path: Path) -> None:
    _write(tmp_path / CANONICAL_DIRNAME / "Meteo-42.json", _workflow("Meteo", "42"))

    export = next(iter(iter_local_exports(tmp_path)))

    assert export == LocalExport(
        path=tmp_path / CANONICAL_DIRNAME / "Meteo-42.json",
        relpath="n8nPipelines/Meteo-42.json",
        workflow_id="42",
        name="Meteo-42.json",
    )


# --- reading ----------------------------------------------------------------


def test_load_export_returns_the_workflow_body(tmp_path: Path) -> None:
    _write(tmp_path / CANONICAL_DIRNAME / "Meteo-42.json", _workflow("Meteo", "42"))

    assert load_export(tmp_path, f"{CANONICAL_DIRNAME}/Meteo-42.json") == _workflow("Meteo", "42")


def test_load_export_returns_none_for_unreadable_or_invalid(tmp_path: Path) -> None:
    _write(tmp_path / "missing.json", _workflow("Gone"))
    _write(tmp_path / "bad.json", "{not json")
    _write(tmp_path / "no-nodes.json", {"name": "Nope"})
    _write(tmp_path / "list.json", "[]")

    assert load_export(tmp_path, "absent.json") is None
    assert load_export(tmp_path, "bad.json") is None
    assert load_export(tmp_path, "no-nodes.json") is None
    assert load_export(tmp_path, "list.json") is None


# --- plans ------------------------------------------------------------------


def test_plan_import_creates_new_exports_only(tmp_path: Path) -> None:
    _write(tmp_path / CANONICAL_DIRNAME / "Meteo-42.json", _workflow("Meteo", "42"))
    _write(tmp_path / "Veille-7.json", _workflow("Veille", "7"))

    plan = plan_import(tmp_path, [])

    assert isinstance(plan, ImportPlan)
    assert [candidate.export.relpath for candidate in plan.create] == [
        "n8nPipelines/Meteo-42.json",
        "Veille-7.json",
    ]
    assert plan.create[0].body == _workflow("Meteo", "42")
    assert plan.skip == ()


def test_plan_import_skips_a_renamed_workflow_by_the_id_in_its_file_name(tmp_path: Path) -> None:
    # The name inside diverges from the file name, so only the id suffix can
    # catch the duplicate.
    _write(tmp_path / "Meteo_jour-42.json", _workflow("Daily forecast", "42"))

    plan = plan_import(tmp_path, [{"id": "42", "name": "Daily forecast"}])

    assert plan.create == ()
    assert plan.skip[0][1] == SKIP_KNOWN_ID


def test_plan_import_only_reads_a_numeric_id_from_a_file_name(tmp_path: Path) -> None:
    # n8n ids are integers, so ``<name>-<id>.json`` only ever carries digits
    # there. A non-numeric suffix is a user-authored name, and judging it by
    # name alone is what keeps it from being mistaken for an id.
    _write(tmp_path / "Meteo-existing.json", _workflow("Meteo"))

    plan = plan_import(tmp_path, [{"id": "existing", "name": "Other"}])

    assert [candidate.export.name for candidate in plan.create] == ["Meteo-existing.json"]
    assert plan.skip == ()


def test_plan_import_reports_a_readable_reason_for_every_skip(tmp_path: Path) -> None:
    _write(tmp_path / "Meteo-77.json", _workflow("Meteo", "77"))
    _write(tmp_path / "plain-config.json", {"owner": "someone"})
    _write(tmp_path / "broken.json", "{not json")
    _write(tmp_path / "Fresh-3.json", _workflow("Taken"))
    _write(tmp_path / "New-4.json", _workflow("Brand new"))

    plan = plan_import(
        tmp_path,
        [{"id": "77", "name": "Meteo"}, {"id": "9", "name": "Taken"}],
    )

    reasons = {export.name: reason for export, reason in plan.skip}
    assert reasons == {
        "Meteo-77.json": SKIP_KNOWN_ID,
        "broken.json": SKIP_UNREADABLE,
        "plain-config.json": SKIP_NOT_A_WORKFLOW,
        "Fresh-3.json": SKIP_KNOWN_NAME,
    }
    assert [candidate.export.name for candidate in plan.create] == ["New-4.json"]


def test_plan_import_ignores_a_manifest_at_the_root(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", {"name": "app"})

    plan = plan_import(tmp_path, [])

    # A manifest is not a skip, it is simply not an export: reporting it would
    # charge the user's package.json against the sync counters.
    assert plan.create == ()
    assert plan.skip == ()


def test_plan_export_names_every_remote_workflow(tmp_path: Path) -> None:
    plan = plan_export(
        tmp_path,
        [{"id": "1", "name": "Mon WS"}, {"id": "2", "name": None}, {"id": "", "name": "No id"}],
    )

    assert isinstance(plan, ExportPlan)
    # The name comes from the payload and the fallback is "workflow", so the
    # file written is still recognisable as a launcher export.
    assert plan.write == (("Mon_WS-1.json", "1"), ("workflow-2.json", "2"))
    assert plan.prune == ()


def test_plan_export_prunes_only_launcher_named_orphans(tmp_path: Path) -> None:
    _reference_tree(tmp_path)

    plan = plan_export(tmp_path, [{"id": "42", "name": "Meteo"}])

    # Gone-7 style orphans go; a hand-written file, a manifest and a directory
    # that merely looks like an export all stay.
    assert plan.write == (("Meteo-42.json", "42"),)
    assert [path.name for path in plan.prune] == []


def test_plan_export_prunes_an_orphan_only_inside_the_canonical_folder(tmp_path: Path) -> None:
    _write(tmp_path / CANONICAL_DIRNAME / "Gone-9.json", _workflow("Gone", "9"))
    _write(tmp_path / "AlsoGone-9.json", _workflow("Gone", "9"))

    plan = plan_export(tmp_path, [{"id": "1", "name": "Meteo"}])

    assert [path.name for path in plan.prune] == ["Gone-9.json"]
    # The root is legacy read-only: nothing here is ever deleted.
    assert (tmp_path / "AlsoGone-9.json").exists()


# --- create payload ---------------------------------------------------------


def test_create_payload_whitelists_the_create_schema() -> None:
    payload = create_payload(
        {
            **_workflow("Meteo", "42"),
            # Server-owned fields an export carries but the schema refuses.
            "active": True,
            "triggerCount": 3,
            "shared": [],
            "createdAt": "2024-01-01",
            "updatedAt": "2024-06-01",
            "description": "x",
        }
    )

    assert payload == {
        "name": "Meteo",
        "nodes": [{"name": "Set", "type": "n8n-nodes-base.set"}],
        "connections": {},
        "settings": {},
    }


def test_create_payload_defaults_settings_when_absent() -> None:
    assert create_payload({"name": "Meteo", "nodes": [], "connections": {}}) == {
        "name": "Meteo",
        "nodes": [],
        "connections": {},
        "settings": {},
    }


def test_create_payload_keeps_an_explicit_settings_object() -> None:
    settings = {"executionOrder": "v1"}

    assert create_payload({"name": "M", "settings": settings})["settings"] == settings
