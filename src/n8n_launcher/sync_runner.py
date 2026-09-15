"""Optional workflow synchronization with controlled shutdown."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api_client import N8nApiClient


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
        logger: Any | None = None,
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
            target = self.workflows_dir / f"{_safe_name(workflow.get('name', workflow_id))}-{workflow_id}.json"
            if target.exists():
                skipped += 1
                continue
            detail = self.api.get_workflow(workflow_id)
            target.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            pulled += 1
        return SyncReport(pulled=pulled, skipped=skipped)

    def export_all(self) -> SyncReport:
        """Refresh every workflow from n8n and remove stale local exports."""
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        workflows = self.api.list_workflows()
        exported = set()
        pulled = 0
        for workflow in workflows:
            workflow_id = str(workflow.get("id", ""))
            if not workflow_id:
                continue
            target = self.workflows_dir / f"{_safe_name(workflow.get('name', workflow_id))}-{workflow_id}.json"
            detail = self.api.get_workflow(workflow_id)
            target.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            exported.add(target.name)
            pulled += 1
        for stale in self.workflows_dir.glob("*.json"):
            if stale.name not in exported:
                stale.unlink()
        return SyncReport(pulled=pulled)

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
            if path.name in existing_ids:
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


def _safe_name(value: Any) -> str:
    text = str(value or "workflow").strip()
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in text)
    return safe or "workflow"


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
