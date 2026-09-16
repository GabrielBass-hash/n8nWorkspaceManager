"""n8n instance integration: public API client, owner bootstrap and workflow
synchronization.

The public surface is re-exported here so consumers can import from
``n8n_launcher.n8n`` without knowing the internal file layout.
"""

from .api import N8nApiClient, N8nApiError
from .owner import (
    ApiCredentials,
    OwnerSetup,
    OwnerSetupError,
    REQUIRED_WORKFLOW_SCOPES,
    hash_owner_password,
)
from .workflows import SyncPolicy, SyncReport, SyncRunner

__all__ = [
    "ApiCredentials",
    "N8nApiClient",
    "N8nApiError",
    "OwnerSetup",
    "OwnerSetupError",
    "REQUIRED_WORKFLOW_SCOPES",
    "SyncPolicy",
    "SyncReport",
    "SyncRunner",
    "hash_owner_password",
]