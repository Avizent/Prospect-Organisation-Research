"""Behaviour tests for :class:`backend.agents.base.BaseAgent`.

Every test exercises a single dummy subclass against the
:class:`FakeCloudClient` from ``conftest.py``. No real CloudClient
construction. No SDK import. No network.

Coverage targets, mapped to the Step 6a brief:

* injection — agent receives a CloudClient-like object and uses it
* model lookup honours ``role`` via the injected :class:`Models`
* trap exceptions propagate unchanged
* output validation retries once and then fails cleanly
* prompts and response text never appear in logs
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from pydantic import BaseModel, Field

from backend.agents.base import BaseAgent
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Dummy agent + output model
# ---------------------------------------------------------------------------

class _DummyOutput(BaseModel):
    """Minimal validated payload — one required string field."""

    value: str = Field(min_length=1)


_SECRET_PROMPT_MARKER = "BRIEF_PROMPT_PAYLOAD_42"
_SECRET_RESPONSE_MARKER = "BRIEF_RESPONSE_PAYLOAD_99"


class _DummyAgent(BaseAgent):
    """Concrete subclass used solely by these tests.

    ``name`` is ``"needs"`` so it lines up with a real entry in
    ``MAX_TOKENS`` (the cloud client does that lookup; the fake doesn't,
    but using a real name keeps the wrapper honest if a future refactor
    ever validates the name early). ``role="research_model"`` lets the
    role→model test prove the right attribute is read.
    """

    name = "needs"
    role = "research_model"
    output_model = _DummyOutput

    def system_prompt(self) -> str:
        return "You are a test agent. Respond with strict JSON."

    def user_prompt(self, **inputs: Any) -> str:
        # Marker lets the leakage test assert it does not appear in logs.
        return f"Hello {_SECRET_PROMPT_MARKER}: please return value=ok."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response() -> Any:
    text = json.dumps({"value": _SECRET_RESPONSE_MARKER})
    return make_response(text=text)


def _bad_json_response() -> Any:
    return make_response(text="this is not json at all")


def _wrong_shape_response() -> Any:
    # Valid JSON, fails Pydantic (empty string violates min_length=1).
    return make_response(text=json.dumps({"value": ""}))


# ---------------------------------------------------------------------------
# 1. Injection + clean call
# ---------------------------------------------------------------------------

def test_run_returns_parsed_pydantic_instance(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(job_id="job-abc")

    assert isinstance(result, _DummyOutput)
    assert result.value == _SECRET_RESPONSE_MARKER
    assert len(fake_cloud_client.calls) == 1


def test_run_forwards_required_kwargs_to_cloud_client(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)
    agent.run(job_id="job-xyz")

    call = fake_cloud_client.calls[0]
    assert call["agent"] == "needs"
    assert call["job_id"] == "job-xyz"
    # max_tokens must come from the per-agent ceiling, never a default.
    from backend.agents.limits import MAX_TOKENS

    assert call["max_tokens"] == MAX_TOKENS["needs"]
    assert isinstance(call["messages"], list) and len(call["messages"]) == 1
    assert call["messages"][0]["role"] == "user"
    assert call["input_tokens_estimate"] >= 1


def test_run_accepts_none_job_id_for_jobless_probes(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(job_id=None)

    assert isinstance(result, _DummyOutput)
    assert fake_cloud_client.calls[0]["job_id"] is None


# ---------------------------------------------------------------------------
# 2. Role → model lookup
# ---------------------------------------------------------------------------

def test_role_selects_correct_model_from_models_dataclass(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)
    agent.run(job_id=None)

    # _DummyAgent.role == "research_model"
    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_changing_role_changes_model_lookup(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A subclass with role='writer_model' must pick writer_model."""

    class _WriterDummy(_DummyAgent):
        role = "writer_model"

    fake_cloud_client.script([_ok_response()])
    agent = _WriterDummy(client=fake_cloud_client, models=test_models)
    agent.run(job_id=None)

    assert fake_cloud_client.calls[0]["model"] == test_models.writer_model


# ---------------------------------------------------------------------------
# 3. Trap propagation
# ---------------------------------------------------------------------------

def test_runaway_trap_exception_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A trap raised by the cloud client must surface as-is — no
    swallowing, no wrapping, no retry."""
    trap = JobBudgetExceeded(
        "synthetic trap for test",
        job_id="job-trap",
        agent="needs",
        context={"why": "test"},
    )
    fake_cloud_client.script([trap])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(job_id="job-trap")

    # Identity check — same exception object, not a wrapped one.
    assert exc_info.value is trap
    # Only one call should have been made (no retry of trap exceptions).
    assert len(fake_cloud_client.calls) == 1


# ---------------------------------------------------------------------------
# 4. Output validation retry
# ---------------------------------------------------------------------------

def test_json_parse_failure_triggers_one_retry_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(job_id="job-1")

    assert isinstance(result, _DummyOutput)
    assert len(fake_cloud_client.calls) == 2
    # The retry must carry a stricter reminder appended to the user content.
    second_content = fake_cloud_client.calls[1]["messages"][0]["content"]
    assert "Return ONLY a valid JSON" in second_content


def test_two_consecutive_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(job_id="job-2")

    assert exc_info.value.attempt == 2
    assert exc_info.value.agent == "needs"
    assert exc_info.value.model == test_models.research_model
    # And it is not a RunawayTrapFired — orchestrator handles it separately.
    from backend.cost_control.exceptions import RunawayTrapFired

    assert not isinstance(exc_info.value, RunawayTrapFired)


def test_pydantic_validation_failure_retries_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_wrong_shape_response(), _ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(job_id=None)

    assert isinstance(result, _DummyOutput)
    assert len(fake_cloud_client.calls) == 2


def test_two_pydantic_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_wrong_shape_response(), _wrong_shape_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(job_id=None)

    assert exc_info.value.attempt == 2
    # The Pydantic errors list is populated (locations + messages, no
    # response text).
    assert len(exc_info.value.validation_errors) >= 1


# ---------------------------------------------------------------------------
# 5. Log-leakage guard
# ---------------------------------------------------------------------------

def test_prompts_and_responses_are_never_logged(
    fake_cloud_client: FakeCloudClient,
    test_models,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The base agent must log structure (agent, model, attempt) but
    must NOT log prompt content or response content. We capture at
    DEBUG so even debug-level slip-ups would be caught."""
    fake_cloud_client.script([_ok_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    with caplog.at_level(logging.DEBUG, logger="backend.agents.base"):
        agent.run(job_id="job-leak")

    blob = "\n".join(record.getMessage() for record in caplog.records)
    # Plus the full structured record dict, just in case.
    blob += "\n" + "\n".join(str(record.__dict__) for record in caplog.records)

    assert _SECRET_PROMPT_MARKER not in blob, (
        "user prompt content leaked into logs — wrapper must log "
        "structure only, never the prompt body"
    )
    assert _SECRET_RESPONSE_MARKER not in blob, (
        "response text leaked into logs — wrapper must not log the "
        "decoded JSON or response content"
    )


def test_log_leakage_guard_also_holds_when_output_invalid(
    fake_cloud_client: FakeCloudClient,
    test_models,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even on a parse/validate failure, the failing text must not be
    logged — the error path is the most tempting place to slip up."""
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = _DummyAgent(client=fake_cloud_client, models=test_models)

    with caplog.at_level(logging.DEBUG, logger="backend.agents.base"):
        with pytest.raises(AgentOutputInvalid):
            agent.run(job_id="job-leak-2")

    blob = "\n".join(record.getMessage() for record in caplog.records)
    blob += "\n" + "\n".join(str(record.__dict__) for record in caplog.records)

    # The bad response text was literally "this is not json at all".
    assert "this is not json at all" not in blob
    assert _SECRET_PROMPT_MARKER not in blob
