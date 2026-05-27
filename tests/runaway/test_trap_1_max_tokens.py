"""Trap 1 — per-call max_tokens ceiling.

The wrapper must:
  * raise :class:`MaxTokensExceeded` if a caller requests more than
    the per-agent ceiling;
  * raise :class:`UnknownAgent` for any agent not in
    ``backend.agents.limits.MAX_TOKENS`` (no default fallback);
  * refuse non-positive ``max_tokens``;
  * fail **before** the SDK is touched in every case.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from backend.agents.limits import MAX_TOKENS
from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetGuard,
    MaxTokensExceeded,
    UnknownAgent,
)


def _build_client(
    sdk, budget_state: BudgetState, db: Session, job_id: str
) -> CloudClient:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db)
    return CloudClient(
        sdk_client=sdk,
        budget_state=budget_state,
        job_guard=guard,
    )


def test_request_above_ceiling_raises_and_sdk_untouched(
    fake_sdk, budget_state, db_session, job_id
):
    client = _build_client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    with pytest.raises(MaxTokensExceeded):
        client.messages_create(
            agent="research",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=MAX_TOKENS["research"] + 1,  # one above ceiling
            input_tokens_estimate=10,
        )

    assert fake_sdk.calls == [], "SDK must not be called when Trap 1 fires"


def test_request_at_ceiling_is_allowed(
    fake_sdk, budget_state, db_session, job_id
):
    client = _build_client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    result = client.messages_create(
        agent="critic",
        model="claude-haiku-4-5",
        job_id=job_id,
        messages=[{"role": "user", "content": "review"}],
        max_tokens=MAX_TOKENS["critic"],
        input_tokens_estimate=10,
    )

    assert len(fake_sdk.calls) == 1
    assert fake_sdk.calls[0]["max_tokens"] == MAX_TOKENS["critic"]
    assert result.output_tokens == 10


def test_unknown_agent_raises_and_sdk_untouched(
    fake_sdk, budget_state, db_session, job_id
):
    client = _build_client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    with pytest.raises(UnknownAgent):
        client.messages_create(
            agent="not-a-real-agent",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1000,
            input_tokens_estimate=10,
        )

    assert fake_sdk.calls == []


def test_non_positive_max_tokens_raises(
    fake_sdk, budget_state, db_session, job_id
):
    client = _build_client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    with pytest.raises(MaxTokensExceeded):
        client.messages_create(
            agent="needs",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=0,
            input_tokens_estimate=10,
        )

    assert fake_sdk.calls == []


def test_every_agent_has_an_explicit_ceiling() -> None:
    """No agent name is silently allowed via a default — by design.

    The wrapper looks up ``MAX_TOKENS[agent]`` directly; this test
    pins the set of registered agents so an accidental rename to a
    new name (without adding it to the dict) gets caught here too.
    """
    expected = {
        "research",
        "needs",
        "briefing_compiler",
        "mapping",
        "benefits_writer",
        "faq_writer",
        "objections_writer",
        "critic",
    }
    assert set(MAX_TOKENS.keys()) == expected
