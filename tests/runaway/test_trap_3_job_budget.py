"""Trap 3 — per-job budget guard.

Pre-call estimate + spent must stay within the cap. Tests cover:
  * guard standalone math
  * SDK is *not* invoked when the estimate breaks the cap
  * realised cost is persisted on ``jobs.cost_usd``
  * a second call sees the spend from the first
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetExceeded,
    JobBudgetGuard,
)


# ---------------------------------------------------------------------------
# Standalone guard
# ---------------------------------------------------------------------------

def test_guard_blocks_when_estimate_breaks_cap(db_session, job_id) -> None:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=0.10, db=db_session)
    with pytest.raises(JobBudgetExceeded):
        guard.check_estimate(0.20)


def test_guard_allows_when_estimate_fits(db_session, job_id) -> None:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    guard.check_estimate(0.05)  # no raise


def test_guard_record_persists_on_jobs_row(db_session, job_id) -> None:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    guard.record(0.10)
    guard.record(0.20)

    val = db_session.execute(
        sa.text("SELECT cost_usd FROM jobs WHERE id = :id"),
        {"id": job_id},
    ).scalar()
    assert val == pytest.approx(0.30)
    assert guard.spent_usd == pytest.approx(0.30)


def test_guard_negative_inputs_rejected(db_session, job_id) -> None:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    with pytest.raises(ValueError):
        guard.check_estimate(-0.01)
    with pytest.raises(ValueError):
        guard.record(-0.01)


# ---------------------------------------------------------------------------
# CloudClient — SDK must not run when Trap 3 fires
# ---------------------------------------------------------------------------

def test_sdk_untouched_when_per_job_cap_broken(
    fake_sdk, budget_state: BudgetState, db_session, job_id
) -> None:
    # Cap so low that any nontrivial call breaks it.
    guard = JobBudgetGuard(job_id=job_id, cap_usd=0.001, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )
    fake_sdk.set_next_response(input_tokens=1000, output_tokens=2000)

    with pytest.raises(JobBudgetExceeded):
        client.messages_create(
            agent="research",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "x"}],
            max_tokens=2000,
            input_tokens_estimate=1000,
        )
    assert fake_sdk.calls == []


def test_realised_cost_blocks_subsequent_call(
    fake_sdk, budget_state: BudgetState, db_session, job_id
) -> None:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=0.05, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )

    # First call: estimate 0.033 < 0.05 → passes; actual cost is also small.
    fake_sdk.set_next_response(input_tokens=1000, output_tokens=2000)
    result_1 = client.messages_create(
        agent="critic",
        model="claude-sonnet-4-6",
        job_id=job_id,
        messages=[{"role": "user", "content": "first"}],
        max_tokens=2000,
        input_tokens_estimate=1000,
    )
    assert result_1.cost_usd > 0

    # Second call: same estimate, but now spent + estimate > 0.05.
    fake_sdk.set_next_response(input_tokens=1000, output_tokens=2000)
    with pytest.raises(JobBudgetExceeded):
        client.messages_create(
            agent="critic",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "second"}],
            max_tokens=2000,
            input_tokens_estimate=1000,
        )
    # Only the first call hit the SDK.
    assert len(fake_sdk.calls) == 1
