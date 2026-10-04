"""Where workflow exports live, how they are named, and how they are enumerated.

This module is the single owner of the export layout. Every other consumer --
the local :class:`~n8n_launcher.n8n.workflows.SyncRunner`, the CI harness
(``workspaces/ci.py``), the generated remote ``deploy.py`` -- asks it where a
file is and what it is called, instead of re-implementing the rule. The
generated scripts receive the constants below by token substitution, so a
change here moves every implementation at once.

Two directories are in play:

* ``n8nPipelines/`` is **canonical**: the launcher writes there and nowhere
  else.
* the workspace root is **legacy read-only**. Exports used to be mirrored there
  so that consumers reading two different folders would find fresh copies. Now
  that every consumer goes through :func:`iter_local_exports`, the mirror is
  gone and the root is only still read, so an existing workspace keeps working
  without a migration step.

:func:`ROOT_BLOCKLIST` applies to the **root only**. It exists to stop
manifest files (``package.json`` and friends) that live next to the workspace
from being read as workflow exports. It is deliberately *not* applied inside
``n8nPipelines/``: a user who drops ``package.json`` in there owns that file,
and the launcher's pruning already leaves non-conforming names alone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The one folder name the launcher writes to. Consumed by the generated
# scripts through the ``__CANONICAL_DIRNAME__`` token.
CANONICAL_DIRNAME = "n8nPipelines"

# Launcher exports are ``<safe_name>-<workflow_id>.json``. Matching this
# pattern is what lets a prune tell the launcher's own files apart from
# user-authored JSON it must never touch, and what lets a re-import skip a
# renamed workflow by the id carried in its file name.
EXPORT_NAME_RE = re.compile(r"^.+?-(\d+)\.json$")

# Root-level JSON files that are clearly not workflow exports. Root only -- see
# the module docstring.
ROOT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "composer.json",
        "Pipfile",
        "Pipfile.lock",
        "deno.json",
        "deno.lock",
    }
)

# Why an export was not pushed to n8n. Exported because they are part of
# ``ImportPlan.skip``: a caller that reports them must not hardcode the text.
SKIP_KNOWN_ID = "id déjà présent dans n8n (lu dans le nom du fichier)"
SKIP_UNREADABLE = "JSON illisible"
SKIP_NOT_A_WORKFLOW = "pas un export de workflow (pas de « nodes »)"
SKIP_KNOWN_NAME = "un workflow de ce nom existe déjà dans n8n"

# Whitelist of the fields the public create schema accepts. The POST endpoint
# validates with ``additionalProperties: false`` and rejects a server-only
# field with "must NOT have additional properties", and export files carry
# several (``description``, ``active``, ``triggerCount``, ``shared``, ...).
# The generated ``deploy.py`` receives this same tuple through the
# ``__WORKFLOW_KEYS__`` token, so the two payloads cannot drift apart.
WORKFLOW_KEYS: tuple[str, ...] = (
    "name",
    "nodes",
    "connections",
    "settings",
    "staticData",
    "pinData",
    "nodeGroups",
    "projectId",
    "parentFolderId",
)


def _safe_name(value: object) -> str:
    """Reduce a workflow name to the character set an export file name allows."""
    text = str(value or "workflow").strip()
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_" for character in text
    )
    return safe or "workflow"


def export_filename(name: object, workflow_id: str) -> str:
    """Return the launcher file name for a workflow.

    *name* is the value taken from the API payload, so it may be ``None`` or a
    non-string: a missing name falls back to the literal ``workflow`` rather
    than to the id, which is what the exported file name has always carried.
    """
    return f"{_safe_name(name)}-{workflow_id}.json"


def export_path(root: Path, name: object, workflow_id: str) -> Path:
    """Return the canonical path a workflow's export is written to."""
    return root / CANONICAL_DIRNAME / export_filename(name, workflow_id)


def parse_export_id(filename: str) -> str | None:
    """Return the n8n id a launcher export file name carries, else ``None``.

    A file named ``Meteo-42.json`` encodes the id ``42`` even when the workflow
    was renamed inside it, which is what lets a re-import skip a duplicate
    instead of creating it under a second name. Anything outside the
    ``<name>-<id>.json`` convention yields ``None``: such a file is either
    user-authored or a manifest, and neither carries an id to compare.
    """
    match = EXPORT_NAME_RE.match(Path(filename).name)
    return match.group(1) if match else None


@dataclass(frozen=True)
class LocalExport:
    """One workflow export found on disk."""

    path: Path
    # POSIX form, as the CI harness and the generated scripts report it
    # ("n8nPipelines/Meteo-42.json"), independent of the host separator.
    relpath: str
    workflow_id: str | None
    name: str


def _local_export(path: Path, relpath: str) -> LocalExport:
    return LocalExport(
        path=path,
        relpath=relpath,
        workflow_id=parse_export_id(path.name),
        name=path.name,
    )


def iter_local_exports(root: Path) -> Iterator[LocalExport]:
    """Yield every workflow export of a workspace, canonical ones first.

    ``n8nPipelines/*.json`` is enumerated, then the root-level ``*.json`` that
    are neither blocklisted nor a duplicate of a canonical file. A basename
    found in both places is yielded **once**, from ``n8nPipelines/``: the
    canonical copy is the one the launcher writes and keeps fresh, so reading
    the legacy copy would import or upload a stale version of the same
    workflow.
    """
    canonical = root / CANONICAL_DIRNAME
    canonical_names: set[str] = set()
    if canonical.is_dir():
        for path in sorted(canonical.glob("*.json")):
            if not path.is_file():
                continue
            canonical_names.add(path.name)
            yield _local_export(path, f"{CANONICAL_DIRNAME}/{path.name}")
    for path in sorted(root.glob("*.json")):
        if not path.is_file():
            continue
        if path.name in ROOT_BLOCKLIST or path.name in canonical_names:
            continue
        yield _local_export(path, path.name)


def _read_body(path: Path) -> dict[str, Any] | None:
    """Parse an export file, or return ``None`` when it is not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_export(root: Path, relpath: str) -> dict[str, Any] | None:
    """Load one export by its :attr:`LocalExport.relpath`; ``None`` if unusable.

    ``None`` covers every rejection the CI harness cares about -- missing file,
    unreadable JSON, not an object, no ``nodes`` key -- because a file that
    fails any of them is equally unusable there.
    """
    path = root / relpath
    if not path.is_file():
        return None
    body = _read_body(path)
    if body is None or "nodes" not in body:
        return None
    return body


@dataclass(frozen=True)
class ImportCandidate:
    """An export that will be created in n8n, with the body to create it from."""

    export: LocalExport
    body: dict[str, Any]


@dataclass(frozen=True)
class ImportPlan:
    """What a push of local exports would do, decided without touching n8n."""

    create: tuple[ImportCandidate, ...]
    skip: tuple[tuple[LocalExport, str], ...]


def plan_import(root: Path, remote: list[dict[str, Any]]) -> ImportPlan:
    """Decide which local exports to create in n8n, and why the others are not.

    *remote* is ``api.list_workflows()``. A workflow already in n8n is skipped
    twice over: by the id encoded in the export file name (which survives a
    rename inside the file), then by name. Nothing here talks to n8n -- the
    plan is a pure function of what is on disk plus what the caller already
    knows -- so the rules are testable without a server.
    """
    known_ids = {str(item.get("id")) for item in remote if item.get("id")}
    known_names = {str(item.get("name")) for item in remote if item.get("name")}
    create: list[ImportCandidate] = []
    skip: list[tuple[LocalExport, str]] = []
    for export in iter_local_exports(root):
        if export.workflow_id is not None and export.workflow_id in known_ids:
            skip.append((export, SKIP_KNOWN_ID))
            continue
        body = _read_body(export.path)
        if body is None:
            skip.append((export, SKIP_UNREADABLE))
            continue
        if "nodes" not in body:
            skip.append((export, SKIP_NOT_A_WORKFLOW))
            continue
        if str(body.get("name")) in known_names:
            skip.append((export, SKIP_KNOWN_NAME))
            continue
        create.append(ImportCandidate(export=export, body=body))
    return ImportPlan(create=tuple(create), skip=tuple(skip))


@dataclass(frozen=True)
class ExportPlan:
    """What a refresh of the canonical folder would write and remove."""

    # (filename, workflow_id) pairs. The filename is decided here rather than
    # by the caller so that the file actually written cannot disagree with the
    # set of names the prune protects.
    write: tuple[tuple[str, str], ...]
    prune: tuple[Path, ...]


def plan_export(root: Path, remote: list[dict[str, Any]]) -> ExportPlan:
    """Decide which canonical exports to write and which to delete.

    ``write`` names every workflow n8n holds; the caller fetches each body,
    because reading it is I/O and this plan is not. ``prune`` is restricted to
    the canonical folder and to files matching :data:`EXPORT_NAME_RE`, so a
    hand-written ``custom.json`` -- or a directory that happens to be named
    like an export -- survives.
    """
    write: list[tuple[str, str]] = []
    live: set[str] = set()
    for workflow in remote:
        workflow_id = str(workflow.get("id", ""))
        if not workflow_id:
            continue
        filename = export_filename(workflow.get("name", workflow_id), workflow_id)
        write.append((filename, workflow_id))
        live.add(filename)
    canonical = root / CANONICAL_DIRNAME
    prune = tuple(
        stale
        for stale in sorted(canonical.glob("*.json"))
        if stale.is_file() and stale.name not in live and EXPORT_NAME_RE.match(stale.name)
    )
    return ExportPlan(write=tuple(write), prune=prune)


def create_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    """Build a create-request body the public API accepts.

    A whitelist rather than a blacklist, because the create schema is strict
    and export files carry many read-only fields. ``settings`` is required by
    that schema, so it defaults to an empty object.
    """
    payload = {key: workflow[key] for key in WORKFLOW_KEYS if key in workflow}
    payload.setdefault("settings", {})
    return payload
