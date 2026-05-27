"""Trap 5 — wall-clock timeouts.

Three layers:
  * per-call 90s — SDK ``TimeoutError`` is converted to
    :class:`CallTimeoutExceeded` (which is a :class:`RunawayTrapFired`)
  * per-agent 5 min — :func:`run_with_agent_timeout` wraps a coro
  * per-job 20 min — :func:`run_with_job_timeout` wraps a coro

We use short timeouts in tests so the suite stays fast.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.cost_control import (
    AgentTimeoutExceeded,
    BudgetState,
    CallTimeoutExceeded,
    CloudClient,
    JobBudgetGuard,
    JobTimeoutExceeded,
    run_with_agent_timeout,
    run_with_job_timeout,
)


# ---------------------------------------------------------------------------
# Per-call: SDK timeout becomes a CallTimeoutExceeded trap firing.
# ---------------------------------------------------------------------------

def test_sdk_timeout_becomes_call_timeout_trap(
    fake_sdk, budget_state, db_session, job_id
):
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )
    fake_sdk.set_next_error(TimeoutError("SDK timed out"))

    with pytest.raises(CallTimeoutExceeded):
        client.messages_create(
            agent="needs",
            model="claude-haiku-4-5",
            job_id=job_id,
            messages=[{"role": "user", "content": "x"}],
            max_tokens=1000,
            input_tokens_estimate=10,
        )


# ---------------------------------------------------------------------------
# Per-agent timeout
# ---------------------------------------------------------------------------

def test_agent_timeout_raises_agent_timeout_exceeded():
    async def slow():
        await asyncio.sleep(0.5)
        return "done"

    async def runner():
        await run_with_agent_timeout(
            slow, agent="research", job_id="jid", timeout_s=0.05
        )

    with pytest.raises(AgentTimeoutExceeded):
        asyncio.run(runner())


def test_agent_within_timeout_returns_value():
    async def fast():
        return "ok"

    async def runner():
        return await run_with_agent_timeout(
            fast, agent="needs", job_id="jid", timeout_s=1.0
        )

    assert asyncio.run(runner()) == "ok"


# ---------------------------------------------------------------------------
# Per-job timeout
# ---------------------------------------------------------------------------

def test_job_timeout_raises_job_timeout_exceeded():
    async def slow():
        await asyncio.sleep(0.5)

    async def runner():
        await run_with_job_timeout(slow, job_id="jid", timeout_s=0.05)

    with pytest.raises(JobTimeoutExceeded):
        asyncio.run(runner())


def test_job_within_timeout_returns_value():
    async def fast():
        return 42

    async def runner():
        return await run_with_job_timeout(fast, job_id="jid", timeout_s=1.0)

    assert asyncio.run(runner()) == 42
