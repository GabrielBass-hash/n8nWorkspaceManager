import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from n8n_launcher.core.config import ConfigError, ConfigStore, ensure_directories
from n8n_launcher.core.models import (
    AppConfig,
    DbConfig,
    DbMode,
    GitConfig,
    ServerConfig,
    Workspace,
    WorkspaceState,
)


def _sample_config(work_dir: Path) -> AppConfig:
    """A config exercising every field the store serializes."""
    return AppConfig(
        "owner@example.test",
        "secret",
        work_dir,
        [
            Workspace(
                id="ws-a",
                name="Alpha",
                workflows_dir=work_dir / "alpha",
                port=5678,
                db=DbConfig(DbMode.MANAGED, "alpha_db", "alpha_user", "alpha_pw"),
                state=WorkspaceState.RUNNING,
            ),
            Workspace(
                id="ws-b",
                name="Beta",
                workflows_dir=work_dir / "beta",
                port=5680,
                db=DbConfig(DbMode.NONE),
                git=GitConfig(
                    enabled=True,
                    remote_url="https://github.com/o/r.git",
                    branch="dev",
                    ci_enabled=True,
                    ci_credentials=[{"name": "GitHub", "type": "githubOAuth2Api"}],
                ),
                server=ServerConfig(True, "host", 2222, "root", "/root/n8n", 5678),
                api_key="key-b",
            ),
        ],
        github_token="ghp_sample",
    )


def test_config_store_writes_and_reads_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = _sample_config(tmp_path / "work")
    store = ConfigStore(path)

    store.save(config)

    assert store.load() == config
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions differ on Windows")
def test_config_store_restricts_wal_and_shm_sidecars(tmp_path: Path) -> None:
    """The WAL/SHM sidecars must be as private as the database itself.

    They hold the same pages (owner password, GitHub token) and are created
    by SQLite with the default umask, so a leak here would defeat the 0o600
    on the main file.
    """
    path = tmp_path / "launcher.db"
    store = ConfigStore(path)
    store.save(_sample_config(tmp_path / "work"))

    assert path.stat().st_mode & 0o777 == 0o600
    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        assert sidecar.exists(), sidecar
        assert sidecar.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions differ on Windows")
def test_ensure_directories_restricts_an_existing_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config directory must be 0o700 even when it already exists."""
    target = tmp_path / "n8n-launcher"
    target.mkdir()
    target.chmod(0o755)
    monkeypatch.setattr(
        "n8n_launcher.core.paths.user_config_dir", lambda name, *a, **k: str(target)
    )

    ensure_directories()

    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700


def test_config_store_persists_across_reopen(tmp_path: Path) -> None:
    config = _sample_config(tmp_path / "work")
    ConfigStore(tmp_path / "config.json").save(config)

    reopened = ConfigStore(tmp_path / "config.json")

    assert reopened.load() == config


def test_config_store_does_not_create_advisory_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))

    # SQLite serializes writers itself; the old FileLock is gone.
    assert path.exists()
    assert not (tmp_path / "config.json.lock").exists()


def test_config_store_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="n'existe pas"):
        ConfigStore(tmp_path / "config.json").load()


def test_config_store_rewrites_only_changed_workspace_row(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = _sample_config(tmp_path / "work")
    store = ConfigStore(path)
    store.save(config)
    untouched_before = _row(path, "ws-b")

    store.mutate(lambda cfg: setattr(cfg.workspaces[0], "state", WorkspaceState.STOPPED))

    loaded = store.load()
    assert loaded.workspaces[0].state == WorkspaceState.STOPPED
    assert loaded.workspaces[0].name == config.workspaces[0].name
    assert loaded.workspaces[1] == config.workspaces[1]
    assert _row(path, "ws-b") == untouched_before  # only row ws-a was rewritten


def _row(path: Path, workspace_id: str) -> str | None:
    """Return the raw JSON blob SQLite stores for *workspace_id*."""
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT data FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return row[0] if row is not None else None
    finally:
        conn.close()


def test_config_mutate_returns_fn_result(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))

    result = store.mutate(lambda cfg: len(cfg.workspaces))

    assert result == 0


def test_config_mutate_rolls_back_when_fn_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = _sample_config(tmp_path / "work")
    store = ConfigStore(path)
    store.save(config)

    with pytest.raises(RuntimeError):
        store.mutate(lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")))

    assert store.load() == config


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_store_migrates_legacy_json_once(tmp_path: Path) -> None:
    legacy = tmp_path / "config.json"
    config = _sample_config(tmp_path / "work")
    legacy.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    db = tmp_path / "launcher.db"
    store = ConfigStore(db, legacy_json=legacy)

    assert store.load() == config

    # The JSON keeps its role as a backup of the pre-SQLite state.
    assert legacy.exists()
    assert db.exists()

    # A second read does not re-import anything (idempotent by construction).
    assert ConfigStore(db, legacy_json=legacy).load() == config
    replacement = AppConfig("x@y.z", "pw", tmp_path)
    ConfigStore(db, legacy_json=legacy).save(replacement)
    assert ConfigStore(db).load() == replacement


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_store_migrates_from_default_legacy_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "n8n_launcher.core.paths.user_config_dir", lambda name, *a, **k: str(tmp_path)
    )
    config = _sample_config(tmp_path / "work")
    (tmp_path / "config.json").write_text(json.dumps(config.to_dict()), encoding="utf-8")

    default_store = ConfigStore()

    assert default_store.load() == config
    assert (tmp_path / "launcher.db").exists()


def test_config_store_corrupt_legacy_config_raises(tmp_path: Path) -> None:
    legacy = tmp_path / "config.json"
    legacy.write_text("{broken json", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid launcher configuration"):
        ConfigStore(tmp_path / "launcher.db", legacy_json=legacy).load()


def test_config_store_corrupt_database_raises(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{broken json", encoding="utf-8")

    with pytest.raises(ConfigError, match="Invalid launcher configuration"):
        ConfigStore(path).load()


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_mutate_blocks_on_a_foreign_write_lock(tmp_path: Path) -> None:
    """A store cycle must not run while another process owns a write lock."""
    db = tmp_path / "config.json"
    store = ConfigStore(db)
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))
    holder = (
        "import sqlite3, sys, time\n"
        "conn = sqlite3.connect(sys.argv[1])\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "time.sleep(5)\n"
        "conn.rollback()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", holder, str(db)])
    try:
        time.sleep(0.4)  # let the child take the write lock
        started = time.monotonic()
        # mutate blocks on the foreign transaction and only runs after the
        # child releases (5 s), proving SQLite gates the whole cycle.
        store.mutate(lambda cfg: setattr(cfg, "workspaces", []))
        assert time.monotonic() - started >= 4.0
    finally:
        child.terminate()
        child.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_config_store_cycle_releases_the_write_lock_after_commit(tmp_path: Path) -> None:
    """Once a cycle commits, a sibling process may take the write lock."""
    db = tmp_path / "config.json"
    store = ConfigStore(db)
    store.save(AppConfig("owner@example.test", "secret", tmp_path / "work"))

    store.mutate(lambda cfg: setattr(cfg, "github_token", "ghp_new"))

    prober = (
        "import sqlite3, sys\n"
        "conn = sqlite3.connect(sys.argv[1])\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "conn.commit()\n"
    )
    result = subprocess.run([sys.executable, "-c", prober, str(db)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert store.load().github_token == "ghp_new"


def test_config_store_export_json(tmp_path: Path) -> None:
    config = _sample_config(tmp_path / "work")
    store = ConfigStore(tmp_path / "config.json")
    store.save(config)

    target = tmp_path / "export.json"
    store.export_json(target)

    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported == config.to_dict()
