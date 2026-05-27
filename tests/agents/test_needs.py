"""Behaviour tests for :class:`backend.agents.needs.NeedsAgent`.

Mirrors ``tests/agents/test_contact_extractor.py``. Everything runs
through the fake CloudClient — no SDK, no network, no DB writes.
Coverage pins the contract between this agent and the base wrapper:

* ``role`` resolves to ``research_model`` (per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping")
* ``MAX_TOKENS["needs"]`` (= 3000) is the ceiling sent
* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1 / Trap 2 lookups land under the right key
* :meth:`tools` returns ``None`` — needs inference is text-in /
  JSON-out and must NOT request ``web_search`` or ``web_fetch`` (the
  research agent already gathered evidence; this agent reasons)
* user prompt includes the company, the dossier JSON, and (optionally)
  ``user_context``
* the system prompt names the four ``LabMaturity`` enum values verbatim
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. ``lab_maturity = "advanced"``) reaches
  the same retry-then-``AgentOutputInvalid`` path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.limits import MAX_TOKENS
from backend.agents.needs import NeedsAgent
from backend.agents.needs_models import (
    Confidence,
    LabMaturity,
    NeedsAssessment,
)
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimum_assessment_payload() -> dict[str, Any]:
    """Smallest dict that validates as ``NeedsAssessment``. Used by
    wiring tests that don't care about the assessment's content."""
    return {
        "company_name": "Acme Ltd",
        "lab_maturity": "mature",
        "lab_maturity_reasoning": (
            "Named CTO, stable vendor footprint, no transformation "
            "programme visible."
        ),
        "lab_maturity_evidence": [],
        "lab_maturity_confidence": "MEDIUM",
        "needs": [],
        "gaps": [],
    }


def _populated_assessment_payload() -> dict[str, Any]:
    return {
        "company_name": "Acme Ltd",
        "lab_maturity": "modernisation_in_progress",
        "lab_maturity_reasoning": (
            "Active SD-WAN job postings dated within 90 days plus a "
            "recent vendor case study on zero-trust rollout."
        ),
        "lab_maturity_evidence": [
            {
                "summary": "Job posting for Head of SD-WAN dated 2026-04-01.",
                "source_url": "https://acme.example.com/jobs/sdwan",
                "source_title": "Head of SD-WAN — Acme Ltd",
                "retrieved_at": "2026-05-27",
                "confidence": "HIGH",
            }
        ],
        "lab_maturity_confidence": "HIGH",
        "needs": [
            {
                "summary": "Accelerate SD-WAN rollout",
                "detail": "Public job postings and vendor materials "
                "suggest ANS could de-risk an in-flight programme.",
                "priority": 1,
                "evidence": [
                    {
                        "summary": "Job posting for Head of SD-WAN.",
                        "source_url": "https://acme.example.com/jobs/sdwan",
                        "source_title": "Head of SD-WAN — Acme Ltd",
                        "retrieved_at": "2026-05-27",
                        "confidence": "HIGH",
                    }
                ],
                "confidence": "HIGH",
            }
        ],
        "gaps": [],
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_assessment_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _dossier_blob() -> str:
    """A small string that stands in for the research dossier JSON. The
    agent treats it as opaque text — the test only needs to assert it
    lands in the user-prompt body verbatim."""
    return '{"company_name":"Acme Ltd","retrieved_at":"2026-05-27"}'


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling
# ---------------------------------------------------------------------------

def test_agent_name_is_needs(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert fake_cloud_client.calls[0]["agent"] == "needs"


def test_role_resolves_to_research_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_max_tokens_matches_needs_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["needs"]
        == 3000
    )


# ---------------------------------------------------------------------------
# 2. No tools — needs inference is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None``
    and must NOT allocate a ``tool_use_counts`` dict — that's how the
    base contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    call = fake_cloud_client.calls[0]
    assert call["tools"] is None
    assert call["tool_use_counts"] is None


# ---------------------------------------------------------------------------
# 3. Prompt rendering
# ---------------------------------------------------------------------------

def test_user_prompt_contains_company_name_url_and_dossier(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Acme Ltd" in content
    assert "https://acme.example.com/" in content
    assert _dossier_blob() in content


def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
        user_context="focus on the SD-WAN angle",
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "focus on the SD-WAN angle" in content


def test_system_prompt_carries_lab_maturity_values(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The handover §9.3 four-value enum is load-bearing. Pin the
    lower-case strings in the system prompt so a future edit cannot
    silently rename one (which would silently break Stage 2 writers)."""
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    for needle in (
        "none_visible",
        "basic",
        "mature",
        "modernisation_in_progress",
    ):
        assert needle in system, (
            f"expected the system prompt to mention {needle!r}"
        )


def test_system_prompt_carries_reasoning_only_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The handover specifies "no tools" — the agent reasons over the
    dossier, never the web. Pin the constraint in the prompt so a
    future edit cannot silently add a tool-use hint."""
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "no web-search or web-fetch" in system.lower()
    assert "reasoning" in system.lower()


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_assessment(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_assessment_payload())])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, NeedsAssessment)
    assert result.company_name == "Acme Ltd"
    assert result.lab_maturity is LabMaturity.MODERNISATION_IN_PROGRESS
    assert result.lab_maturity_confidence is Confidence.HIGH
    assert len(result.needs) == 1
    assert result.needs[0].priority == 1


def test_empty_assessment_is_a_legal_outcome(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A dossier that supports a NONE_VISIBLE call with no evidence
    is a valid result — the agent must not raise or retry just because
    ``needs`` and ``lab_maturity_evidence`` are empty."""
    payload = dict(_minimum_assessment_payload())
    payload["lab_maturity"] = "none_visible"
    fake_cloud_client.script([_ok_response(payload)])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, NeedsAssessment)
    assert result.lab_maturity is LabMaturity.NONE_VISIBLE
    assert result.lab_maturity_evidence == []
    assert result.needs == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, NeedsAssessment)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert exc_info.value.agent == "needs"
    assert exc_info.value.model == test_models.research_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="needs",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(
            job_id="job-trap",
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_unknown_lab_maturity_value_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response that's valid JSON but breaks the schema (lab_maturity
    outside the frozen four-value set) must hit the same
    retry-then-AgentOutputInvalid path as a plain parse failure. Pin
    this so the LabMaturity enum is actually reachable as a backstop
    end-to-end."""
    bad_payload = dict(_minimum_assessment_payload())
    bad_payload["lab_maturity"] = "advanced"  # not in the enum
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = NeedsAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert len(fake_cloud_client.calls) == 2
