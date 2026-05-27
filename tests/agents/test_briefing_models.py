"""Schema-validation tests for :mod:`backend.agents.briefing_models`.

The :class:`Briefing` is the load-bearing artefact for Stage 2; its
shape is consumed by every writer. These tests pin:

1. The six required sections (every section is required even if its
   inner lists are empty).
2. Required-field guards on the supporting models.
3. ``extra="forbid"`` on every model.
4. ``Confidence`` is shared with :mod:`research_models`.
5. ``LabMaturity`` is shared with :mod:`needs_models`.
6. ``source_index`` / ``source_indices`` range validation:
   - non-negative at field-validation time
   - in-range at model-validation time
   - applies to BriefingClaim, BriefingContact, and RankedNeed
   - covers every section that holds claims
7. ``RankedNeed.priority`` is ``ge=1`` and ties are tolerated.
8. ``user_context`` defaults to ``None`` (the approval screen writes
   the operator-supplied value).
9. Round-trip through JSON.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.briefing_models import (
    Briefing,
    BriefingClaim,
    BriefingContact,
    BusinessContext,
    Confidence,
    ItLandscape,
    KeyPeople,
    LabMaturity,
    Opportunity,
    RankedNeed,
    Snapshot,
    SourceEntry,
    SourceRegister,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid payloads
# ---------------------------------------------------------------------------

def _source_entry(**overrides) -> SourceEntry:
    base = {
        "url": "https://acme.example.com/news/cloud",
        "title": "Acme migrates to cloud",
        "retrieved_at": date(2026, 5, 27),
        "confidence": Confidence.HIGH,
    }
    base.update(overrides)
    return SourceEntry(**base)


def _claim(**overrides) -> BriefingClaim:
    base = {
        "summary": "Acme is migrating workloads to the cloud.",
        "detail": "Press release confirms a 24-month migration "
        "programme covering all core systems.",
        "confidence": Confidence.HIGH,
        "source_indices": [0],
    }
    base.update(overrides)
    return BriefingClaim(**base)


def _contact(**overrides) -> BriefingContact:
    base = {
        "name": "Alice Example",
        "job_title": "CTO",
        "function": "CTO",
        "seniority": "C_LEVEL",
        "confidence": Confidence.HIGH,
        "source_index": 0,
    }
    base.update(overrides)
    return BriefingContact(**base)


def _ranked_need(**overrides) -> RankedNeed:
    base = {
        "priority": 1,
        "summary": "Modernise SD-WAN footprint",
        "detail": "Press releases and job postings indicate a stalled "
        "SD-WAN rollout that ANS could accelerate.",
        "suggested_products": ["SD-WAN replacement"],
        "entry_angle": "Lead with the SD-WAN refresh angle; cite the "
        "open Head of SD-WAN posting as evidence.",
        "watch_outs": ["Existing incumbent has a 3-year contract."],
        "confidence": Confidence.HIGH,
        "source_indices": [0],
    }
    base.update(overrides)
    return RankedNeed(**base)


def _snapshot(**overrides) -> Snapshot:
    base = {
        "headline": "Acme Ltd — UK mid-market manufacturer migrating "
        "to the cloud",
        "one_line_desc": "Industrial controls manufacturer based in "
        "Manchester.",
        "sector": "Industrial manufacturing",
        "headcount_band": "1000-5000",
        "hq_country": "United Kingdom",
        "ownership": "Private",
        "lab_maturity": LabMaturity.MODERNISATION_IN_PROGRESS,
        "why_interesting_to_ans": "Active cloud migration + open "
        "senior network roles align with ANS's SD-WAN proposition.",
        "confidence": Confidence.HIGH,
    }
    base.update(overrides)
    return Snapshot(**base)


def _it_landscape(**overrides) -> ItLandscape:
    base = {
        "lab_maturity_reasoning": "Active SD-WAN job postings plus a "
        "recent zero-trust vendor case study indicate an in-flight "
        "modernisation programme.",
    }
    base.update(overrides)
    return ItLandscape(**base)


def _opportunity(**overrides) -> Opportunity:
    base = {
        "ranked_needs": [],
        "buying_cycle_stage": "Evaluating",
        "recommended_angle": "Lead with the SD-WAN refresh angle.",
        "watch_outs": [],
    }
    base.update(overrides)
    return Opportunity(**base)


def _briefing(
    *,
    sources: SourceRegister | None = None,
    business_context: BusinessContext | None = None,
    it_landscape: ItLandscape | None = None,
    key_people: KeyPeople | None = None,
    opportunity: Opportunity | None = None,
) -> Briefing:
    """A populated, valid :class:`Briefing` with one source entry."""
    if sources is None:
        sources = SourceRegister(entries=[_source_entry()], gaps=[])
    return Briefing(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        compiled_at=date(2026, 5, 27),
        snapshot=_snapshot(),
        business_context=business_context or BusinessContext(),
        it_landscape=it_landscape or _it_landscape(),
        key_people=key_people or KeyPeople(),
        opportunity=opportunity or _opportunity(),
        sources=sources,
    )


_REQUIRED_BRIEFING_FIELDS: tuple[str, ...] = (
    "company_name",
    "company_url",
    "compiled_at",
    "snapshot",
    "business_context",
    "it_landscape",
    "key_people",
    "opportunity",
    "sources",
)


# ---------------------------------------------------------------------------
# 1. Six sections — all required at the top level
# ---------------------------------------------------------------------------

def test_minimum_briefing_validates() -> None:
    briefing = _briefing()
    assert briefing.company_name == "Acme Ltd"
    assert briefing.snapshot.lab_maturity is LabMaturity.MODERNISATION_IN_PROGRESS
    assert briefing.user_context is None  # default


@pytest.mark.parametrize("field", _REQUIRED_BRIEFING_FIELDS)
def test_required_top_level_field_omitted_raises(field: str) -> None:
    payload: dict[str, Any] = {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "compiled_at": date(2026, 5, 27),
        "snapshot": _snapshot(),
        "business_context": BusinessContext(),
        "it_landscape": _it_landscape(),
        "key_people": KeyPeople(),
        "opportunity": _opportunity(),
        "sources": SourceRegister(entries=[_source_entry()]),
    }
    del payload[field]
    with pytest.raises(ValidationError):
        Briefing(**payload)


def test_briefing_has_all_six_named_sections() -> None:
    """Pin the exact set of section attribute names — a rename here
    silently breaks the Stage 2 writers and the briefing template."""
    expected = {
        "snapshot",
        "business_context",
        "it_landscape",
        "key_people",
        "opportunity",
        "sources",
    }
    fields = set(Briefing.model_fields.keys())
    assert expected.issubset(fields)


# ---------------------------------------------------------------------------
# 2. Required-field guards on supporting models
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field",
    [
        "headline",
        "one_line_desc",
        "lab_maturity",
        "why_interesting_to_ans",
        "confidence",
    ],
)
def test_snapshot_required_field_omitted_raises(field: str) -> None:
    payload = {
        "headline": "x",
        "one_line_desc": "y",
        "lab_maturity": LabMaturity.MATURE,
        "why_interesting_to_ans": "z",
        "confidence": Confidence.MEDIUM,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        Snapshot(**payload)


def test_it_landscape_requires_lab_maturity_reasoning() -> None:
    with pytest.raises(ValidationError):
        ItLandscape()  # type: ignore[call-arg]


def test_opportunity_requires_recommended_angle() -> None:
    with pytest.raises(ValidationError):
        Opportunity()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field",
    ["summary", "detail", "confidence"],
)
def test_briefing_claim_required_field_omitted_raises(field: str) -> None:
    payload = {
        "summary": "x",
        "detail": "y",
        "confidence": Confidence.HIGH,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        BriefingClaim(**payload)


@pytest.mark.parametrize(
    "field",
    ["name", "function", "confidence"],
)
def test_briefing_contact_required_field_omitted_raises(field: str) -> None:
    payload = {
        "name": "Alice",
        "function": "CTO",
        "confidence": Confidence.HIGH,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        BriefingContact(**payload)


@pytest.mark.parametrize(
    "field",
    [
        "priority",
        "summary",
        "detail",
        "entry_angle",
        "confidence",
    ],
)
def test_ranked_need_required_field_omitted_raises(field: str) -> None:
    payload = {
        "priority": 1,
        "summary": "x",
        "detail": "y",
        "entry_angle": "z",
        "confidence": Confidence.HIGH,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        RankedNeed(**payload)


@pytest.mark.parametrize(
    "field",
    ["url", "retrieved_at", "confidence"],
)
def test_source_entry_required_field_omitted_raises(field: str) -> None:
    payload = {
        "url": "https://acme.example.com/",
        "retrieved_at": date(2026, 5, 27),
        "confidence": Confidence.HIGH,
    }
    del payload[field]
    with pytest.raises(ValidationError):
        SourceEntry(**payload)


# ---------------------------------------------------------------------------
# 3. extra="forbid" on every model
# ---------------------------------------------------------------------------

def test_unexpected_field_on_briefing_rejected() -> None:
    base = _briefing()
    payload = base.model_dump(mode="python")
    payload["unknown"] = "oops"
    with pytest.raises(ValidationError):
        Briefing(**payload)


def test_unexpected_field_on_snapshot_rejected() -> None:
    with pytest.raises(ValidationError):
        Snapshot(
            headline="x",
            one_line_desc="y",
            lab_maturity=LabMaturity.MATURE,
            why_interesting_to_ans="z",
            confidence=Confidence.HIGH,
            unknown="oops",  # type: ignore[arg-type]
        )


def test_unexpected_field_on_business_context_rejected() -> None:
    with pytest.raises(ValidationError):
        BusinessContext(unknown="oops")  # type: ignore[call-arg]


def test_unexpected_field_on_it_landscape_rejected() -> None:
    with pytest.raises(ValidationError):
        ItLandscape(
            lab_maturity_reasoning="x",
            unknown="oops",  # type: ignore[call-arg]
        )


def test_unexpected_field_on_key_people_rejected() -> None:
    with pytest.raises(ValidationError):
        KeyPeople(unknown="oops")  # type: ignore[call-arg]


def test_unexpected_field_on_opportunity_rejected() -> None:
    with pytest.raises(ValidationError):
        Opportunity(
            recommended_angle="x",
            unknown="oops",  # type: ignore[call-arg]
        )


def test_unexpected_field_on_source_register_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceRegister(unknown="oops")  # type: ignore[call-arg]


def test_unexpected_field_on_briefing_claim_rejected() -> None:
    with pytest.raises(ValidationError):
        BriefingClaim(
            summary="x",
            detail="y",
            confidence=Confidence.HIGH,
            unknown="oops",  # type: ignore[call-arg]
        )


def test_unexpected_field_on_briefing_contact_rejected() -> None:
    with pytest.raises(ValidationError):
        BriefingContact(
            name="x",
            function="CTO",
            confidence=Confidence.HIGH,
            unknown="oops",  # type: ignore[call-arg]
        )


def test_unexpected_field_on_ranked_need_rejected() -> None:
    with pytest.raises(ValidationError):
        RankedNeed(
            priority=1,
            summary="x",
            detail="y",
            entry_angle="z",
            confidence=Confidence.HIGH,
            unknown="oops",  # type: ignore[call-arg]
        )


def test_unexpected_field_on_source_entry_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceEntry(
            url="https://acme.example.com/",
            retrieved_at=date(2026, 5, 27),
            confidence=Confidence.HIGH,
            unknown="oops",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# 4. Confidence is shared with research_models
# ---------------------------------------------------------------------------

def test_confidence_enum_is_shared_with_research_models() -> None:
    from backend.agents.research_models import Confidence as ResearchConfidence

    assert Confidence is ResearchConfidence


# ---------------------------------------------------------------------------
# 5. LabMaturity is shared with needs_models
# ---------------------------------------------------------------------------

def test_lab_maturity_enum_is_shared_with_needs_models() -> None:
    """The Snapshot's ``lab_maturity`` propagates the needs assessment's
    headline call verbatim. A parallel definition here would create a
    silent drift surface and break the assertion that Stage 2 writers
    branch on a single enum."""
    from backend.agents.needs_models import LabMaturity as NeedsLabMaturity

    assert LabMaturity is NeedsLabMaturity


# ---------------------------------------------------------------------------
# 6. source_index / source_indices range validation
# ---------------------------------------------------------------------------

def test_valid_source_indices_pass() -> None:
    """The control case — every index points to a real entry."""
    sources = SourceRegister(
        entries=[_source_entry(), _source_entry(url="https://acme.example.com/2")],
        gaps=[],
    )
    briefing = _briefing(
        sources=sources,
        business_context=BusinessContext(
            news=[_claim(source_indices=[0, 1])],
        ),
        key_people=KeyPeople(
            contacts=[_contact(source_index=1)],
            hiring_signals=[_claim(source_indices=[0])],
        ),
        opportunity=_opportunity(
            ranked_needs=[_ranked_need(source_indices=[0, 1])],
        ),
    )
    assert briefing.sources.entries[0].url.host == "acme.example.com"


def test_out_of_range_source_index_on_business_context_fails() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError) as exc_info:
        _briefing(
            sources=sources,
            business_context=BusinessContext(
                news=[_claim(source_indices=[1])],  # only index 0 exists
            ),
        )
    assert "out of range" in str(exc_info.value)


def test_out_of_range_source_index_on_it_landscape_fails() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError) as exc_info:
        _briefing(
            sources=sources,
            it_landscape=_it_landscape(
                vendors=[_claim(source_indices=[5])],
            ),
        )
    assert "out of range" in str(exc_info.value)


def test_out_of_range_source_index_on_key_people_contact_fails() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError) as exc_info:
        _briefing(
            sources=sources,
            key_people=KeyPeople(
                contacts=[_contact(source_index=2)],
            ),
        )
    assert "out of range" in str(exc_info.value)


def test_out_of_range_source_index_on_hiring_signals_fails() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError) as exc_info:
        _briefing(
            sources=sources,
            key_people=KeyPeople(
                hiring_signals=[_claim(source_indices=[3])],
            ),
        )
    assert "out of range" in str(exc_info.value)


def test_out_of_range_source_index_on_ranked_need_fails() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError) as exc_info:
        _briefing(
            sources=sources,
            opportunity=_opportunity(
                ranked_needs=[_ranked_need(source_indices=[7])],
            ),
        )
    assert "out of range" in str(exc_info.value)


def test_negative_source_index_on_briefing_claim_fails() -> None:
    with pytest.raises(ValidationError):
        BriefingClaim(
            summary="x",
            detail="y",
            confidence=Confidence.HIGH,
            source_indices=[-1],
        )


def test_negative_source_index_on_ranked_need_fails() -> None:
    with pytest.raises(ValidationError):
        RankedNeed(
            priority=1,
            summary="x",
            detail="y",
            entry_angle="z",
            confidence=Confidence.HIGH,
            source_indices=[-1],
        )


def test_negative_source_index_on_briefing_contact_fails() -> None:
    """``Field(ge=0)`` on ``source_index`` rejects negatives at the
    field-validation layer — before the Briefing-level range check
    runs. Pin both layers."""
    with pytest.raises(ValidationError):
        BriefingContact(
            name="x",
            function="CTO",
            confidence=Confidence.HIGH,
            source_index=-1,
        )


def test_bad_indices_are_not_silently_dropped() -> None:
    """Confirm the validation does not "fix" a bad index by dropping it
    — the test would pass if the engine silently dropped the index and
    revalidated. Asserting on the count would not be enough; we assert
    that the constructor raises."""
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    with pytest.raises(ValidationError):
        _briefing(
            sources=sources,
            business_context=BusinessContext(
                news=[
                    _claim(source_indices=[0, 99]),  # one good + one bad
                ],
            ),
        )


def test_empty_source_indices_allowed_for_inferred_claims() -> None:
    """A claim with INFERRED confidence may carry no citations. The
    schema must allow that — the prompt enforces the convention, not
    the validator."""
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    briefing = _briefing(
        sources=sources,
        business_context=BusinessContext(
            news=[
                _claim(confidence=Confidence.INFERRED, source_indices=[]),
            ],
        ),
    )
    assert briefing.business_context.news[0].source_indices == []


def test_contact_with_no_source_index_is_allowed() -> None:
    """``source_index`` is optional. The renderer treats ``None`` as
    "no citation"; the schema does not force a citation here because
    the prompt already asks for one and there are edge cases (e.g. an
    org-chart inference) where ``None`` is correct."""
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    briefing = _briefing(
        sources=sources,
        key_people=KeyPeople(
            contacts=[_contact(source_index=None)],
        ),
    )
    assert briefing.key_people.contacts[0].source_index is None


def test_empty_source_register_with_no_citations_validates() -> None:
    """A briefing with no upstream citations (every claim INFERRED, no
    contacts) is a legal — if rare — outcome. Pin so the validator
    doesn't trip on the empty list edge case."""
    sources = SourceRegister(entries=[], gaps=["no public information found"])
    briefing = _briefing(sources=sources)
    assert briefing.sources.entries == []
    assert briefing.sources.gaps == ["no public information found"]


# ---------------------------------------------------------------------------
# 7. RankedNeed.priority — ge=1, ties allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("priority", [0, -1, -50])
def test_ranked_need_priority_rejects_non_positive(priority: int) -> None:
    with pytest.raises(ValidationError):
        _ranked_need(priority=priority)


def test_ranked_need_priority_accepts_one() -> None:
    need = _ranked_need(priority=1)
    assert need.priority == 1


def test_ranked_need_priority_ties_allowed_by_schema() -> None:
    """Ties are tolerated — parity with :class:`IdentifiedNeed`."""
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    briefing = _briefing(
        sources=sources,
        opportunity=_opportunity(
            ranked_needs=[
                _ranked_need(priority=1, summary="A"),
                _ranked_need(priority=1, summary="B"),
            ],
        ),
    )
    assert [n.priority for n in briefing.opportunity.ranked_needs] == [1, 1]


# ---------------------------------------------------------------------------
# 8. user_context defaults to None
# ---------------------------------------------------------------------------

def test_user_context_defaults_to_none() -> None:
    """The agent emits ``user_context=None``; the approval screen
    writes the operator-supplied value at edit time."""
    briefing = _briefing()
    assert briefing.user_context is None


def test_user_context_round_trips_when_populated() -> None:
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    briefing = Briefing(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        compiled_at=date(2026, 5, 27),
        user_context="focus on the SD-WAN angle",
        snapshot=_snapshot(),
        business_context=BusinessContext(),
        it_landscape=_it_landscape(),
        key_people=KeyPeople(),
        opportunity=_opportunity(),
        sources=sources,
    )
    assert briefing.user_context == "focus on the SD-WAN angle"


# ---------------------------------------------------------------------------
# 9. Round-trip through JSON
# ---------------------------------------------------------------------------

def test_briefing_round_trip_through_json() -> None:
    """Stage 2 writers receive the briefing as JSON text; pin that a
    populated briefing serialises and re-validates cleanly."""
    sources = SourceRegister(entries=[_source_entry()], gaps=[])
    briefing = _briefing(
        sources=sources,
        business_context=BusinessContext(
            news=[_claim(source_indices=[0])],
        ),
        key_people=KeyPeople(
            contacts=[_contact(source_index=0)],
            hiring_signals=[_claim(source_indices=[0])],
        ),
        opportunity=_opportunity(
            ranked_needs=[_ranked_need(source_indices=[0])],
        ),
    )
    dumped = briefing.model_dump_json()
    rebuilt = Briefing.model_validate_json(dumped)
    assert rebuilt.snapshot.lab_maturity is LabMaturity.MODERNISATION_IN_PROGRESS
    assert rebuilt.business_context.news[0].source_indices == [0]
    assert rebuilt.key_people.contacts[0].source_index == 0
    assert rebuilt.opportunity.ranked_needs[0].priority == 1
