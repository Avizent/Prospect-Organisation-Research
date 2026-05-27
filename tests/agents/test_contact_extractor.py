"""Behaviour tests for :class:`backend.agents.contact_extractor.ContactExtractor`.

Mirrors ``tests/agents/test_research_agent.py``. Everything runs through
the fake CloudClient — no SDK, no network, no DB writes. Coverage pins
the contract between this agent and the base wrapper:

* ``role`` resolves to ``research_model`` (per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping")
* ``MAX_TOKENS["contact_extractor"]`` is the ceiling sent
* ``name`` is forwarded as the agent label so cost-control logs and
  Trap 1/2 lookups land under the right key
* :meth:`tools` returns ``None`` — extraction is text-in / JSON-out and
  must NOT request ``web_search`` or ``web_fetch`` (handover §15: stay
  grounded in dossier-attributed sources)
* user prompt includes the company, the dossier JSON, and (optionally)
  ``user_context``
* parse-retry and ``AgentOutputInvalid`` behaviour from the base
  wrapper still applies
* :class:`RunawayTrapFired` propagates unchanged
* a schema-level rejection (e.g. personal-domain email in the emitted
  contact) reaches the same retry-then-AgentOutputInvalid path
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from backend.agents.contact_extractor import ContactExtractor
from backend.agents.contact_models import (
    Confidence,
    ContactExtractionResult,
    Function,
)
from backend.agents.limits import MAX_TOKENS
from backend.agents.output import AgentOutputInvalid
from backend.cost_control.exceptions import JobBudgetExceeded

from .conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimum_extraction_payload() -> dict[str, Any]:
    """Smallest dict that validates as ContactExtractionResult."""
    return {
        "company_name": "Acme Ltd",
        "contacts": [],
        "gaps": [],
    }


def _one_contact_payload() -> dict[str, Any]:
    return {
        "company_name": "Acme Ltd",
        "contacts": [
            {
                "name": "Alice Example",
                "function": "CIO",
                "source_url": "https://acme.example.com/news/cio",
                "retrieved_at": "2026-05-27",
                "confidence": "HIGH",
            }
        ],
        "gaps": [],
    }


def _ok_response(payload: dict[str, Any] | None = None) -> Any:
    return make_response(
        text=json.dumps(payload or _minimum_extraction_payload())
    )


def _bad_json_response() -> Any:
    return make_response(text="not json at all")


def _dossier_blob() -> str:
    """A small string that stands in for the research dossier JSON. The
    agent treats it as opaque text — the test only needs to assert that
    it lands in the user-prompt body verbatim."""
    return '{"company_name":"Acme Ltd","retrieved_at":"2026-05-27"}'


# ---------------------------------------------------------------------------
# 1. Identity / role / token ceiling
# ---------------------------------------------------------------------------

def test_agent_name_is_contact_extractor(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert fake_cloud_client.calls[0]["agent"] == "contact_extractor"


def test_role_resolves_to_research_model(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert fake_cloud_client.calls[0]["model"] == test_models.research_model


def test_max_tokens_matches_contact_extractor_ceiling(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert (
        fake_cloud_client.calls[0]["max_tokens"]
        == MAX_TOKENS["contact_extractor"]
        == 4000
    )


# ---------------------------------------------------------------------------
# 2. No tools — extraction is text-in / JSON-out
# ---------------------------------------------------------------------------

def test_tools_method_returns_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)
    assert agent.tools() is None


def test_tools_and_tool_use_counts_passed_as_none(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """If ``tools`` is ``None``, the wrapper must pass ``tools=None`` and
    must NOT allocate a ``tool_use_counts`` dict — that's how the base
    contract keeps Trap 2 (per-tool ceilings) off the books for
    text-in / JSON-out agents."""
    fake_cloud_client.script([_ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

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
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

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
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

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
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
        user_context="prioritise the new CISO",
    )

    content = fake_cloud_client.calls[0]["messages"][0]["content"]
    assert "Additional context from the user" in content
    assert "prioritise the new CISO" in content


def test_system_prompt_carries_compliance_rules(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """The handover §15 hard rules — function whitelist, personal-domain
    block, no under-21s — live in the system prompt. Pin the keywords
    so a future edit cannot silently strip them."""
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)
    system = agent.system_prompt()
    for needle in (
        "NETWORK",
        "INFRASTRUCTURE",
        "IT_EXECUTIVE",
        "SECURITY_EXECUTIVE",
        "CTO",
        "CIO",
        "gmail",
        "hotmail",
        "yahoo",
        "under 21",
        "No source, no contact",
    ):
        assert needle in system, (
            f"expected the system prompt to mention {needle!r}"
        )


# ---------------------------------------------------------------------------
# 4. Wrapper behaviour still applies
# ---------------------------------------------------------------------------

def test_run_returns_parsed_extraction_result(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_ok_response(_one_contact_payload())])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, ContactExtractionResult)
    assert result.company_name == "Acme Ltd"
    assert len(result.contacts) == 1
    assert result.contacts[0].name == "Alice Example"
    assert result.contacts[0].function is Function.CIO
    assert result.contacts[0].confidence is Confidence.HIGH
    assert result.contacts[0].retrieved_at == date(2026, 5, 27)


def test_empty_extraction_is_a_legal_outcome(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """Finding no contacts is a valid result — the agent must not raise
    or retry just because ``contacts`` is empty."""
    fake_cloud_client.script([_ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, ContactExtractionResult)
    assert result.contacts == []
    assert len(fake_cloud_client.calls) == 1


def test_parse_failure_retries_once_then_succeeds(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _ok_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    result = agent.run(
        job_id=None,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        dossier_json=_dossier_blob(),
    )

    assert isinstance(result, ContactExtractionResult)
    assert len(fake_cloud_client.calls) == 2


def test_two_parse_failures_raise_agent_output_invalid(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    fake_cloud_client.script([_bad_json_response(), _bad_json_response()])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid) as exc_info:
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert exc_info.value.agent == "contact_extractor"
    assert exc_info.value.model == test_models.research_model


def test_runaway_trap_propagates_unchanged(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    trap = JobBudgetExceeded(
        "synthetic trap",
        job_id="job-trap",
        agent="contact_extractor",
        context={},
    )
    fake_cloud_client.script([trap])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    with pytest.raises(JobBudgetExceeded) as exc_info:
        agent.run(
            job_id="job-trap",
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert exc_info.value is trap
    assert len(fake_cloud_client.calls) == 1


def test_schema_failure_in_emitted_contact_triggers_retry(
    fake_cloud_client: FakeCloudClient, test_models
) -> None:
    """A response that's valid JSON but breaks the schema (e.g.
    personal-domain email) must hit the same retry-then-
    AgentOutputInvalid path as a plain parse failure. Pin this so the
    GDPR backstop in the schema is actually reachable through the
    agent — the validator only protects us if the wrapper enforces it
    end-to-end."""
    bad_payload = {
        "company_name": "Acme Ltd",
        "contacts": [
            {
                "name": "Alice Example",
                "function": "CIO",
                "source_url": "https://acme.example.com/news/cio",
                "retrieved_at": "2026-05-27",
                "confidence": "HIGH",
                "email": "alice@gmail.com",  # personal domain — schema reject
            }
        ],
        "gaps": [],
    }
    fake_cloud_client.script([
        make_response(text=json.dumps(bad_payload)),
        make_response(text=json.dumps(bad_payload)),
    ])
    agent = ContactExtractor(client=fake_cloud_client, models=test_models)

    with pytest.raises(AgentOutputInvalid):
        agent.run(
            job_id=None,
            company_name="Acme Ltd",
            company_url="https://acme.example.com/",
            dossier_json=_dossier_blob(),
        )

    assert len(fake_cloud_client.calls) == 2
