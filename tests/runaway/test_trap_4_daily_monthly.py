"""Trap 4 — daily / monthly caps + ``--force`` override."""

from __future__ import annotations

import json

import pytest

from backend.cost_control import (
    BudgetState,
    CloudClient,
    DailyBudgetExceeded,
    JobBudgetGuard,
    MonthlyBudgetExceeded,
)
from backend.cost_control import audit as audit_mod
from backend.cost_control.config_loader import Budgets


# ---------------------------------------------------------------------------
# Standalone BudgetState
# ---------------------------------------------------------------------------

def test_below_soft_cap_no_warning(caps: Budgets) -> None:
    state = BudgetState(caps)
    decision = state.check_and_reserve(1.00)
    assert decision.proceed is True
    assert decision.warn_daily_soft is False


def test_above_soft_below_hard_warns(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(4.50)  # spent today
    decision = state.check_and_reserve(1.00)  # 5.50 > soft 5.00, < hard 10.00
    assert decision.proceed is True
    assert decision.warn_daily_soft is True


def test_daily_hard_cap_raises(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(9.00)
    with pytest.raises(DailyBudgetExceeded):
        state.check_and_reserve(2.00)  # 9 + 2 = 11 > hard 10


def test_daily_hard_cap_force_overrides(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(9.00)
    # --force allows the call through despite breaching daily-hard.
    decision = state.check_and_reserve(2.00, force=True)
    assert decision.proceed is True


def test_monthly_hard_cap_raises(caps: Budgets) -> None:
    state = BudgetState(caps)
    # Same-day commits roll into the month too, but cap is 50 so we
    # need to fake a higher start without crossing daily-hard.
    state.commit(9.00)
    # Manually inflate the month spend by directly editing the file.
    path = audit_mod.ANS_HOME / "budget_state.json"
    raw = json.loads(path.read_text())
    raw["month"]["spend_usd"] = 49.00
    path.write_text(json.dumps(raw))
    with pytest.raises(MonthlyBudgetExceeded):
        state.check_and_reserve(2.00)


def test_monthly_hard_cap_force_does_not_override(caps: Budgets) -> None:
    """Monthly hard cap is *not* bypassable via ``force``."""
    state = BudgetState(caps)
    state.commit(9.00)
    path = audit_mod.ANS_HOME / "budget_state.json"
    raw = json.loads(path.read_text())
    raw["month"]["spend_usd"] = 49.00
    path.write_text(json.dumps(raw))
    with pytest.raises(MonthlyBudgetExceeded):
        state.check_and_reserve(2.00, force=True)


def test_commit_increments_today_and_month(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(1.25)
    state.commit(0.75)
    snap = state.snapshot()
    assert snap["today"]["spend_usd"] == pytest.approx(2.00)
    assert snap["month"]["spend_usd"] == pytest.approx(2.00)


# ---------------------------------------------------------------------------
# Atomic-write hygiene — file mode and replace semantics
# ---------------------------------------------------------------------------

def test_state_file_created_with_0o600(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(0.10)
    path = audit_mod.ANS_HOME / "budget_state.json"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# Daily rollover
# ---------------------------------------------------------------------------

def test_daily_rollover_resets_today(caps: Budgets) -> None:
    state = BudgetState(caps)
    state.commit(3.00)
    # Pretend the persisted date is yesterday — rollover should zero
    # today's count on the next read.
    path = audit_mod.ANS_HOME / "budget_state.json"
    raw = json.loads(path.read_text())
    raw["today"]["date"] = "1999-01-01"
    path.write_text(json.dumps(raw))

    snap = state.snapshot()
    assert snap["today"]["spend_usd"] == 0.0
    # Month should still hold the prior total.
    assert snap["month"]["spend_usd"] == pytest.approx(3.00)


# ---------------------------------------------------------------------------
# CloudClient — SDK must not run when Trap 4 fires
# ---------------------------------------------------------------------------

def test_sdk_untouched_when_daily_hard_broken(
    fake_sdk, caps: Budgets, db_session, job_id
) -> None:
    state = BudgetState(caps)
    state.commit(9.50)  # right under daily_hard 10.00
    guard = JobBudgetGuard(job_id=job_id, cap_usd=5.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=state, job_guard=guard
    )
    fake_sdk.set_next_response(input_tokens=100_000, output_tokens=4000)

    # Estimate: 100_000 input × $5/MTok = $0.50; 4000 output × $25/MTok = $0.10.
    # Spent 9.50 + estimate 0.60 = $10.10 > daily_hard $10.00.
    with pytest.raises(DailyBudgetExceeded):
        client.messages_create(
            agent="research",
            model="claude-opus-4-7",
            job_id=job_id,
            messages=[{"role": "user", "content": "x"}],
            max_tokens=4000,
            input_tokens_estimate=100_000,
        )
    assert fake_sdk.calls == []


def test_force_override_lets_call_through(
    fake_sdk, caps: Budgets, db_session, job_id
) -> None:
    state = BudgetState(caps)
    state.commit(9.50)
    guard = JobBudgetGuard(job_id=job_id, cap_usd=5.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk,
        budget_state=state,
        job_guard=guard,
        force_daily_override=True,
    )
    fake_sdk.set_next_response(input_tokens=100, output_tokens=200)
    client.messages_create(
        agent="needs",
        model="claude-haiku-4-5",
        job_id=job_id,
        messages=[{"role": "user", "content": "x"}],
        max_tokens=1000,
        input_tokens_estimate=100,
    )
    assert len(fake_sdk.calls) == 1
