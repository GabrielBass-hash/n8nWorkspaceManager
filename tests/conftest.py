import os
from pathlib import Path

import pytest

from n8n_launcher.core.models import DbConfig, DbMode, Workspace


def pytest_addoption(parser: pytest.Parser) -> None:
    """Expose the snapshot directory shared by the structure/parity tests.

    ``test_structure.py`` writes ``structure-<os>.json`` there and
    ``test_parity.py`` reads the three OS files back from the same place. CI
    overrides it via ``--structure-dir`` (the parity job points at the merged
    artifact folder); local runs default to ``artifacts/``.
    """
    parser.addoption(
        "--structure-dir",
        action="store",
        default=os.environ.get("STRUCTURE_DIR", "artifacts"),
        help="Directory holding/receiving the structure-<os>.json snapshots",
    )


@pytest.fixture
def structure_dir(request: pytest.FixtureRequest) -> Path:
    """Resolve the configured snapshot directory as an absolute path."""
    return Path(request.config.getoption("--structure-dir"))


@pytest.fixture
def managed_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        id="demo123",
        name="Demo",
        workflows_dir=tmp_path / "workflows with spaces",
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
    )
