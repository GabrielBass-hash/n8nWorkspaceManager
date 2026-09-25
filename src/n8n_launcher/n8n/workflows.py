"""Optional workflow synchronization with controlled shutdown."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import N8nApiClient

# Matches the launcher's own export filenames (``<name>-<id>.json``) so the
# root mirror can tell its files apart from user-authored JSON (package.json,
# manually named exports…) and clean only its own orphans.
EXPORT_NAME_RE = re.compile(r".+-\d+\.json$")


@dataclass(frozen=True)
class SyncPolicy:
    """Controls how synchronization treats remote and local workflows."""

    pull_remote_only: bool = True
    delete_orphans: bool = False


@dataclass(frozen=True)
class SyncReport:
    """Counts of workflows pulled, pushed and skipped by a sync pass."""

    pulled: int = 0
    pushed: int = 0
    skipped: int = 0


class SyncRunner:
    """Optionally periodic two-way synchronization with a workspace folder."""

    def __init__(
        self,
        api: N8nApiClient,
        workflows_dir: Path,
        policy: SyncPolicy | None = None,
        logger: logging.Logger | None = None,
        interval: float = 10.0,
    ) -> None:
        self.api = api
        self.workflows_dir = workflows_dir
        self.policy = policy or SyncPolicy()
        self.logger = logger
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background sync loop (no-op if already running)."""
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="n8n-sync", daemon=False)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the sync thread to stop and wait for it to join."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("Workflow sync did not stop within the timeout")
            self._thread = None

    def is_running(self) -> bool:
        """Return True while the background sync thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def sync_once(self) -> SyncReport:
        """Pull every remote workflow that is not yet exported locally."""
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        pulled = 0
        skipped = 0
        for workflow in self.api.list_workflows():
            workflow_id = str(workflow.get("id", ""))
            if not workflow_id:
                skipped += 1
                continue
            target = (
                self.workflows_dir
                / f"{_safe_name(workflow.get('name', workflow_id))}-{workflow_id}.json"
            )
            if target.exists():
                skipped += 1
                continue
            detail = self.api.get_workflow(workflow_id)
            target.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            pulled += 1
        return SyncReport(pulled=pulled, skipped=skipped)

    def export_all(self, mirror: Path | None = None) -> SyncReport:
        """Refresh every workflow from n8n and remove stale local exports.

        When *mirror* is given (the workspace root, on close/stop/publish) the
        same per-workflow bodies are written there too — import and the CI
        harness read both the ``n8nPipelines/`` folder and the root, so without
        this the root copies would stay stale forever. Cleanup in the mirror is
        restricted to launcher-named files (``<name>-<id>.json``), so unrelated
        root files (``package.json``, hand-written exports with a different
        naming) are never touched.
        """
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        workflows = self.api.list_workflows()
        exported = set()
        live_mirror = set() if mirror is not None else None
        pulled = 0
        for workflow in workflows:
            workflow_id = str(workflow.get("id", ""))
            if not workflow_id:
                continue
            name = _safe_name(workflow.get("name", workflow_id))
            filename = f"{name}-{workflow_id}.json"
            body = json.dumps(self.api.get_workflow(workflow_id), indent=2, sort_keys=True) + "\n"
            (self.workflows_dir / filename).write_text(body, encoding="utf-8")
            exported.add(filename)
            if mirror is not None and live_mirror is not None:
                (mirror / filename).write_text(body, encoding="utf-8")
                live_mirror.add(filename)
            pulled += 1
        for stale in self.workflows_dir.glob("*.json"):
            if stale.name not in exported and EXPORT_NAME_RE.match(stale.name) and stale.is_file():
                stale.unlink()
        if mirror is not None and live_mirror is not None:
            self._clean_mirror(mirror, live_mirror)
        return SyncReport(pulled=pulled)

    def _clean_mirror(self, mirror: Path, live: set[str]) -> None:
        """Remove launcher-owned root exports that no longer match a workflow."""
        for orphan in mirror.glob("*.json"):
            if EXPORT_NAME_RE.match(orphan.name) and orphan.name not in live:
                orphan.unlink()

    def import_all(self) -> SyncReport:
        """Create in n8n every workflow JSON stored in the workspace folder.

        Files are n8n workflow exports (name, nodes, connections). Workflows
        already known to n8n (by id or by name) are skipped. Returns how many
        workflows were created.
        """
        existing = self.api.list_workflows()
        existing_ids = {str(item.get("id")) for item in existing if item.get("id")}
        existing_names = {str(item.get("name")) for item in existing if item.get("name")}
        pushed = 0
        skipped = 0
        for path in sorted(self.workflows_dir.glob("*.json")):
            if _export_id(path.stem) in existing_ids:
                skipped += 1
                continue
            try:
                workflow = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                skipped += 1
                continue
            if not isinstance(workflow, dict) or "nodes" not in workflow:
                skipped += 1
                continue
            if str(workflow.get("name")) in existing_names:
                skipped += 1
                continue
            self.api.create_workflow(_create_payload(workflow))
            pushed += 1
        return SyncReport(pushed=pushed, skipped=skipped)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception as exc:
                if self.logger:
                    self.logger.exception("Workflow sync failed", exc_info=exc)
            self._stop_event.wait(self.interval)


def _safe_name(value: object) -> str:
    text = str(value or "workflow").strip()
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_" for character in text
    )
    return safe or "workflow"


def _export_id(stem: str) -> str:
    """Extract the n8n workflow id carried by a launcher export file name.

    Launcher exports follow the ``<safe_name>-<id>.json`` convention (see
    :func:`_safe_name` and :meth:`SyncRunner.export_all`), so a file like
    ``Meteo-42.json`` encodes the id ``42`` even when the workflow's ``name``
    inside diverges from the file name — a renamed workflow is still skipped on
    re-import by its id instead of being re-created as a duplicate.
    """
    return stem.rsplit("-", 1)[-1]


def _create_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    """Build a create-request body that the public API accepts.

    The public POST /workflows endpoint validates the body against a strict
    OpenAPI schema (``additionalProperties: false``): any property outside its
    declared set is rejected with "must NOT have additional properties". A
    whitelist is therefore safer than a blacklist, because export files carry
    many read-only/server fields (``description``, ``active``,
    ``triggerCount``, ``shared``, ...) that are not part of the create schema.
    """
    allowed = {
        "name",
        "nodes",
        "connections",
        "settings",
        "staticData",
        "pinData",
        "nodeGroups",
        "projectId",
        "parentFolderId",
    }
    payload = {key: value for key, value in workflow.items() if key in allowed}
    # The public create schema marks settings as required.
    payload.setdefault("settings", {})
    return payload
