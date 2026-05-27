"""Trap 7 — concurrent-job semaphore.

The semaphore caps in-flight jobs at ``concurrency.max_concurrent_jobs``.
Excess ``await``\\ ers wait for a slot rather than raise; a fail-fast
helper is also available for defensive callers.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.cost_control import ConcurrentJobLimitExceeded
from backend.cost_control import concurrency as concurrency_mod


# ---------------------------------------------------------------------------
# Default behaviour — excess jobs wait for a slot.
# ---------------------------------------------------------------------------

def test_excess_jobs_wait_until_slot_frees():
    """Reset semaphore to max=2; start three coroutines; the third
    must wait until one of the first two releases."""
    concurrency_mod._reset_for_tests(max_jobs=2)
    order: list[str] = []

    async def hold(label: str, sleep_for: float) -> None:
        async with concurrency_mod.get_job_semaphore():
            order.append(f"start-{label}")
            await asyncio.sleep(sleep_for)
            order.append(f"end-{label}")

    async def main():
        # A and B should start; C must wait for one to finish.
        await asyncio.gather(
            hold("A", 0.05),
            hold("B", 0.05),
            hold("C", 0.0),
        )

    asyncio.run(main())

    # First two starts are A and B; C only starts after one of them ends.
    assert order[:2] == ["start-A", "start-B"]
    assert "start-C" in order
    start_c_idx = order.index("start-C")
    # At least one of A/B must have ended before C starts.
    assert any(order[i] in {"end-A", "end-B"} for i in range(start_c_idx))


# ---------------------------------------------------------------------------
# Fail-fast helper for defensive callers.
# ---------------------------------------------------------------------------

def test_acquire_nowait_or_raise_takes_a_slot_when_available():
    concurrency_mod._reset_for_tests(max_jobs=1)
    # Acquire the only slot synchronously.
    concurrency_mod.acquire_nowait_or_raise(job_id="j1")
    # The next non-blocking attempt must fail fast.
    with pytest.raises(ConcurrentJobLimitExceeded):
        concurrency_mod.acquire_nowait_or_raise(job_id="j2")


# ---------------------------------------------------------------------------
# Pulls cap from config.yaml.
# ---------------------------------------------------------------------------

def test_configured_max_comes_from_config(monkeypatch):
    # Force a reload from config.yaml.
    concurrency_mod._semaphore = None
    concurrency_mod._configured_max = None
    sem = concurrency_mod.get_job_semaphore()
    # config.yaml in this repo sets max_concurrent_jobs: 3.
    assert concurrency_mod.configured_max() == 3
    # And the semaphore agrees.
    assert getattr(sem, "_value") == 3
