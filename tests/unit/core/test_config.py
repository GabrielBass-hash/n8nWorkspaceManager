import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from n8n_launcher.core.config import ConfigStore
from n8n_launcher.core.filelock import FileLock
from n8n_launcher.core.models import AppConfig


def _lock_probe(lock_path: Path) -> int:
    """Child process: 0 when *lock_path* is refused, 2 when acquirable."""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from n8n_launcher.core.filelock import FileLock, LockError\n"
        "try:\n"
        "    FileLock(Path(sys.argv[1])).acquire(blocking=False)\n"
        "except LockError:\n"
        "    sys.exit(0)\n"
        "sys.exit(2)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(lock_path)],
        capture_output=True,
        cwd=Path(__file__).parents[3],
    )
    assert result.returncode in (0, 2), result.stderr.decode()
    return result.returncode


def test_config_store_writes_and_reads_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = AppConfig("owner@example.test", "secret", tmp_path / "work")
    store = ConfigStore(path)

    store.save(config)

    assert store.load() == config
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_config_store_uses_dedicated_lock_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))

    assert (tmp_path / "config.json.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_store_holds_a_real_process_lock(tmp_path: Path) -> None:
    """The config lock file must fight sibling processes while a store cycle
    is in flight, and yield the OS lock once the cycle is done."""
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))
    lock_path = tmp_path / "config.json.lock"

    store.mutate(lambda cfg: cfg)  # completes, then the lock is released
    assert _lock_probe(lock_path) == 2

    # While an OS lock is held on the same file, a child process is refused.
    held = FileLock(lock_path)
    with held.locked():
        assert _lock_probe(lock_path) == 0
    assert _lock_probe(lock_path) == 2

    assert store.load() == AppConfig("owner@example.test", "secret", tmp_path / "work")


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_mutate_blocks_on_a_foreign_lock(tmp_path: Path) -> None:
    """A store cycle must not run while another process owns the lock file."""
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))
    lock_path = tmp_path / "config.json.lock"
    holder = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from n8n_launcher.core.filelock import FileLock\n"
        "with FileLock(Path(sys.argv[1])).locked():\n"
        "    time.sleep(5)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", holder, str(lock_path)], cwd=Path(__file__).parents[3]
    )
    try:
        time.sleep(0.4)  # let the child take the lock
        started = time.monotonic()
        # mutate blocks on the foreign exclusive lock and only runs after the
        # child releases (5 s), proving the file lock gates the whole cycle.
        store.mutate(lambda cfg: setattr(cfg, "workspaces", []))
        assert time.monotonic() - started >= 4.0
    finally:
        child.terminate()
        child.wait(timeout=10)
