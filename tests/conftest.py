from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace


@pytest.fixture
def managed_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        id="demo123",
        name="Demo",
        workflows_dir=tmp_path / "workflows with spaces",
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
    )
