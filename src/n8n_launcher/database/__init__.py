"""Per-workspace data-database layer: schema layout detection, migration
runner and n8n credential provisioning.

The public surface is re-exported here so consumers can import from
``n8n_launcher.database`` without knowing the internal file layout.
"""

from ..core.models import (
    WORKSPACE_DATA_DB_NAME,
    WORKSPACE_DATA_DB_PASSWORD,
    WORKSPACE_DATA_DB_USER,
)
from .credentials import (
    DatabaseTarget,
    configure_db_credential,
    data_db_target,
)
from .layout import detect_migrations, detect_schema, has_db_layout
from .migrations import MigrationError, MigrationRunner

__all__ = [
    "WORKSPACE_DATA_DB_NAME",
    "WORKSPACE_DATA_DB_PASSWORD",
    "WORKSPACE_DATA_DB_USER",
    "DatabaseTarget",
    "MigrationError",
    "MigrationRunner",
    "configure_db_credential",
    "data_db_target",
    "detect_migrations",
    "detect_schema",
    "has_db_layout",
]
