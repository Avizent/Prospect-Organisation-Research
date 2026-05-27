"""Behaviour tests for :class:`backend.agents.briefing_compiler.BriefingCompiler`.

Mirrors ``tests/agents/test_needs.py``. Everything runs through the
fake CloudClient — no SDK, no network, no DB writes. Coverage pins the
contract between this agent and the base wrapper:

* ``role`` resolves to ``research_model`` (per ``config.yaml`` line 14)
* ``MAX_TOKENS["briefing_compiler"]`` (= 8000) is the ceiling sent
* ``name`` is forwarded as the agent label
* :meth:`tools` returns ``None`` — the compiler merges, never browses
* the user prompt includes the company, all THREE upstream JSON blobs
  (dossier, contacts, needs), and (optionally) ``user_context``
* the system prompt names the six briefing-section labels and the
  "no web-search or web-fetch" constraint
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. out-of-range source_index) reaches the
  same retry-then-``AgentOutputInvalid`` path — proving the index
  range check is reachable as a backstop
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from backend.agents.briefing_compiler import BriefingCompiler
from backend.agents.briefing_models import (
    Briefing,
    Confidence,
    LabMaturity,
)
from backend.agents.limits import MAX_TOKENS
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimum_briefing_payload() -> dict[str, Any]:
    """Smallest dict that validates as ``Briefing``. Used by wiring
    tests that don't care about content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "compiled_at": "2026-05-27",
        "snapshot": {
            "headline": "Acme Ltd — UK mid-market manufacturer",
            "one_line_desc": "Industrial controls manufacturer.",
            "lab_maturity": "mature",
            "why_interesting_to_ans": "Stable vendor footprint, "
            "potential SD-WAN refresh in 18 months.",
            "confidence": "MEDIUM",
        },
        "business_context": {},
        "it_landscape": {
            "lab_maturity_reasoning": "Named CTO, no active "
            "transformation programme visible.",
        },
        "key_people": {},
        "opportunity": {
            "recommended_angle": "Lead with the SD-WAN refresh angle.",
        },
        "sources": {"entries": [], "gaps": []},
    }


def _populated_briefing_payload() -> dict[str, Any]:
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "compiled_at": "2026-05-27",
        "snapshot": {
            "headline": "Acme Ltd — modernising network",
            "one_line_desc": "Industrial controls manufacturer.",
            "sector": "Industrial manufacturing",
            "headcount_band": "1000-5000",
            "hq_country": "United Kingdom",
            "ownership": "Private",
            "lab_maturity": "modernisation_in_progress",
            "why_interesting_to_ans": "Active SD-WAN job postings "
            "align with ANS's network refresh proposition.",
            "confidence": "HIGH",
        },
        "business_context": {
            "news": [
                {
                    "summary": "Acme announces cloud migration.",
                    "detail": "24-month migration programme.",
                    "confidence": "HIGH",
                    "source_indices": [0],
                }
            ],
        },
        "it_landscape": {
            "lab_maturity_reasoning": "Active SD-WAN job postings "
            "plus zero-trust vendor case study.",
            "vendors": [
                {
                    "summary": "Cisco incumbent.",
                    "detail": "Existing 3-year contract.",
                    "confidence": "MEDIUM",
                    "source_indices": [0],
                }
            ],
        },
        "key_people": {
            "contacts": [
                {
                    "name": "Alice Example",
                    "job_title": "CTO",
                    "function": "CTO",
                    "seniority": "C_LEVEL",
                    "confidence": "HIGH",
                    "source_index": 0,
                }
            ],
        },
        "opportunity": {
            "ranked_needs": [
                {
                    "priority": 1,
                    "summary": "Modernise SD-WAN",
                    "detail": "Open Head of SD-WAN posting indicates "
                    "an in-flight programme ANS could accelerate.",
                    "suggested_products": ["SD-WAN replacement"],
                    "entry_angle": "Lead with SD-WAN refresh.",
                    "watch_outs": ["Existing incumbent contract."],
                    "confidence": "HIGH",
                    "source_indices": [0],
                }
            ],
            "buying_cycle_stage": "Evaluating",
            "recommended_angle": "Lead with the SD-WAN refresh angle.",
            "watch_outs": [],
        },
        "sources": {
            "entries": [
                {
                    "url": "https://acme.example.com/news/cloud",
                    "title": "Acme migrates to cloud",
                    "retrieved_at": "2026-05-27",
                    "confidence": "HIGH",
                }
            ],
            "gaps": [],
        },
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_briefing_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _dossier_blob() -> str:
    return '{"company_name":"Acme Ltd","retrieved_at":"2026-05-27"}'


def _contacts_blob() -> str:
    return '{"company_name":"Acme Ltd","contacts":[],"gaps":[]}'


def _needs_blob() -> str:
    return (
        '{"company_name":"Acme Ltd","lab_maturity":"mature",'
        '"lab_maturity_reasoning":"...","lab_maturity_confidence":"MEDIUM",'
        '"needs":[],"gaps":[]}'
    )


def _run(
    agent: BriefingCompiler,
    *,
    job_id: str | None = None,
    **overrides: Any,
) -> Briefing:
    """Run the agent with the standard input set, allowing overrides."""
    kwargs: dict[str, Any] = {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "dossier_json": _dossier_blob(),
        "contacts_json": _contacts_blob(),
        "needs_json": _needs_blob(),
    }
    kwargs.update(overrides)
    return agent.run(job_id=job_id, **kwargs)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling
# ---------------------------------------------------------------------------

def test_agent_name_is_briefing_compiler(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    assert fake_cloud_client.calls[0]["agent"] == "briefing_compiler"


def test_role_resolves_to_research_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_max_tokens_matches_briefing_compiler_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["briefing_compiler"]
        == 8000
    )


# ---------------------------------------------------------------------------
# 2. No tools — the compiler merges, never browses
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    call = fake_cloud_client.calls[0]
    assert call["tools"] is None
    assert call["tool_use_counts"] is None


# ---------------------------------------------------------------------------
# 3. Prompt rendering — all three upstream blobs land in the prompt
# ---------------------------------------------------------------------------

def test_user_prompt_contains_company_name_and_url(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Acme Ltd" in content
    assert "https://acme.example.com/" in content


def test_user_prompt_contains_dossier_contacts_and_needs_json(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The compiler is the merge point — it must see all three upstream
    artefacts in its prompt body, verbatim. If any blob were missing the
    model could not honour the "merge only, do not invent" rule."""
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert _dossier_blob() in content
    assert _contacts_blob() in content
    assert _needs_blob() in content


def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent)

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    _run(agent, user_context="focus on the SD-WAN angle")

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "focus on the SD-WAN angle" in content


def test_system_prompt_carries_six_section_labels(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The six section attribute names are the schema contract. Pin
    them in the system prompt so a future edit cannot silently rename
    one (which would silently break Stage 2 writers + the renderer)."""
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    for needle in (
        "snapshot",
        "business_context",
        "it_landscape",
        "key_people",
        "opportunity",
        "sources",
    ):
        assert needle in system, (
            f"expected the system prompt to mention {needle!r}"
        )


def test_system_prompt_carries_no_tools_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "no web-search or web-fetch" in system.lower()


def test_system_prompt_warns_against_invention(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "do not invent" in system.lower()


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_briefing(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_briefing_payload())])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    result = _run(agent)

    assert isinstance(result, Briefing)
    assert result.company_name == "Acme Ltd"
    assert result.snapshot.lab_maturity is LabMaturity.MODERNISATION_IN_PROGRESS
    assert result.snapshot.confidence is Confidence.HIGH
    assert len(result.opportunity.ranked_needs) == 1
    assert result.opportunity.ranked_needs[0].priority == 1
    assert result.compiled_at == date(2026, 5, 27)


def test_empty_briefing_is_a_legal_outcome(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A briefing whose sections are mostly empty (the dossier surfaced
    no public info) is a valid result — the agent must not raise or
    retry just because the inner lists are empty."""
    fake_cloud_client.script([_ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    result = _run(agent)

    assert isinstance(result, Briefing)
    assert result.business_context.news == []
    assert result.key_people.contacts == []
    assert result.opportunity.ranked_needs == []
    assert result.sources.entries == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    result = _run(agent)

    assert isinstance(result, Briefing)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        _run(agent)

    assert exc_info.value.agent == "briefing_compiler"
    assert exc_info.value.model == test_models.research_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="briefing_compiler",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        _run(agent, job_id="job-trap")

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_out_of_range_source_index_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response with a ``source_index`` past the end of
    ``sources.entries`` must hit the same retry-then-AgentOutputInvalid
    path as a plain parse failure. Pin this so the briefing-level
    range validator is reachable as a backstop end-to-end."""
    bad_payload = _populated_briefing_payload()
    # Sources list has one entry (index 0); add a bad index referencing
    # entry 5 in the news section.
    bad_payload["business_context"]["news"][0]["source_indices"] = [5]
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        _run(agent)

    assert len(fake_cloud_client.calls) == 2


def test_negative_source_index_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A negative ``source_index`` on a ranked need must also hit the
    same retry-then-AgentOutputInvalid path — proving both layers of
    the range guard are reachable through the wrapper."""
    bad_payload = _populated_briefing_payload()
    bad_payload["opportunity"]["ranked_needs"][0]["source_indices"] = [-1]
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = BriefingCompiler(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        _run(agent)

    assert len(fake_cloud_client.calls) == 2
