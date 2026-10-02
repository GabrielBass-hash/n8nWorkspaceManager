import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Qt must never look for a display in the test suite. This has to be set before
# the first Qt import anywhere, which is why it sits at module import time
# rather than in a fixture; the offscreen platform draws nothing and is what
# every CI runner has. A test that needs the real platform can override it.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from n8n_launcher.core.models import DbConfig, DbMode, Workspace


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
def managed_workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        id="demo123",
        name="Demo",
        workflows_dir=tmp_path / "workflows with spaces",
        port=5678,
        db=DbConfig(mode=DbMode.MANAGED),
    )
