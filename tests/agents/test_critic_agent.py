"""Behaviour tests for :class:`backend.agents.critic.Stage2CriticAgent`.

Mirrors ``tests/agents/test_benefits_agent.py``. Everything runs
through the fake :class:`FakeCloudClient` — no real SDK, no network,
no DB, no filesystem reads of ANS knowledge, briefing, mapping or
writer artefacts. Coverage pins the contract between this agent and
the base wrapper:

* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1 / Trap 2 lookups land under ``critic`` — the pre-existing
  registry key in :mod:`backend.agents.limits`
* ``role`` resolves to ``critic_model`` (distinct from the writer
  model used by the three Stage 2 writers and the research model
  used by Stage 1)
* ``MAX_TOKENS["critic"]`` (= 2000) is the ceiling sent
* :meth:`tools` returns ``None`` — the critic is text-in / JSON-out
  and must NOT request ``web_search`` or ``web_fetch``
* user prompt includes the company, the briefing JSON verbatim, the
  product mapping JSON verbatim, the three writer artefact JSONs
  verbatim, the knowledge bundle verbatim, and (optionally)
  ``user_context``
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. verdict/severity mismatch) reaches
  the same retry-then-``AgentOutputInvalid`` path
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.agents.critic import Stage2CriticAgent
from backend.agents.critic_models import (
    CitedArtefact,
    Severity,
    Stage2CriticReport,
    Verdict,
)
from backend.agents.limits import MAX_TOKENS
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue_payload(
    *,
    artefact: str = "BENEFITS",
    locator: str = "executive_summary.body[0].claim",
    severity: str = "INFO",
    category: str = "OTHER",
    description: str = (
        "Tone is slightly heavier on hype words than the senior "
        "pre-sales voice the rest of the brief uses."
    ),
    suggested_fix: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artefact": artefact,
        "locator": locator,
        "severity": severity,
        "category": category,
        "description": description,
    }
    if suggested_fix is not None:
        payload["suggested_fix"] = suggested_fix
    return payload


def _minimum_report_payload() -> dict[str, Any]:
    """Smallest dict that validates as :class:`Stage2CriticReport`.
    READY verdict with no issues — used by wiring tests that don't
    care about the report's content."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "READY",
        "issues": [],
        "summary": (
            "All three writer artefacts cleared the four checks "
            "against the approved briefing and the ANS knowledge "
            "bundle."
        ),
    }


def _populated_report_payload() -> dict[str, Any]:
    """A populated report with one WARNING issue and one INFO note —
    exercises the cross-field validator on the warning rule end-to-
    end through the agent's parse path."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "READY_WITH_WARNINGS",
        "issues": [
            _issue_payload(
                artefact="FAQ",
                locator="rows[3].answer",
                severity="WARNING",
                category="OFF_BRAND_TONE",
                description=(
                    "Answer leans on a marketing phrase that does "
                    "not match the rest of the register."
                ),
                suggested_fix=(
                    "Re-cast the second sentence in plain "
                    "pre-sales voice."
                ),
            ),
            _issue_payload(
                artefact="OBJECTIONS",
                locator="rows[2].response",
                severity="INFO",
                category="OTHER",
                description=(
                    "Response is fine but slightly longer than the "
                    "rest of the register."
                ),
            ),
        ],
        "summary": (
            "Artefacts are otherwise sound; one tone slip in the FAQ "
            "register is the only flag."
        ),
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_report_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _briefing_blob() -> str:
    """Stand-in for the approved briefing JSON. Opaque blob — only
    verbatim presence in the prompt matters."""
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


def _benefits_blob() -> str:
    return (
        '{"company_name":"Acme Ltd",'
        '"written_at":"2026-05-27",'
        '"executive_summary":{"heading":"Strategic"}}'
    )


def _faq_blob() -> str:
    return (
        '{"company_name":"Acme Ltd",'
        '"written_at":"2026-05-27",'
        '"rows":[{"category":"PRODUCT"}]}'
    )


def _objections_blob() -> str:
    return (
        '{"company_name":"Acme Ltd",'
        '"written_at":"2026-05-27",'
        '"rows":[{"category":"PRICE"}]}'
    )


def _knowledge_blob() -> str:
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
        "benefits_json": _benefits_blob(),
        "faq_json": _faq_blob(),
        "objections_json": _objections_blob(),
        "knowledge_bundle": _knowledge_blob(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling / output_model
# ---------------------------------------------------------------------------

def test_agent_name_is_critic(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``name`` is what Trap 1 / Trap 2 / cost-control look up. It
    must equal the existing registry key reserved at Step 1 so the
    critic doesn't silently fall outside the runaway traps."""
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["agent"] == "critic"


def test_role_resolves_to_critic_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The critic uses the dedicated critic-model id, distinct from
    the writer model the three Stage 2 writers share and the
    research model used by Stage 1."""
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert fake_cloud_client.calls[0]["model"] == test_models.critic_model


def test_max_tokens_matches_critic_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["critic"]
        == 2000
    )


def test_output_model_is_stage2_critic_report() -> None:
    """Declared output model must be :class:`Stage2CriticReport` —
    the base wrapper's parse step validates against this."""
    assert Stage2CriticAgent.output_model is Stage2CriticReport


# ---------------------------------------------------------------------------
# 2. No tools — critic is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None``
    and must NOT allocate a ``tool_use_counts`` dict — that's how the
    base contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    call = fake_cloud_client.calls[0]
    assert call["tools"] is None
    assert call["tool_use_counts"] is None


# ---------------------------------------------------------------------------
# 3. Prompt rendering — verbatim threading of every input
# ---------------------------------------------------------------------------

def test_user_prompt_contains_company_and_all_six_blobs_verbatim(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """Every input the critic reasons over must reach the prompt body
    verbatim — the agent is not allowed to summarise or slice."""
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Acme Ltd" in content
    assert "https://acme.example.com/" in content
    assert _briefing_blob() in content
    assert _mapping_blob() in content
    assert _benefits_blob() in content
    assert _faq_blob() in content
    assert _objections_blob() in content
    assert _knowledge_blob() in content


def test_user_context_omitted_when_not_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(**_run_kwargs())

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" not in content


def test_user_context_is_rendered_when_provided(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    agent.run(
        **_run_kwargs(user_context="prioritise tone checks over citations")
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "prioritise tone checks over citations" in content


def test_system_prompt_names_four_checks(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The closed-enum self-fence on IssueCategory relies on the
    model knowing the four handover-named checks. Pin them in the
    system prompt so a future edit cannot silently drop one."""
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "UNSUPPORTED_COMPANY_CLAIM" in system
    assert "UNSUPPORTED_PRODUCT_CLAIM" in system
    assert "FACTUAL_INCONSISTENCY" in system
    assert "OFF_BRAND_TONE" in system


def test_system_prompt_names_three_verdict_values(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The cross-field validator only fires if the model has been
    told which verdicts exist. Pin all three values in the system
    prompt."""
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "READY" in system
    assert "READY_WITH_WARNINGS" in system
    assert "NEEDS_REVISION" in system


def test_system_prompt_names_three_severity_values(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "BLOCKING" in system
    assert "WARNING" in system
    assert "INFO" in system


def test_system_prompt_names_three_artefacts(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "BENEFITS" in system
    assert "FAQ" in system
    assert "OBJECTIONS" in system


def test_system_prompt_carries_grounding_constraint(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The critic must stay grounded in approved inputs — pin the
    no-external-research language in the prompt so future edits
    cannot weaken it without a visible test diff."""
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    assert "approved briefing" in system.lower()
    assert "knowledge" in system.lower()
    assert "no commentary" in system.lower()


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_report(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_populated_report_payload())])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, Stage2CriticReport)
    assert result.company_name == "Acme Ltd"
    assert result.verdict is Verdict.READY_WITH_WARNINGS
    assert len(result.issues) == 2
    assert result.issues[0].artefact is CitedArtefact.FAQ
    assert result.issues[0].severity is Severity.WARNING


def test_minimum_report_validates_and_returns(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A READY report with no issues is a legal minimum-shape
    outcome — the agent must not retry just because ``issues`` is
    empty."""
    fake_cloud_client.script([_ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, Stage2CriticReport)
    assert result.verdict is Verdict.READY
    assert result.issues == []
    assert result.gaps == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    result = agent.run(**_run_kwargs())

    assert isinstance(result, Stage2CriticReport)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(**_run_kwargs())

    assert exc_info.value.agent == "critic"
    assert exc_info.value.model == test_models.critic_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """Trap exceptions raised inside ``messages_create`` must
    propagate untouched — the orchestrator (Step 9) has the single
    catch-point. The critic must not swallow them."""
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="critic",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(**_run_kwargs(job_id="job-trap"))

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


# ---------------------------------------------------------------------------
# 5. Schema-level rejections reach the retry path end-to-end
# ---------------------------------------------------------------------------

def test_invalid_verdict_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response with a hallucinated verdict (e.g. ``MAYBE_READY``)
    must hit the same retry-then-AgentOutputInvalid path as a plain
    parse failure. Pin that the enum self-fence is actually
    reachable end-to-end."""
    bad_payload = _minimum_report_payload()
    bad_payload["verdict"] = "MAYBE_READY"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_verdict_severity_mismatch_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response that emits a BLOCKING issue but stamps verdict
    READY must hit the retry path — proves the cross-field
    validator is reachable end-to-end, not just at the schema
    level."""
    bad_payload = _populated_report_payload()
    bad_payload["verdict"] = "READY"
    # The populated payload only has WARNING+INFO issues; READY
    # forbids WARNING, so this exercises the warning rule.
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_extra_field_response_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """``extra="forbid"`` is the schema's self-fence against silent
    drift. Prove that an unknown field at the top level reaches the
    retry path end-to-end."""
    bad_payload = _minimum_report_payload()
    bad_payload["surprise_field"] = "nope"
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2


def test_empty_description_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """An issue with a blank description is useless to the operator
    — the ``min_length=1`` fence must reach the retry path so the
    critic cannot accidentally ship one."""
    bad_payload = _populated_report_payload()
    bad_payload["issues"][0]["description"] = ""
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = Stage2CriticAgent(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(**_run_kwargs())

    assert len(fake_cloud_client.calls) == 2
