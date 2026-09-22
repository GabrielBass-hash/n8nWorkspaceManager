"""n8n instance integration: public API client, owner bootstrap and workflow
synchronization.

The public surface is re-exported here so consumers can import from
``n8n_launcher.n8n`` without knowing the internal file layout.
"""

from .api import N8nApiClient, N8nApiError
from .owner import (
    REQUIRED_WORKFLOW_SCOPES,
    ApiCredentials,
    OwnerSetup,
    OwnerSetupError,
    hash_owner_password,
)
from .workflows import SyncPolicy, SyncReport, SyncRunner

__all__ = [
    "REQUIRED_WORKFLOW_SCOPES",
    "ApiCredentials",
    "N8nApiClient",
    "N8nApiError",
    "OwnerSetup",
    "OwnerSetupError",
    "SyncPolicy",
    "SyncReport",
    "SyncRunner",
    "hash_owner_password",
]
