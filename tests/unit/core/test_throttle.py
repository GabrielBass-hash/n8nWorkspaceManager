"""Unit tests for the concurrency throttle (``core/throttle.py``)."""

from __future__ import annotations

import threading
import time

import pytest

from n8n_launcher.core.throttle import Throttle


def test_init_rejects_zero_limit() -> None:
    with pytest.raises(ValueError):
        Throttle(0)


def test_limit_exposes_the_ceiling() -> None:
    assert Throttle(4).limit == 4


def test_run_executes_and_returns_result() -> None:
    throttle = Throttle(2)
    assert throttle.run(lambda: 40 + 2) == 42


def test_run_releases_slot_after_error() -> None:
    throttle = Throttle(1)

    with pytest.raises(RuntimeError):
        throttle.run(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert throttle.run(lambda: "ok") == "ok"


def test_run_blocks_while_slots_full_then_runs_in_turn() -> None:
    throttle = Throttle(1)
    release_holder = threading.Event()
    entered: list[str] = []

    def first() -> None:
        entered.append("first")
        release_holder.wait(5)

    holder = threading.Thread(target=lambda: throttle.run(first), daemon=True)
    holder.start()
    time.sleep(0.05)  # let ``first`` acquire the only slot
    assert entered == ["first"]

    second_started = threading.Event()

    def second() -> None:
        entered.append("second")
        second_started.set()

    waiter = threading.Thread(target=lambda: throttle.run(second), daemon=True)
    waiter.start()
    time.sleep(0.05)
    assert not second_started.is_set()  # blocked: the slot is still held
    assert entered == ["first"]

    release_holder.set()
    holder.join(5)
    waiter.join(5)
    assert entered == ["first", "second"]


def test_try_run_runs_when_slot_free() -> None:
    throttle = Throttle(2)
    assert throttle.try_run(lambda: "free") == "free"


def test_try_run_skips_work_when_saturated() -> None:
    throttle = Throttle(1)
    release = threading.Event()

    holder = threading.Thread(
        target=lambda: throttle.run(lambda: release.wait(5)),
        daemon=True,
    )
    holder.start()
    time.sleep(0.05)

    assert throttle.try_run(lambda: "queued") is None

    release.set()
    holder.join(5)
    assert throttle.try_run(lambda: "again") == "again"
