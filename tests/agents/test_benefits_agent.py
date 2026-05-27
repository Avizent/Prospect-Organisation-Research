"""Behaviour tests for :class:`backend.agents.benefits.BenefitsWriter`.

Mirrors ``tests/agents/test_mapping_agent.py``. Everything runs
through the fake :class:`FakeCloudClient` — no real SDK, no network,
no DB, no filesystem reads of ANS knowledge, briefing or mapping
artefacts. Coverage pins the contract between this agent and the
base wrapper:

* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1 / Trap 2 lookups land under ``benefits_writer`` — the
  pre-existing registry key in :mod:`backend.agents.limits`
* ``role`` resolves to ``writer_model`` (distinct from the research
  model used by Stage 1 + the mapping agent)
* ``MAX_TOKENS["benefits_writer"]`` (= 6000) is the ceiling sent
* :meth:`tools` returns ``None`` — the writer is text-in / JSON-out
  and must NOT request ``web_search`` or ``web_fetch``
* user prompt includes the company, the briefing JSON verbatim, the
  product mapping JSON verbatim, the knowledge bundle verbatim, and
  (optionally) ``user_context``
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. mis-tiered ``audience``) reaches the
  same retry-then-``AgentOutputInvalid`` path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.benefits import BenefitsWriter
from backend.agents.benefits_models import Audience, BenefitsBrief, Confidence
from backend.agents.limits import MAX_TOKENS
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claim_payload(
    *,
    knowledge_excerpt_refs: list[int] | None = None,
    briefing_source_refs: list[int] | None = None,
    confidence: str = "HIGH",
) -> dict[str, Any]:
    return {
        "claim": "Reduce SD-WAN cutover risk",
        "detail": (
            "Emulate the planned topology in the lab with realistic "
            "latency, loss and jitter before any production change."
        ),
        "why_it_matters": (
            "Catches design errors before they hit production."
        ),
        "knowledge_excerpt_refs": (
            knowledge_excerpt_refs if knowledge_excerpt_refs is not None
            else [0]
        ),
        "briefing_source_refs": (
            briefing_source_refs if briefing_source_refs is not None
            else [0]
        ),
        "confidence": confidence,
    }


def _section_payload(
    *,
    heading: str,
    audience: str,
    summary: str,
    body: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "heading": heading,
        "audience": audience,
        "summary": summary,
        "body": body if body is not None else [_claim_payload()],
    }


def _minimum_brief_payload() -> dict[str, Any]:
    """Smallest dict that validates as :class:`BenefitsBrief`. Used by
    wiring tests that don't care about the brief's content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "executive_summary": _section_payload(
            heading="Strategic summary",
            audience="CTO_CIO",
            summary="Three lab-validated wins de-risk the programme.",
        ),
        "technical_fit": _section_payload(
            heading="Engineering fit",
            audience="ENGINEERS",
            summary="Netropy 100G covers bandwidth and impairment ranges.",
        ),
        "business_case": _section_payload(
            heading="Spend justification",
            audience="IT_DIRECTOR",
            summary="Lab cost is a small fraction of a rollback's cost.",
        ),
    }


def _populated_brief_payload() -> dict[str, Any]:
    payload = _minimum_brief_payload()
    payload["gaps"] = ["No datasheet detail on Netropy 100G jitter floor."]
    return payload


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_brief_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _briefing_blob() -> str:
    """A small string that stands in for the approved briefing JSON.
    The agent treats it as opaque text — the test only needs to assert
    it lands in the user-prompt body verbatim."""
    return (
        '{"company_name":"Acme Ltd",'
        '"company_url":"https://acme.example.com/",'
        '"compiled_at":"2026-05-27"}'
    )


def _mapping_blob() -> str:
    """A small string that stands in for the upstream product mapping
    JSON. Opaque blob — only verbatim presence in the prompt matters."""
    return (
        '{"company_name":"Acme Ltd",'
        '"mapped_at":"2026-05-27",'
        '"matches":[{"need_priority":1}]}'
    )


def _knowledge_blob() -> str:
    """A small string that stands in for the ANS knowledge bundle."""
    return (
        "# ANS Knowledge Bundle\n\n"
        "## Netropy Network Emulators\nBandwidth up to 100 Gbps.\n\n"
        "## SD-WAN lab validation\nPrior case study summary.\n"
    )


def _run_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": None,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "briefing_json": _briefing_blob(),
        "product_mapping_json": _mapping_blob(),
        "knowledge_bundle": _knowledge_blob(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling
# ---------------------------------------------------------------------------

def test_agent_name_is_benefits_writer(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``name`` is what Trap 1 / Trap 2 / cost-control look up. It
    must equal the existing registry key so the writer doesn't
    silently fall outside the runaway traps."""
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["agent"] == "benefits_writer"


def test_role_resolves_to_writer_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The three Stage 2 writers share the writer model id, distinct
    from the research model used by Stage 1 and the mapping agent."""
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["model"] == test_models.writer_model


def test_max_tokens_matches_benefits_writer_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["benefits_writer"]
        == 6000
    )


# ---------------------------------------------------------------------------
# 2. No tools — writer is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None``
    and must NOT allocate a ``tool_use_counts`` dict — that's how the
    base contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    call = fake_cloud_client.calls[0]
    assert call["tools"] is None
    assert call["tool_use_counts"] is None


# ---------------------------------------------------------------------------
# 3. Prompt rendering — verbatim threading of every input
# ---------------------------------------------------------------------------

def test_user_prompt_contains_company_briefing_mapping_and_knowledge_verbatim(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Acme Ltd" in content
    assert "https://acme.example.com/" in content
    assert _briefing_blob() in content
    assert _mapping_blob() in content
    assert _knowledge_blob() in content


def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs(user_context="emphasise risk reduction angle"))

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "emphasise risk reduction angle" in content


def test_system_prompt_names_three_audience_tiers(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The closed-enum self-fence on Audience relies on the model
    knowing the three tier names. Pin them in the system prompt so a
    future edit cannot silently drop one."""
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "CTO_CIO" in system
    assert "ENGINEERS" in system
    assert "IT_DIRECTOR" in system


def test_system_prompt_carries_grounding_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The brief must stay grounded in approved inputs — pin the
    no-fabrication language in the prompt so future edits cannot
    weaken it without a visible test diff."""
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "approved briefing" in system.lower()
    assert "knowledge" in system.lower()
    assert "no commentary" in system.lower()


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_brief(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_brief_payload())])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, BenefitsBrief)
    assert result.company_name == "Acme Ltd"
    assert result.executive_summary.audience is Audience.CTO_CIO
    assert result.technical_fit.audience is Audience.ENGINEERS
    assert result.business_case.audience is Audience.IT_DIRECTOR
    assert result.executive_summary.body[0].confidence is Confidence.HIGH


def test_minimum_brief_validates_and_returns(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A brief with one claim per section and no gaps is a legal
    minimum-shape outcome — the agent must not retry just because
    ``gaps`` is empty."""
    fake_cloud_client.script([_ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, BenefitsBrief)
    assert result.gaps == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, BenefitsBrief)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(**_run_kwargs())

    assert exc_info.value.agent == "benefits_writer"
    assert exc_info.value.model == test_models.writer_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="benefits_writer",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(**_run_kwargs(job_id="job-trap"))

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_mis_tiered_audience_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response whose section→audience pinning is broken must hit
    the same retry-then-AgentOutputInvalid path as a plain parse
    failure. Pin that the model-level audience validator is actually
    reachable as a backstop end-to-end."""
    bad_payload = _populated_brief_payload()
    bad_payload["executive_summary"]["audience"] = "ENGINEERS"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_invalid_audience_value_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """An audience value outside the closed enum (e.g. ``CFO``) hits
    the retry path — proves the enum self-fence is reachable end-to-
    end, not just at the schema level."""
    bad_payload = _populated_brief_payload()
    bad_payload["executive_summary"]["audience"] = "CFO"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_negative_ref_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The non-negativity validator on refs must reach the same retry-
    then-AgentOutputInvalid path."""
    bad_payload = _populated_brief_payload()
    bad_payload["technical_fit"]["body"][0]["knowledge_excerpt_refs"] = [-1]
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_extra_field_response_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``extra="forbid"`` is the schema's self-fence against silent
    drift. Prove that an unknown field at the top level reaches the
    retry path end-to-end."""
    bad_payload = _populated_brief_payload()
    bad_payload["surprise_field"] = "nope"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_empty_section_body_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A section with zero claims is editorial noise — the
    ``min_length=1`` on ``body`` must reach the retry path so the
    writer cannot accidentally ship one."""
    bad_payload = _populated_brief_payload()
    bad_payload["business_case"]["body"] = []
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BenefitsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2
