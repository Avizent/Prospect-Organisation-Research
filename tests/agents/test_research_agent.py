"""Behaviour tests for :class:`backend.agents.research.ResearchAgent`.

Everything is fake-CloudClient driven (see ``conftest.py``). No real
SDK import, no network. Coverage focuses on the *contract* between
the research agent and the wrapper:

* tools metadata is forwarded to ``messages_create``
* ``max_uses`` values come from :data:`TOOL_CALL_LIMITS` (single source
  of truth — the dossier and the runaway traps must agree)
* ``tool_use_counts`` is initialised once and threaded through both
  attempts (so Trap 2 accounting persists across the parse-retry)
* ``role`` resolves to ``research_model``
* ``MAX_TOKENS["research"]`` (= 4000) is the ceiling sent
* user_context is rendered into the user prompt when provided
* parse-retry behaviour from Step 6a still applies
* :class:`RunawayTrapFired` propagates unchanged (no swallowing)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.limits import MAX_TOKENS, TOOL_CALL_LIMITS
from backend.agents.output import AgentOutputInvalid
from backend.agents.research import ResearchAgent
from backend.agents.research_models import ResearchDossier
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimum_dossier_payload() -> dict[str, Any]:
    """Smallest dict that validates as ResearchDossier — empty sections,
    no findings, no gaps. Lets the wrapper-contract tests focus on the
    plumbing rather than dossier content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "retrieved_at": "2026-05-27",
    }


def _ok_response() -> Any:
    return make_response(text=json.dumps(_minimum_dossier_payload()))


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


# ---------------------------------------------------------------------------
# 1. Tool metadata is forwarded
# ---------------------------------------------------------------------------

def test_tools_metadata_is_forwarded_to_messages_create(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id="job-1",
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    call = fake_cloud_client.calls[0]
    tools = call["tools"]
    assert isinstance(tools, list) and len(tools) == 2
    names = sorted(tool["name"] for tool in tools)
    assert names == ["web_fetch", "web_search"]


def test_max_uses_values_come_from_TOOL_CALL_LIMITS(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``TOOL_CALL_LIMITS`` is the runaway-trap source of truth. Drift
    between the dossier and the cloud-client trap is a silent runaway
    risk — pin both to the same constant."""
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    tools = fake_cloud_client.calls[0]["tools"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["web_search"]["max_uses"] == TOOL_CALL_LIMITS["research"]["web_search"]
    assert by_name["web_fetch"]["max_uses"] == TOOL_CALL_LIMITS["research"]["web_fetch"]


def test_tools_method_returns_copies_not_shared_state(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If the SDK rewrites tool dicts in place (a real concern at Step
    18), the class-level constant must not be mutated."""
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)
    first = agent.tools()
    second = agent.tools()

    assert first == second
    assert first is not second
    assert first[0] is not second[0]


def test_tool_use_counts_dict_is_passed(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The wrapper must allocate the counts dict whenever ``tools`` is
    not None — that is what enables Trap 2 inside the cloud client."""
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    call = fake_cloud_client.calls[0]
    assert call["tool_use_counts"] == {}
    assert isinstance(call["tool_use_counts"], dict)


def test_tool_use_counts_is_shared_across_retry_attempts(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The same dict object must be passed on both attempts — otherwise
    a retry would silently reset Trap 2's per-job accounting."""
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id="job-retry",
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    first_counts = fake_cloud_client.calls[0]["tool_use_counts"]
    second_counts = fake_cloud_client.calls[1]["tool_use_counts"]
    assert first_counts is second_counts


# ---------------------------------------------------------------------------
# 2. Role / model / token ceiling wiring
# ---------------------------------------------------------------------------

def test_research_role_resolves_to_research_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_max_tokens_matches_research_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    assert fake_cloud_client.calls[0]["max_tokens"] == MAX_TOKENS["research"] == 4000


def test_agent_name_is_research(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    assert fake_cloud_client.calls[0]["agent"] == "research"


# ---------------------------------------------------------------------------
# 3. User-context rendering
# ---------------------------------------------------------------------------

def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        user_context="focus on the recent network outage in March",
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "focus on the recent network outage in March" in content


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_dossier(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    assert isinstance(result, ResearchDossier)
    assert result.company_name == "Acme Ltd"


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
    )

    assert isinstance(result, ResearchDossier)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
        )

    assert exc_info.value.agent == "research"
    assert exc_info.value.model == test_models.research_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="research",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = ResearchAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(
            job_id="job-trap",
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
        )

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1
