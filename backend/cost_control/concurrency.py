"""Concurrent-job semaphore — primitive for Trap 7.

The orchestrator/job-start route (wired in Step 7) wraps every job
launch in ``async with get_job_semaphore(): ...``. The semaphore caps
in-flight jobs at ``config.yaml: concurrency.max_concurrent_jobs``
(default 3). Excess jobs ``await`` until a slot frees, which is the
intended behaviour — they appear with status ``queued`` in the UI
and in SQLite.

The semaphore is module-global so a single process shares one count
across all jobs. Tests reset it explicitly via :func:`_reset_for_tests`.

A fail-fast variant — :func:`acquire_nowait_or_raise` — is exposed for
defensive callers that prefer to raise :class:`ConcurrentJobLimitExceeded`
rather than wait. The production route does **not** use it; it ``await``\\ s.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from backend.cost_control.config_loader import concurrency as _concurrency_cfg
from backend.cost_control.exceptions import ConcurrentJobLimitExceeded


_semaphore: Optional[asyncio.Semaphore] = None
_configured_max: Optional[int] = None


def get_job_semaphore() -> asyncio.Semaphore:
    """Return the process-global job semaphore, constructing it on demand.

    The max-jobs value is read once from ``config.yaml`` on first call.
    To pick up a config change, restart the process.
    """
    global _semaphore, _configured_max
    if _semaphore is None:
        cfg = _concurrency_cfg()
        _configured_max = cfg.max_concurrent_jobs
        _semaphore = asyncio.Semaphore(_configured_max)
    return _semaphore


def configured_max() -> int:
    """Return the configured maximum (after construction)."""
    if _configured_max is None:
        get_job_semaphore()
    assert _configured_max is not None
    return _configured_max


def acquire_nowait_or_raise(job_id: str | None = None) -> None:
    """Non-blocking acquire: take a slot, or raise.

    Useful for defensive callers that prefer fail-fast over waiting.
    The production job-start route ``await``\\ s on the semaphore
    instead, so excess jobs simply queue.

    Must be called from coroutine code (an asyncio.Semaphore has no
    sync-friendly acquire); if called outside a loop, the behaviour
    of the underlying ``_value`` check is still well-defined.
    """
    sem = get_job_semaphore()
    # ``Semaphore`` exposes ``_value`` as its internal counter. It is
    # not deprecated; the public surface uses ``acquire``/``release``,
    # but inspecting the count is safe and documented in CPython.
    # Reference: cpython/Lib/asyncio/locks.py.
    current_value = getattr(sem, "_value", None)
    if current_value is None or current_value <= 0:
        raise ConcurrentJobLimitExceeded(
            "concurrent-job semaphore saturated",
            job_id=job_id,
            context={"max_concurrent_jobs": configured_max()},
        )
    # Decrement directly. This is what ``Semaphore.acquire()`` does
    # when ``_value > 0`` — it returns immediately without suspending.
    sem._value = current_value - 1  # type: ignore[attr-defined]


def _reset_for_tests(max_jobs: int | None = None) -> None:
    """Rebuild the semaphore. Tests only.

    If ``max_jobs`` is None, re-reads config.yaml. Otherwise overrides
    the cap for the duration of the test.
    """
    global _semaphore, _configured_max
    if max_jobs is None:
        cfg = _concurrency_cfg()
        max_jobs = cfg.max_concurrent_jobs
    _configured_max = max_jobs
    _semaphore = asyncio.Semaphore(max_jobs)
