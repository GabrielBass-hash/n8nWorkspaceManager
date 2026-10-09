"""n8n instance integration: public API client, owner bootstrap and workflow
synchronization.

Entry: :class:`N8nApiClient` (``api.py``), :class:`~n8n_launcher.n8n.owner.OwnerSetup`
/ ``wait_for_n8n`` (``owner.py``), :class:`~n8n_launcher.n8n.workflows.SyncRunner`
(``workflows.py``).
Gotcha: ``/healthz`` is liveness, not readiness — see ``owner.wait_for_n8n``.
The public surface is re-exported here so consumers can import from
``n8n_launcher.n8n`` without knowing the internal file layout.
Map: ``docs/architecture.md``.
"""

from .api import N8nApiClient, N8nApiError
from .exports import (
    CANONICAL_DIRNAME,
    EXPORT_NAME_RE,
    ROOT_BLOCKLIST,
    SKIP_KNOWN_ID,
    SKIP_KNOWN_NAME,
    SKIP_NOT_A_WORKFLOW,
    SKIP_UNREADABLE,
    WORKFLOW_KEYS,
    ExportPlan,
    ImportCandidate,
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
from .owner import (
    REQUIRED_WORKFLOW_SCOPES,
    ApiCredentials,
    OwnerSetup,
    OwnerSetupError,
    hash_owner_password,
)
from .workflows import SyncPolicy, SyncReport, SyncRunner

__all__ = [
    "CANONICAL_DIRNAME",
    "EXPORT_NAME_RE",
    "REQUIRED_WORKFLOW_SCOPES",
    "ROOT_BLOCKLIST",
    "SKIP_KNOWN_ID",
    "SKIP_KNOWN_NAME",
    "SKIP_NOT_A_WORKFLOW",
    "SKIP_UNREADABLE",
    "WORKFLOW_KEYS",
    "ApiCredentials",
    "ExportPlan",
    "ImportCandidate",
    "ImportPlan",
    "LocalExport",
    "N8nApiClient",
    "N8nApiError",
    "OwnerSetup",
    "OwnerSetupError",
    "SyncPolicy",
    "SyncReport",
    "SyncRunner",
    "create_payload",
    "export_filename",
    "export_path",
    "hash_owner_password",
    "iter_local_exports",
    "load_export",
    "parse_export_id",
    "plan_export",
    "plan_import",
]
