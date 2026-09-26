import os
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def sandbox_platform_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep the whole session out of the developer's real launcher directories.

    ``config_dir()`` and ``logs_dir()`` resolve through ``platformdirs``, so any
    test that builds a default ``ConfigStore()`` or calls
    ``bootstrap_logging()`` without stubbing them writes to the real
    ``~/.config/n8n-launcher`` and ``~/.local/state/n8n-launcher`` — which is
    how the unit suite ended up injecting its own "Configuration illisible"
    events into the developer's monitoring journal. The two functions are
    patched rather than the XDG variables because macOS and Windows ignore
    those. A test-level ``monkeypatch.setattr`` still wins and unwinds back to
    these values, so per-test stubs keep working unchanged.
    """
    base = tmp_path_factory.mktemp("platformdirs")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "n8n_launcher.core.paths.user_config_dir",
            lambda name, *args, **kwargs: str(base / "config"),
        )
        patch.setattr(
            "n8n_launcher.core.paths.user_log_dir",
            lambda name, *args, **kwargs: str(base / "log"),
        )
        yield


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
