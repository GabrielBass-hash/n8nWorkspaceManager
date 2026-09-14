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
    pull_remote_only: bool = True
    delete_orphans: bool = False


@dataclass(frozen=True)
class SyncReport:
    pulled: int = 0
    pushed: int = 0
    skipped: int = 0


class SyncRunner:
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
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="n8n-sync", daemon=False)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("Workflow sync did not stop within the timeout")
            self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def sync_once(self) -> SyncReport:
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
