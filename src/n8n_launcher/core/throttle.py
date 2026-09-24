"""Concurrency limiter for background work.

Scaling to ~100 workspaces means many background actions (``docker compose
up``, git sync, publish, CI reads) can be in flight at once, oversubscribing
the machine and its docker/git/ssh backends. :class:`Throttle` caps the number
of *simultaneously running* actions per process with a semaphore: callers wrap
their worker bodies in :meth:`Throttle.run` (blocking) or :meth:`Throttle.try_run`
(best-effort) so heavy work serializes behind a fixed budget instead of
spawning an unbounded pile of subprocesses.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class Throttle:
    """Bound the number of concurrently-running actions in this process.

    A ``limit`` of ``N`` lets ``N`` callables run at any given time; the
    ``N+1``-th call to :meth:`run` blocks until a slot frees up. The budget is
    local to the process and dies with it — it is a concurrency ceiling, not a
    cross-process lock (file locks in ``core/filelock.py`` cover those).
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit} instead")
        self._limit = limit
        self._slots = threading.BoundedSemaphore(limit)

    @property
    def limit(self) -> int:
        """The configured concurrency ceiling (constant after construction)."""
        return self._limit

    def run(self, action: Callable[[], T]) -> T:
        """Run *action* under the concurrency budget and return its result.

        Blocks while the budget is exhausted; the slot is released even if
        *action* raises, so one failure never starves the waiters behind it.
        """
        with self._slots:
            return action()

    def try_run(self, action: Callable[[], T]) -> T | None:
        """Run *action* only if a slot is free; return ``None`` when saturated.

        Non-blocking counterpart of :meth:`run` for best-effort work (probes,
        refreshes) that must never queue behind heavier actions.
        """
        if not self._slots.acquire(blocking=False):
            return None
        try:
            return action()
        finally:
            self._slots.release()
