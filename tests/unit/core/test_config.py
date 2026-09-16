import os
from pathlib import Path

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.models import AppConfig


def test_config_store_writes_and_reads_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig("owner@example.test", "secret", tmp_path / "work")
    store = ConfigStore(path)

    store.save(config)

    assert store.load() == config
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
