"""Per-workspace data-database layer: schema layout detection, migration
runner and n8n credential provisioning.

Entry: ``has_db_layout`` (``layout.py``), ``MigrationRunner`` (``migrations.py``),
``data_db_target`` (``credentials.py``).
Launcher-local only: the generated remote ``deploy.py`` never runs migrations.
The public surface is re-exported here so consumers can import from
``n8n_launcher.database`` without knowing the internal file layout.
Map: ``docs/architecture.md``.
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
