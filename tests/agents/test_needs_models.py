"""Schema-validation tests for :mod:`backend.agents.needs_models`.

The :class:`LabMaturity` enum is the load-bearing branchpoint for every
Stage 2 writer; these tests pin its exact four values so a rename
cannot slide through silently.

Coverage:

1. ``LabMaturity`` is exactly the four handover-§9.3 values, lower-case.
2. ``NeedsAssessment`` required-field guards (every load-bearing field
   raises on omit).
3. ``IdentifiedNeed`` required-field guards + ``priority >= 1``.
4. ``EvidencePointer`` required-field guards + URL/date validation.
5. ``extra="forbid"`` on all three models.
6. ``Confidence`` enum is **the same object** as
   :class:`backend.agents.research_models.Confidence`.
7. ``lab_maturity_evidence`` and ``needs`` may both be empty.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.agents.needs_models import (
    Confidence,
    EvidencePointer,
    IdentifiedNeed,
    LabMaturity,
    NeedsAssessment,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid payloads
# ---------------------------------------------------------------------------

def _evidence(**overrides) -> EvidencePointer:
    base = {
        "summary": "Press release confirms cloud migration programme.",
        "source_url": "https://acme.example.com/news/cloud",
        "source_title": "Acme migrates to cloud",
        "retrieved_at": date(2026, 5, 27),
        "confidence": Confidence.HIGH,
    }
    base.update(overrides)
    return EvidencePointer(**base)


def _need(**overrides) -> IdentifiedNeed:
    base = {
        "summary": "Modernise SD-WAN footprint",
        "detail": "Press releases and job postings indicate a stalled "
        "SD-WAN rollout that ANS could accelerate.",
        "priority": 1,
        "evidence": [_evidence()],
        "confidence": Confidence.HIGH,
    }
    base.update(overrides)
    return IdentifiedNeed(**base)


_VALID_ASSESSMENT: dict = {
    "company_name": "Acme Ltd",
    "lab_maturity": LabMaturity.MATURE,
    "lab_maturity_reasoning": (
        "Named CTO, stable vendor footprint, no active modernisation "
        "programme visible in the last 18 months."
    ),
    "lab_maturity_evidence": [],
    "lab_maturity_confidence": Confidence.MEDIUM,
    "needs": [],
    "gaps": [],
}


# ---------------------------------------------------------------------------
# 1. LabMaturity is exactly the four handover values
# ---------------------------------------------------------------------------

def test_lab_maturity_has_exactly_four_documented_values() -> None:
    """Stage 2 writers branch on this enum — a renamed or added value
    silently breaks every writer's prioritisation. Pin the set."""
    assert {member.value for member in LabMaturity} == {
        "none_visible",
        "basic",
        "mature",
        "modernisation_in_progress",
    }


@pytest.mark.parametrize(
    "value",
    [
        "none_visible",
        "basic",
        "mature",
        "modernisation_in_progress",
    ],
)
def test_lab_maturity_accepts_each_documented_value(value: str) -> None:
    payload = dict(_VALID_ASSESSMENT)
    payload["lab_maturity"] = value
    assessment = NeedsAssessment(**payload)
    assert assessment.lab_maturity.value == value


def test_lab_maturity_rejects_unknown_value() -> None:
    payload = dict(_VALID_ASSESSMENT)
    payload["lab_maturity"] = "advanced"
    with pytest.raises(ValidationError):
        NeedsAssessment(**payload)


def test_lab_maturity_rejects_uppercase_variant() -> None:
    """The handover specifies lower-case underscore form so values
    persist directly into ``companies.lab_maturity``. ``MATURE`` would
    silently break that round trip — reject it at the schema layer."""
    payload = dict(_VALID_ASSESSMENT)
    payload["lab_maturity"] = "MATURE"
    with pytest.raises(ValidationError):
        NeedsAssessment(**payload)


# ---------------------------------------------------------------------------
# 2. NeedsAssessment required-field guards
# ---------------------------------------------------------------------------

def test_minimum_required_fields_validate() -> None:
    assessment = NeedsAssessment(**_VALID_ASSESSMENT)
    assert assessment.company_name == "Acme Ltd"
    assert assessment.lab_maturity is LabMaturity.MATURE
    assert assessment.lab_maturity_confidence is Confidence.MEDIUM
    assert assessment.lab_maturity_evidence == []
    assert assessment.needs == []
    assert assessment.gaps == []


@pytest.mark.parametrize(
    "field",
    [
        "company_name",
        "lab_maturity",
        "lab_maturity_reasoning",
        "lab_maturity_confidence",
    ],
)
def test_required_assessment_field_omitted_raises(field: str) -> None:
    payload = dict(_VALID_ASSESSMENT)
    del payload[field]
    with pytest.raises(ValidationError):
        NeedsAssessment(**payload)


def test_empty_company_name_rejected() -> None:
    with pytest.raises(ValidationError):
        NeedsAssessment(**{**_VALID_ASSESSMENT, "company_name": ""})


def test_empty_lab_maturity_reasoning_rejected() -> None:
    with pytest.raises(ValidationError):
        NeedsAssessment(
            **{**_VALID_ASSESSMENT, "lab_maturity_reasoning": ""}
        )


# ---------------------------------------------------------------------------
# 3. IdentifiedNeed required fields + priority >= 1
# ---------------------------------------------------------------------------

def test_identified_need_minimum_required_fields_validate() -> None:
    need = _need()
    assert need.summary == "Modernise SD-WAN footprint"
    assert need.priority == 1
    assert need.confidence is Confidence.HIGH


@pytest.mark.parametrize(
    "field",
    ["summary", "detail", "priority", "confidence"],
)
def test_identified_need_required_field_omitted_raises(field: str) -> None:
    payload = {
        "summary": "x",
        "detail": "y",
        "priority": 1,
        "confidence": Confidence.HIGH,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        IdentifiedNeed(**payload)


@pytest.mark.parametrize("priority", [0, -1, -100])
def test_identified_need_priority_rejects_non_positive(
    priority: int,
) -> None:
    with pytest.raises(ValidationError):
        _need(priority=priority)


def test_identified_need_priority_accepts_one() -> None:
    """The prompt instructs "1 = highest" — the schema must accept 1."""
    need = _need(priority=1)
    assert need.priority == 1


def test_identified_need_evidence_defaults_to_empty_list() -> None:
    """A need with INFERRED confidence may legitimately carry no
    evidence pointers. The schema must allow that — the prompt
    enforces the convention, not the validator."""
    need = _need(evidence=[])
    assert need.evidence == []


def test_identified_need_priority_ties_allowed_by_schema() -> None:
    """Ties are not schema-enforced — the briefing compiler tolerates
    them. Rejecting ties at validation time would cause retry loops
    on cosmetic disagreements."""
    needs = [
        _need(priority=1, summary="A"),
        _need(priority=1, summary="B"),
    ]
    assessment = NeedsAssessment(**{**_VALID_ASSESSMENT, "needs": needs})
    assert [n.priority for n in assessment.needs] == [1, 1]


# ---------------------------------------------------------------------------
# 4. EvidencePointer required fields + URL/date validation
# ---------------------------------------------------------------------------

def test_evidence_pointer_minimum_required_fields_validate() -> None:
    pointer = _evidence()
    assert pointer.confidence is Confidence.HIGH
    assert pointer.retrieved_at == date(2026, 5, 27)


@pytest.mark.parametrize(
    "field",
    ["summary", "source_url", "retrieved_at", "confidence"],
)
def test_evidence_pointer_required_field_omitted_raises(
    field: str,
) -> None:
    base = {
        "summary": "x",
        "source_url": "https://acme.example.com/",
        "retrieved_at": date(2026, 5, 27),
        "confidence": Confidence.HIGH,
    }
    del base[field]
    with pytest.raises(ValidationError):
        EvidencePointer(**base)


def test_evidence_pointer_source_url_must_be_url() -> None:
    with pytest.raises(ValidationError):
        _evidence(source_url="not-a-url")


def test_evidence_pointer_retrieved_at_must_be_date() -> None:
    with pytest.raises(ValidationError):
        _evidence(retrieved_at="not-a-date")


def test_evidence_pointer_empty_summary_rejected() -> None:
    with pytest.raises(ValidationError):
        _evidence(summary="")


def test_evidence_pointer_source_title_optional() -> None:
    pointer = _evidence(source_title=None)
    assert pointer.source_title is None


# ---------------------------------------------------------------------------
# 5. extra="forbid"
# ---------------------------------------------------------------------------

def test_unexpected_field_on_assessment_rejected() -> None:
    with pytest.raises(ValidationError):
        NeedsAssessment(
            **_VALID_ASSESSMENT,
            unknown="oops",
        )


def test_unexpected_field_on_need_rejected() -> None:
    with pytest.raises(ValidationError):
        IdentifiedNeed(
            summary="x",
            detail="y",
            priority=1,
            confidence=Confidence.HIGH,
            unknown="oops",
        )


def test_unexpected_field_on_evidence_pointer_rejected() -> None:
    with pytest.raises(ValidationError):
        EvidencePointer(
            summary="x",
            source_url="https://acme.example.com/",
            retrieved_at=date(2026, 5, 27),
            confidence=Confidence.HIGH,
            unknown="oops",
        )


# ---------------------------------------------------------------------------
# 6. Confidence is shared with research_models
# ---------------------------------------------------------------------------

def test_confidence_enum_is_shared_with_research_models() -> None:
    """The dossier's Confidence and the needs assessment's Confidence
    are the same object — re-importing the symbol must not silently
    create a parallel enum that means subtly different things."""
    from backend.agents.research_models import Confidence as ResearchConfidence

    assert Confidence is ResearchConfidence


# ---------------------------------------------------------------------------
# 7. Empty lists are legal outcomes
# ---------------------------------------------------------------------------

def test_lab_maturity_evidence_may_be_empty() -> None:
    """When ``lab_maturity == none_visible`` the absence of evidence is
    the call. The schema allows empty for all four values; the prompt
    asks for at least one pointer when the value is not
    ``none_visible``. Pin the schema-level permission."""
    assessment = NeedsAssessment(
        **{
            **_VALID_ASSESSMENT,
            "lab_maturity": LabMaturity.NONE_VISIBLE,
            "lab_maturity_evidence": [],
        }
    )
    assert assessment.lab_maturity is LabMaturity.NONE_VISIBLE
    assert assessment.lab_maturity_evidence == []


def test_needs_list_may_be_empty() -> None:
    """A target with no actionable needs is a legal outcome — surface
    via ``gaps`` instead of raising."""
    assessment = NeedsAssessment(
        **{
            **_VALID_ASSESSMENT,
            "needs": [],
            "gaps": ["no actionable infrastructure modernisation visible"],
        }
    )
    assert assessment.needs == []
    assert assessment.gaps == ["no actionable infrastructure modernisation visible"]


# ---------------------------------------------------------------------------
# Round-trip sanity
# ---------------------------------------------------------------------------

def test_assessment_round_trip_through_json() -> None:
    """Stage 2 writers receive the assessment as JSON text; pin that
    a populated assessment serialises and re-validates cleanly."""
    assessment = NeedsAssessment(
        **{
            **_VALID_ASSESSMENT,
            "lab_maturity": LabMaturity.MODERNISATION_IN_PROGRESS,
            "lab_maturity_evidence": [_evidence()],
            "lab_maturity_confidence": Confidence.HIGH,
            "needs": [_need()],
        }
    )
    dumped = assessment.model_dump_json()
    rebuilt = NeedsAssessment.model_validate_json(dumped)
    assert rebuilt.lab_maturity is LabMaturity.MODERNISATION_IN_PROGRESS
    assert rebuilt.lab_maturity_evidence[0].confidence is Confidence.HIGH
    assert rebuilt.needs[0].priority == 1
