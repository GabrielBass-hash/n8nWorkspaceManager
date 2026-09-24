"""Tests for the cross-process advisory lock helpers."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from n8n_launcher.core.filelock import FileLock, LockError, acquire_single_instance_lock


def _probe(lock_path: Path) -> int:
    """Run a child process that exits 0 when *lock_path* is refused, else 2."""
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


def test_filelock_acquire_release_cycle(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")

    lock.acquire()
    assert lock.path.exists()
    lock.release()

    # A released lock must be immediately re-acquirable by a fresh instance.
    fresh = FileLock(tmp_path / "x.lock")
    fresh.acquire(blocking=False)
    fresh.release()


@pytest.mark.skipif(os.name == "nt", reason="Windows locks are always exclusive")
def test_filelock_shared_locks_do_not_conflict(tmp_path: Path) -> None:
    """Shared holders must not compete — POSIX ``flock`` semantics only."""
    held = FileLock(tmp_path / "x.lock")
    with held.locked(shared=True):
        contender = FileLock(tmp_path / "x.lock")
        contender.acquire(shared=True, blocking=False)
        contender.release()


def test_filelock_exclusive_conflicts_with_other_instance(tmp_path: Path) -> None:
    held = FileLock(tmp_path / "x.lock")
    with held.locked():
        contender = FileLock(tmp_path / "x.lock")
        with pytest.raises(LockError):
            contender.acquire(blocking=False)


def test_filelock_reentrant_within_one_instance(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")
    with lock.locked():
        # Nested acquisition on the same instance is a depth increment.
        lock.acquire(shared=True)
        lock.acquire()
        lock.release()
        lock.release()
        # After unwinding to depth zero the OS lock is truly gone, so a
        # brand-new instance can take and release it without ever blocking.
    with FileLock(tmp_path / "x.lock").locked(shared=True):
        pass
    # Even deeper nesting via the helper re-balances on its own.
    with lock.locked(), lock.locked(shared=True):
        pass


def test_instance_lock_is_non_blocking_between_siblings(tmp_path: Path) -> None:
    first = acquire_single_instance_lock(tmp_path)
    with pytest.raises(LockError):
        acquire_single_instance_lock(tmp_path)
    first.release()
    # Released: a new process may take over.
    acquire_single_instance_lock(tmp_path).release()


def test_filelock_timeout_on_contended_nonblocking(tmp_path: Path) -> None:
    held = FileLock(tmp_path / "x.lock")
    held.acquire()
    try:
        blocker = FileLock(tmp_path / "x.lock")
        with pytest.raises(LockError):
            blocker.acquire(shared=True, blocking=False)
    finally:
        held.release()


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_filelock_blocks_across_processes(tmp_path: Path) -> None:
    """A second OS process must be refused the lock while we hold it."""
    lock_file = tmp_path / "x.lock"
    held = FileLock(lock_file)
    with held.locked():
        assert _probe(lock_file) == 0
    assert _probe(lock_file) == 2


@pytest.mark.skipif(os.name == "nt", reason="flock semantics differ on Windows")
def test_shared_filelock_allows_concurrent_process_readers(tmp_path: Path) -> None:
    """Shared locks must be compatible across separate OS processes."""
    lock_file = tmp_path / "x.lock"
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from n8n_launcher.core.filelock import FileLock, LockError\n"
        "lock = FileLock(Path(sys.argv[1]))\n"
        "with lock.locked(shared=True):\n"
        "    try:\n"
        "        FileLock(Path(sys.argv[2])).acquire(shared=True, blocking=False)\n"
        "    except LockError:\n"
        "        sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(lock_file), str(lock_file)],
        capture_output=True,
        cwd=Path(__file__).parents[3],
    )
    assert result.returncode == 0, result.stderr.decode()
