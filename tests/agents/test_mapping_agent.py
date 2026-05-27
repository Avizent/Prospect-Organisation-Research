"""Behaviour tests for :class:`backend.agents.mapping.ProductMappingAgent`.

Mirrors ``tests/agents/test_needs.py``. Everything runs through the
fake :class:`FakeCloudClient` — no real SDK, no network, no DB,
no filesystem reads of ANS knowledge or briefing artefacts. Coverage
pins the contract between this agent and the base wrapper:

* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1 / Trap 2 lookups land under the right key
* ``role`` resolves to ``research_model`` (per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping")
* ``MAX_TOKENS["mapping"]`` (= 2000) is the ceiling sent
* :meth:`tools` returns ``None`` — product mapping is text-in /
  JSON-out and must NOT request ``web_search`` or ``web_fetch``
* user prompt includes the company, the briefing JSON verbatim, the
  knowledge bundle verbatim, and (optionally) ``user_context``
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. ``product_line = "firewalls"``)
  reaches the same retry-then-``AgentOutputInvalid`` path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.limits import MAX_TOKENS
from backend.agents.mapping import ProductMappingAgent
from backend.agents.mapping_models import (
    Confidence,
    ProductLine,
    ProductMapping,
)
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimum_mapping_payload() -> dict[str, Any]:
    """Smallest dict that validates as ``ProductMapping``. Used by
    wiring tests that don't care about the mapping's content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "mapped_at": "2026-05-27",
    }


def _populated_mapping_payload() -> dict[str, Any]:
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "mapped_at": "2026-05-27",
        "matches": [
            {
                "need_priority": 1,
                "need_summary": "De-risk SD-WAN rollout",
                "products": [
                    {
                        "product_line": "emulators",
                        "product_name": "Netropy 100G",
                        "knowledge_excerpt_refs": [0],
                    }
                ],
                "use_case_framing": (
                    "Lab-emulate planned topology before cutover."
                ),
                "why_this_fits": (
                    "Briefing flags an active SD-WAN programme."
                ),
                "confidence": "HIGH",
            }
        ],
        "unmatched_needs": [],
        "knowledge_excerpts": [
            {
                "source_file": "products/emulators.md",
                "heading": "Netropy Network Emulators",
                "rationale": "Datasheet for SD-WAN-grade emulator.",
            }
        ],
        "gaps": [],
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_mapping_payload())
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


def _knowledge_blob() -> str:
    """A small string that stands in for the ANS knowledge bundle.
    The agent treats it as opaque text — the test only needs to assert
    it lands in the user-prompt body verbatim."""
    return (
        "# ANS Knowledge Bundle\n\n"
        "## Netropy Network Emulators\nBandwidth up to 100 Gbps.\n\n"
        "## SD-WAN lab validation\nPrior case study summary.\n"
    )


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling
# ---------------------------------------------------------------------------

def test_agent_name_is_mapping(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert fake_cloud_client.calls[0]["agent"] == "mapping"


def test_role_resolves_to_research_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_max_tokens_matches_mapping_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["mapping"]
        == 2000
    )


# ---------------------------------------------------------------------------
# 2. No tools — mapping is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None``
    and must NOT allocate a ``tool_use_counts`` dict — that's how the
    base contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    call = fake_cloud_client.calls[0]
    assert call["tools"] is None
    assert call["tool_use_counts"] is None


# ---------------------------------------------------------------------------
# 3. Prompt rendering
# ---------------------------------------------------------------------------

def test_user_prompt_contains_company_briefing_and_knowledge_verbatim(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Acme Ltd" in content
    assert "https://acme.example.com/" in content
    assert _briefing_blob() in content
    assert _knowledge_blob() in content


def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
        user_context="emphasise SD-WAN angle",
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "emphasise SD-WAN angle" in content


def test_system_prompt_names_the_two_product_lines(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The closed-enum self-fence relies on the model knowing the two
    product line names. Pin them in the prompt so a future edit cannot
    silently drop one (which would silently break the writer pipeline)."""
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "emulators" in system
    assert "traffic_generators" in system


def test_system_prompt_carries_reasoning_only_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The handover specifies "no tools" — the agent reasons over the
    briefing and knowledge bundle, never the web."""
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "approved briefing" in system.lower()
    assert "knowledge" in system.lower()
    assert "no commentary" in system.lower()


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_mapping(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_mapping_payload())])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert isinstance(result, ProductMapping)
    assert result.company_name == "Acme Ltd"
    assert len(result.matches) == 1
    assert result.matches[0].need_priority == 1
    assert result.matches[0].confidence is Confidence.HIGH
    assert result.matches[0].products[0].product_line is ProductLine.EMULATORS


def test_empty_mapping_is_a_legal_outcome(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A briefing with no ranked needs yields an empty matches list.
    The agent must not raise or retry just because ``matches`` is
    empty."""
    fake_cloud_client.script([_ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert isinstance(result, ProductMapping)
    assert result.matches == []
    assert result.unmatched_needs == []
    assert result.knowledge_excerpts == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        briefing_json=_briefing_blob(),
        knowledge_bundle=_knowledge_blob(),
    )

    assert isinstance(result, ProductMapping)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            briefing_json=_briefing_blob(),
            knowledge_bundle=_knowledge_blob(),
        )

    assert exc_info.value.agent == "mapping"
    assert exc_info.value.model == test_models.research_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="mapping",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(
            job_id="job-trap",
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            briefing_json=_briefing_blob(),
            knowledge_bundle=_knowledge_blob(),
        )

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_unknown_product_line_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response whose ``product_line`` is outside the closed enum
    must hit the same retry-then-AgentOutputInvalid path as a plain
    parse failure. Pin this so ProductLine is actually reachable as a
    backstop end-to-end."""
    bad_payload = _populated_mapping_payload()
    bad_payload["matches"][0]["products"][0]["product_line"] = "firewalls"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            briefing_json=_briefing_blob(),
            knowledge_bundle=_knowledge_blob(),
        )

    assert len(fake_cloud_client.calls) == 2


def test_out_of_range_excerpt_ref_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The model-level range validator on ``knowledge_excerpt_refs``
    must reach the same retry-then-AgentOutputInvalid path."""
    bad_payload = _populated_mapping_payload()
    # knowledge_excerpts has length 1, so index 5 is out of range.
    bad_payload["matches"][0]["products"][0]["knowledge_excerpt_refs"] = [5]
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            briefing_json=_briefing_blob(),
            knowledge_bundle=_knowledge_blob(),
        )

    assert len(fake_cloud_client.calls) == 2


def test_extra_field_response_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``extra="forbid"`` is the schema's self-fence against silent
    drift. Prove that an unknown field at the top level reaches the
    retry path end-to-end."""
    bad_payload = _populated_mapping_payload()
    bad_payload["surprise_field"] = "nope"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ProductMappingAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            briefing_json=_briefing_blob(),
            knowledge_bundle=_knowledge_blob(),
        )

    assert len(fake_cloud_client.calls) == 2
