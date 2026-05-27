"""Trap 2 — tool-call ceilings.

Research agent: ``web_search`` ≤ 20, ``web_fetch`` ≤ 10.
Every other agent: ≤ 3 calls per tool.

The wrapper checks the running totals supplied by the caller before
*and* after each SDK call. The post-call check matters because the
SDK response is what tells us how many ``tool_use`` blocks the model
actually emitted.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetGuard,
    ToolCallLimitExceeded,
)


def _client(sdk, budget_state: BudgetState, db: Session, job_id: str) -> CloudClient:
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db)
    return CloudClient(
        sdk_client=sdk, budget_state=budget_state, job_guard=guard
    )


def test_research_web_search_cap_blocks_at_21(
    fake_sdk, budget_state, db_session, job_id
):
    """Pre-call count of 21 web_search uses must trip before the SDK runs."""
    client = _client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    counts = {"web_search": 21}
    with pytest.raises(ToolCallLimitExceeded):
        client.messages_create(
            agent="research",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "go"}],
            max_tokens=1000,
            input_tokens_estimate=10,
            tool_use_counts=counts,
        )
    assert fake_sdk.calls == []


def test_research_web_search_at_20_allowed(
    fake_sdk, budget_state, db_session, job_id
):
    client = _client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    counts = {"web_search": 20}
    client.messages_create(
        agent="research",
        model="claude-sonnet-4-6",
        job_id=job_id,
        messages=[{"role": "user", "content": "go"}],
        max_tokens=1000,
        input_tokens_estimate=10,
        tool_use_counts=counts,
    )
    assert len(fake_sdk.calls) == 1


def test_research_web_fetch_cap_blocks_at_11(
    fake_sdk, budget_state, db_session, job_id
):
    client = _client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    with pytest.raises(ToolCallLimitExceeded):
        client.messages_create(
            agent="research",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "go"}],
            max_tokens=1000,
            input_tokens_estimate=10,
            tool_use_counts={"web_fetch": 11},
        )
    assert fake_sdk.calls == []


def test_non_research_agent_capped_at_three(
    fake_sdk, budget_state, db_session, job_id
):
    """A non-research agent gets the default ceiling of 3 per tool."""
    client = _client(fake_sdk, budget_state, db_session, job_id)
    fake_sdk.set_next_response(input_tokens=10, output_tokens=10)

    with pytest.raises(ToolCallLimitExceeded):
        client.messages_create(
            agent="needs",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "go"}],
            max_tokens=1000,
            input_tokens_estimate=10,
            tool_use_counts={"some_tool": 4},  # > DEFAULT_TOOL_CALL_LIMIT
        )
    assert fake_sdk.calls == []


def test_post_call_counts_response_tool_use_blocks(
    fake_sdk, budget_state, db_session, job_id
):
    """A response that pushes the total over the cap raises after the call."""
    client = _client(fake_sdk, budget_state, db_session, job_id)
    # Pre-call total is 3 (already at cap for non-research default).
    # The response includes one more tool_use block, bringing it to 4 → trap.
    fake_sdk.set_next_response(
        input_tokens=10, output_tokens=10, tool_uses=["some_tool"]
    )

    counts = {"some_tool": 3}
    with pytest.raises(ToolCallLimitExceeded):
        client.messages_create(
            agent="mapping",
            model="claude-sonnet-4-6",
            job_id=job_id,
            messages=[{"role": "user", "content": "go"}],
            max_tokens=1000,
            input_tokens_estimate=10,
            tool_use_counts=counts,
        )
    # The SDK was called once (pre-check passed at 3 ≤ 3); the post-call
    # increment to 4 is what raises.
    assert len(fake_sdk.calls) == 1
    assert counts["some_tool"] == 4
