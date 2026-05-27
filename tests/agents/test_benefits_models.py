"""Schema tests for :mod:`backend.agents.benefits_models`.

Pure Pydantic round-trip and validation checks. No agent, no fake
client — the agent's behaviour tests live in
``tests/agents/test_benefits_agent.py``.

Coverage pins:

* ``Audience`` is closed to exactly the three tiers the handover
  names (CTO/CIO, ENGINEERS, IT_DIRECTOR)
* ``Confidence`` is reused from :mod:`backend.agents.research_models`
* ``BenefitsBrief`` rejects ``extra`` fields on every nested model
* Each named section's ``audience`` field is pinned by the top-level
  validator (executive_summary → CTO_CIO, technical_fit → ENGINEERS,
  business_case → IT_DIRECTOR)
* Negative ``knowledge_excerpt_refs`` / ``briefing_source_refs`` are
  rejected at both the claim and the brief level
* Empty refs lists are accepted (a claim can stand on neighbouring
  evidence without a citation list)
* Empty ``body`` on a section is rejected — a section without claims
  is editorial noise
* ``gaps`` is optional and defaults to ``[]``
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
    Confidence,
)
from backend.agents import research_models as research_models_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _claim(
    *,
    claim: str = "Reduce SD-WAN cutover risk",
    detail: str = (
        "Emulate the planned topology in the lab with realistic "
        "latency, loss and jitter before any production change."
    ),
    why_it_matters: str = (
        "Catches design errors before they hit production."
    ),
    knowledge_excerpt_refs: list[int] | None = None,
    briefing_source_refs: list[int] | None = None,
    confidence: str = "HIGH",
) -> dict[str, Any]:
    return {
        "claim": claim,
        "detail": detail,
        "why_it_matters": why_it_matters,
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


def _section(
    *,
    heading: str = "Executive summary",
    audience: str = "CTO_CIO",
    summary: str = (
        "Three lab-validated wins that de-risk the SD-WAN programme."
    ),
    body: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "heading": heading,
        "audience": audience,
        "summary": summary,
        "body": body if body is not None else [_claim()],
    }


def _populated_payload() -> dict[str, Any]:
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "executive_summary": _section(
            heading="Strategic summary",
            audience="CTO_CIO",
            summary=(
                "ANS lab capacity de-risks the SD-WAN programme "
                "ahead of cutover."
            ),
        ),
        "technical_fit": _section(
            heading="Engineering fit",
            audience="ENGINEERS",
            summary=(
                "Netropy 100G covers the planned bandwidth and "
                "impairment ranges end-to-end."
            ),
            body=[
                _claim(
                    claim="Bandwidth headroom up to 100G",
                    detail=(
                        "Netropy 100G emulators sustain line-rate "
                        "traffic with controllable impairments."
                    ),
                    why_it_matters=(
                        "Matches the upper bound of the planned "
                        "SD-WAN aggregate."
                    ),
                )
            ],
        ),
        "business_case": _section(
            heading="Spend justification",
            audience="IT_DIRECTOR",
            summary=(
                "Lab validation cost is a small fraction of a "
                "rollback's blast radius."
            ),
            body=[
                _claim(
                    claim="Lower cutover-rollback risk",
                    detail=(
                        "Pre-production validation surfaces design "
                        "errors that would otherwise force a "
                        "live-network rollback."
                    ),
                    why_it_matters=(
                        "Protects the change-window SLAs the "
                        "briefing flags as load-bearing."
                    ),
                )
            ],
        ),
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


# ---------------------------------------------------------------------------
# 1. Enum closure / Confidence re-use
# ---------------------------------------------------------------------------

def test_audience_enum_values() -> None:
    """Audience is closed to exactly the three handover-named tiers."""
    assert {member.value for member in Audience} == {
        "CTO_CIO",
        "ENGINEERS",
        "IT_DIRECTOR",
    }


def test_confidence_is_shared_with_research_models() -> None:
    """Single source of truth — re-importing the enum from a parallel
    module would silently drift over time."""
    assert Confidence is research_models_module.Confidence


# ---------------------------------------------------------------------------
# 2. Round-trip — populated payload
# ---------------------------------------------------------------------------

def test_populated_payload_validates() -> None:
    brief = BenefitsBrief.model_validate(_populated_payload())
    assert brief.company_name == "Acme Ltd"
    assert brief.executive_summary.audience is Audience.CTO_CIO
    assert brief.technical_fit.audience is Audience.ENGINEERS
    assert brief.business_case.audience is Audience.IT_DIRECTOR
    assert brief.executive_summary.body[0].confidence is Confidence.HIGH


def test_populated_payload_serialises_and_revalidates() -> None:
    brief = BenefitsBrief.model_validate(_populated_payload())
    json_text = brief.model_dump_json()
    again = BenefitsBrief.model_validate_json(json_text)
    assert again == brief


def test_gaps_defaults_to_empty_list() -> None:
    payload = _populated_payload()
    payload.pop("gaps")
    brief = BenefitsBrief.model_validate(payload)
    assert brief.gaps == []


# ---------------------------------------------------------------------------
# 3. extra="forbid" — every nested model
# ---------------------------------------------------------------------------

def test_extra_field_on_brief_rejected() -> None:
    payload = _populated_payload()
    payload["surprise"] = "nope"
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_extra_field_on_section_rejected() -> None:
    payload = _populated_payload()
    payload["executive_summary"]["bogus"] = True
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_extra_field_on_claim_rejected() -> None:
    payload = _populated_payload()
    payload["executive_summary"]["body"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


# ---------------------------------------------------------------------------
# 4. Closed-enum rejections
# ---------------------------------------------------------------------------

def test_invalid_audience_rejected() -> None:
    """A hallucinated audience (e.g. ``CFO``) is the schema's primary
    self-fence. Reject at validation time so the retry loop fires
    before the bad value reaches the renderer."""
    payload = _populated_payload()
    payload["executive_summary"]["audience"] = "CFO"
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_invalid_confidence_rejected() -> None:
    payload = _populated_payload()
    payload["executive_summary"]["body"][0]["confidence"] = "VERY_HIGH"
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


# ---------------------------------------------------------------------------
# 5. Section→audience pinning
# ---------------------------------------------------------------------------

def test_executive_summary_audience_pinned_to_cto_cio() -> None:
    """Swapping ``executive_summary.audience`` to ``ENGINEERS`` must
    fail — the renderer expects the strategic tier in this slot."""
    payload = _populated_payload()
    payload["executive_summary"]["audience"] = "ENGINEERS"
    with pytest.raises(ValidationError) as exc_info:
        BenefitsBrief.model_validate(payload)
    assert "executive_summary" in str(exc_info.value)


def test_technical_fit_audience_pinned_to_engineers() -> None:
    payload = _populated_payload()
    payload["technical_fit"]["audience"] = "CTO_CIO"
    with pytest.raises(ValidationError) as exc_info:
        BenefitsBrief.model_validate(payload)
    assert "technical_fit" in str(exc_info.value)


def test_business_case_audience_pinned_to_it_director() -> None:
    payload = _populated_payload()
    payload["business_case"]["audience"] = "ENGINEERS"
    with pytest.raises(ValidationError) as exc_info:
        BenefitsBrief.model_validate(payload)
    assert "business_case" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Refs non-negativity
# ---------------------------------------------------------------------------

def test_negative_knowledge_excerpt_ref_rejected() -> None:
    payload = _populated_payload()
    payload["executive_summary"]["body"][0]["knowledge_excerpt_refs"] = [-1]
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_negative_briefing_source_ref_rejected() -> None:
    payload = _populated_payload()
    payload["technical_fit"]["body"][0]["briefing_source_refs"] = [-2]
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_empty_refs_lists_accepted() -> None:
    """A claim can stand on adjacent evidence without an explicit ref
    list — the writer should not be forced to invent one."""
    payload = _populated_payload()
    payload["executive_summary"]["body"][0]["knowledge_excerpt_refs"] = []
    payload["executive_summary"]["body"][0]["briefing_source_refs"] = []
    brief = BenefitsBrief.model_validate(payload)
    assert brief.executive_summary.body[0].knowledge_excerpt_refs == []
    assert brief.executive_summary.body[0].briefing_source_refs == []


# ---------------------------------------------------------------------------
# 7. Cardinality
# ---------------------------------------------------------------------------

def test_empty_section_body_rejected() -> None:
    """A section with zero claims is editorial noise — fail parse so
    the writer cannot accidentally ship one."""
    payload = _populated_payload()
    payload["executive_summary"]["body"] = []
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


def test_missing_section_rejected() -> None:
    """All three sections are required — omitting one fails parse."""
    payload = _populated_payload()
    payload.pop("technical_fit")
    with pytest.raises(ValidationError):
        BenefitsBrief.model_validate(payload)


# ---------------------------------------------------------------------------
# 8. Direct model construction (smoke)
# ---------------------------------------------------------------------------

def test_can_construct_via_python_objects() -> None:
    """Sanity: every public model can be constructed from Python
    objects, not just JSON. Catches accidental field renames at the
    Python level."""
    claim = BenefitClaim(
        claim="Reduce SD-WAN cutover risk",
        detail=(
            "Emulate planned topology in the lab before any "
            "production change."
        ),
        why_it_matters="Catches design errors before production.",
        knowledge_excerpt_refs=[0],
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )
    section_cto = BenefitsSection(
        heading="Strategic summary",
        audience=Audience.CTO_CIO,
        summary="Three lab-validated wins.",
        body=[claim],
    )
    section_eng = BenefitsSection(
        heading="Engineering fit",
        audience=Audience.ENGINEERS,
        summary="Bandwidth and impairment headroom.",
        body=[claim],
    )
    section_itd = BenefitsSection(
        heading="Spend justification",
        audience=Audience.IT_DIRECTOR,
        summary="Lab cost vs rollback blast radius.",
        body=[claim],
    )
    brief = BenefitsBrief(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at="2026-05-27",  # type: ignore[arg-type]
        executive_summary=section_cto,
        technical_fit=section_eng,
        business_case=section_itd,
        gaps=[],
    )
    assert brief.executive_summary.audience is Audience.CTO_CIO
    assert brief.technical_fit.body[0].confidence is Confidence.HIGH
    assert brief.business_case.audience is Audience.IT_DIRECTOR
