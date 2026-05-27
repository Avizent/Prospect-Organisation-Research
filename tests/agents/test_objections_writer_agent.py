"""Behaviour tests for :class:`backend.agents.objections_writer.ObjectionsWriter`.

Mirrors ``tests/agents/test_faq_writer_agent.py``. Everything runs
through the fake :class:`FakeCloudClient` — no real SDK, no network,
no DB, no filesystem reads of ANS knowledge, briefing or mapping
artefacts. Coverage pins the contract between this agent and the
base wrapper:

* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1 / Trap 2 lookups land under ``objections_writer`` — the
  pre-existing registry key in :mod:`backend.agents.limits`
* ``role`` resolves to ``writer_model`` (distinct from the research
  model used by Stage 1 + the mapping agent)
* ``MAX_TOKENS["objections_writer"]`` (= 4000) is the ceiling sent
* :meth:`tools` returns ``None`` — the writer is text-in / JSON-out
  and must NOT request ``web_search`` or ``web_fetch``
* user prompt includes the company, the briefing JSON verbatim, the
  product mapping JSON verbatim, the knowledge bundle verbatim, and
  (optionally) ``user_context``
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. invalid category, sub-15 row count,
  empty escalation_path) reaches the same retry-then-
  ``AgentOutputInvalid`` path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.objections_models import (
    Confidence,
    ObjectionCategory,
    ObjectionsRegister,
)
from backend.agents.objections_writer import ObjectionsWriter
from backend.agents.limits import MAX_TOKENS
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_payload(
    *,
    objection: str = "Your emulators look expensive vs. open-source.",
    category: str = "PRICE",
    knowledge_excerpt_refs: list[int] | None = None,
    briefing_source_refs: list[int] | None = None,
    escalation_path: str = "Account owner brings in solutions architect.",
    confidence: str = "HIGH",
) -> dict[str, Any]:
    return {
        "category": category,
        "objection": objection,
        "underlying_concern": (
            "Worried that procurement won't approve a six-figure lab "
            "spend when free tools exist."
        ),
        "response": (
            "ANS Netropy emulators are line-rate and impairment-accurate; "
            "open-source tools cap out before SD-WAN aggregate bandwidth "
            "and miss the realistic-jitter contract the rollout needs."
        ),
        "knowledge_excerpt_refs": (
            knowledge_excerpt_refs if knowledge_excerpt_refs is not None
            else [0]
        ),
        "briefing_source_refs": (
            briefing_source_refs if briefing_source_refs is not None
            else [0]
        ),
        "escalation_path": escalation_path,
        "confidence": confidence,
    }


def _rows(n: int) -> list[dict[str, Any]]:
    return [_row_payload(objection=f"Stated objection #{i}.") for i in range(n)]


def _minimum_register_payload() -> dict[str, Any]:
    """15 rows — schema floor. Used by wiring tests that don't care
    about the register's content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "rows": _rows(15),
    }


def _populated_register_payload() -> dict[str, Any]:
    categories = [
        "PRICE",
        "TIMING",
        "TECHNICAL_FIT",
        "COMPETITION",
        "AUTHORITY",
        "TRUST",
        "INTEGRATION",
        "COMPLIANCE",
    ]
    rows = [
        _row_payload(
            objection=f"Stated objection #{i} ({categories[i % 8]}).",
            category=categories[i % len(categories)],
        )
        for i in range(20)
    ]
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "rows": rows,
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_register_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _briefing_blob() -> str:
    return (
        '{"company_name":"Acme Ltd",'
        '"company_url":"https://acme.example.com/",'
        '"compiled_at":"2026-05-27"}'
    )


def _mapping_blob() -> str:
    return (
        '{"company_name":"Acme Ltd",'
        '"mapped_at":"2026-05-27",'
        '"matches":[{"need_priority":1}]}'
    )


def _knowledge_blob() -> str:
    return (
        "# ANS Knowledge Bundle\n\n"
        "## Netropy Network Emulators\nBandwidth up to 100 Gbps.\n\n"
        "## Known objections\nPrior pricing pushback summary.\n"
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

def test_agent_name_is_objections_writer(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``name`` is what Trap 1 / Trap 2 / cost-control look up. It
    must equal the existing registry key so the writer doesn't
    silently fall outside the runaway traps."""
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["agent"] == "objections_writer"


def test_role_resolves_to_writer_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The three Stage 2 writers share the writer model id, distinct
    from the research model used by Stage 1 and the mapping agent."""
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["model"] == test_models.writer_model
    assert fake_cloud_client.calls[0]["model"] == "test-writer-model"


def test_max_tokens_matches_objections_writer_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["objections_writer"]
        == 4000
    )


def test_output_model_is_objections_register() -> None:
    """Class-level pin: a renamed/moved schema would break this
    assertion before the next agent run did anything more subtle."""
    assert ObjectionsWriter.output_model is ObjectionsRegister


# ---------------------------------------------------------------------------
# 2. No tools — writer is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None``
    and must NOT allocate a ``tool_use_counts`` dict — that's how the
    base contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

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
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

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
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs(user_context="emphasise procurement pushback"))

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "emphasise procurement pushback" in content


def test_system_prompt_names_all_eight_categories(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The closed-enum self-fence on ObjectionCategory relies on the
    model knowing the eight category names. Pin them in the system
    prompt so a future edit cannot silently drop one."""
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "PRICE" in system
    assert "TIMING" in system
    assert "TECHNICAL_FIT" in system
    assert "COMPETITION" in system
    assert "AUTHORITY" in system
    assert "TRUST" in system
    assert "INTEGRATION" in system
    assert "COMPLIANCE" in system


def test_system_prompt_names_all_six_xlsx_columns(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """Handover §9.4 step 8 pins the six XLSX columns in fixed order.
    The system prompt must name every one of them so the writer knows
    the shape it's filling — a silent column drop would only surface
    at render time."""
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    # Match the column names case-insensitively but as substrings.
    lowered = system.lower()
    assert "category" in lowered
    assert "objection" in lowered
    assert "underlying concern" in lowered
    assert "response" in lowered
    assert "supporting evidence" in lowered
    assert "escalation path" in lowered


def test_system_prompt_carries_grounding_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The objections register must stay grounded in approved inputs
    — pin the no-fabrication language in the prompt so future edits
    cannot weaken it without a visible test diff."""
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "approved briefing" in system.lower()
    assert "knowledge" in system.lower()
    assert "no commentary" in system.lower()


def test_system_prompt_names_the_15_to_20_floor_and_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The handover-pinned cardinality bounds should be visible in
    the prompt so the model knows the schema's hard floor and
    ceiling — drops would not show up in tests until a retry storm."""
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "15" in system
    assert "20" in system


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_objections_register(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_register_payload())])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, ObjectionsRegister)
    assert result.company_name == "Acme Ltd"
    assert len(result.rows) == 20
    assert result.rows[0].confidence is Confidence.HIGH
    assert result.rows[0].category is ObjectionCategory.PRICE


def test_minimum_register_validates_and_returns(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A 15-row register with no gaps is a legal minimum-shape
    outcome — the agent must not retry just because ``gaps`` is
    empty."""
    fake_cloud_client.script([_ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, ObjectionsRegister)
    assert len(result.rows) == 15
    assert result.gaps == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, ObjectionsRegister)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(**_run_kwargs())

    assert exc_info.value.agent == "objections_writer"
    assert exc_info.value.model == test_models.writer_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="objections_writer",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(**_run_kwargs(job_id="job-trap"))

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_invalid_category_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A category outside the closed enum (e.g.
    ``PRICING_AND_PROCUREMENT``) hits the retry path — proves the
    enum self-fence is reachable end-to-end, not just at the schema
    level."""
    bad_payload = _populated_register_payload()
    bad_payload["rows"][0]["category"] = "PRICING_AND_PROCUREMENT"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_under_fifteen_rows_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The ``min_length=15`` floor must reach the retry path so the
    writer cannot accidentally ship an under-spec register."""
    bad_payload = _minimum_register_payload()
    bad_payload["rows"] = _rows(14)
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_over_twenty_rows_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The ``max_length=20`` ceiling must reach the retry path so the
    writer cannot blow past the planned XLSX row budget."""
    bad_payload = _minimum_register_payload()
    bad_payload["rows"] = _rows(21)
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_empty_escalation_path_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """Column 6 of the XLSX is required and non-empty. An empty
    string must hit the retry path so the deliverable contract isn't
    silently broken."""
    bad_payload = _populated_register_payload()
    bad_payload["rows"][0]["escalation_path"] = ""
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_negative_ref_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The non-negativity validator on refs must reach the same
    retry-then-AgentOutputInvalid path."""
    bad_payload = _populated_register_payload()
    bad_payload["rows"][0]["knowledge_excerpt_refs"] = [-1]
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_extra_field_response_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``extra="forbid"`` is the schema's self-fence against silent
    drift. Prove that an unknown field at the top level reaches the
    retry path end-to-end."""
    bad_payload = _populated_register_payload()
    bad_payload["surprise_field"] = "nope"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ObjectionsWriter(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2
