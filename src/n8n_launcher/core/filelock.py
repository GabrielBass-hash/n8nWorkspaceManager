"""Cross-process advisory locking backed by a local lock file.

A single ``threading.RLock`` (see :mod:`core.config`) protects the config
store against *thread* interleavings inside one process, but says nothing
about a second launcher process racing on the same file. This module adds the
missing process boundary: an advisory OS lock on a dedicated ``.lock`` file
next to the resource being protected.

- POSIX uses ``fcntl.flock`` (advisory, released automatically when the file
  descriptor closes, so a crashed process never leaves a stale lock behind).
- Windows has no shared ``flock`` equivalent; ``msvcrt.locking`` provides the
  same byte-range exclusion. Windows locks are always exclusive, which is all
  this launcher needs (``shared`` is a no-op distinction there).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["FileLock", "LockError", "acquire_single_instance_lock"]


class LockError(RuntimeError):
    """Raised when a non-blocking lock cannot be acquired."""


class FileLock:
    """Advisory inter-process lock backed by a file on disk.

    An instance is re-entrant **within a single process**: acquiring again on
    the same object (or any invocation of :meth:`locked`) increments a depth
    counter and must be balanced by the same number of releases. Different
    instances pointing at the same file are independent, so a second process
    (or a sibling instance) genuinely competes for the OS lock.

    On POSIX a ``shared`` lock allows concurrent readers; ``exclusive`` is
    required to write. On Windows every lock is exclusive.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None
        self._depth = 0
        self._guard = threading.Lock()

    @property
    def path(self) -> Path:
        """Return the lock file path."""
        return self._path

    @contextmanager
    def locked(self, *, shared: bool = False) -> Iterator[FileLock]:
        """Acquire the lock for the ``with`` block and release it on exit."""
        self.acquire(shared=shared)
        try:
            yield self
        finally:
            self.release()

    def acquire(self, *, shared: bool = False, blocking: bool = True) -> None:
        """Take the lock. Raises :class:`LockError` when *blocking* is false
        and another holder owns it."""
        with self._guard:
            if self._fd is not None:
                self._depth += 1
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
            self._fd = fd
            self._depth = 1
        try:
            self._lock_fd(fd, shared=shared, blocking=blocking)
        except LockError:
            os.close(fd)
            self._fd = None
            self._depth = 0
            raise
        except Exception:
            os.close(fd)
            self._fd = None
            self._depth = 0
            raise

    def release(self) -> None:
        """Release the lock. Each :meth:`acquire` must be balanced with one
        release; the OS lock and descriptor only go away at depth zero."""
        with self._guard:
            if self._fd is None:
                return
            self._depth -= 1
            if self._depth > 0:
                return
            fd, self._fd = self._fd, None
            self._depth = 0
        try:
            self._unlock_fd(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _lock_fd(fd: int, *, shared: bool, blocking: bool) -> None:
        if os.name == "nt":
            import msvcrt  # type: ignore[import-not-found]

            # Windows locks a byte range from the current file position; make
            # sure the file holds at least one byte to lock.
            os.lseek(fd, 0, os.SEEK_END)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"L")
            os.lseek(fd, 0, os.SEEK_SET)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(fd, mode, 1)
            except OSError as exc:
                if not blocking:
                    raise LockError(f"le verrou {fd} est déjà tenu") from exc
                raise
            return
        import fcntl  # type: ignore[import-not-found]

        flag = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            flag |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flag)
        except OSError as exc:
            if not blocking:
                raise LockError(f"le verrou {fd} est déjà tenu") from exc
            raise

    @staticmethod
    def _unlock_fd(fd: int) -> None:
        if os.name == "nt":
            import msvcrt  # type: ignore[import-not-found]

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        import fcntl  # type: ignore[import-not-found]

        fcntl.flock(fd, fcntl.LOCK_UN)

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def acquire_single_instance_lock(lock_dir: Path) -> FileLock:
    """Acquire a non-blocking lock proving "the launcher is already running".

    Returns the held :class:`FileLock` (keep it for the app's lifetime), or
    raises :class:`LockError` when a sibling instance already holds it.
    """
    lock = FileLock(lock_dir / "launcher.lock")
    lock.acquire(shared=False, blocking=False)
    return lock
