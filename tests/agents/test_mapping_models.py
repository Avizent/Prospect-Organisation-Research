"""Schema tests for :mod:`backend.agents.mapping_models`.

Pure Pydantic round-trip and validation checks. No agent, no fake
client — the agent's behaviour tests live in
``tests/agents/test_mapping_agent.py``.

Coverage pins:

* ``ProductLine`` is closed to the two values that match
  ``ans_knowledge/products/*.md``
* ``KnowledgeSource`` is closed to the seven values that match the
  files under ``ans_knowledge/``
* ``Confidence`` is reused from :mod:`backend.agents.research_models`
* ``NeedProductMatch.products`` must be non-empty
* ``ProductMapping`` rejects ``extra`` fields on every nested model
* Out-of-range ``knowledge_excerpt_refs`` is a model-level validation
  failure
* Empty ``matches`` is a legal outcome (e.g. briefing had no ranked
  needs)
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.mapping_models import (
    Confidence,
    KnowledgeExcerptRef,
    KnowledgeSource,
    NeedProductMatch,
    ProductLine,
    ProductMapping,
    ProductReference,
    UnmatchedNeed,
)
from backend.agents import research_models as research_models_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_payload() -> dict[str, Any]:
    """Smallest dict that validates as :class:`ProductMapping`. All
    optional list fields default to ``[]``."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "mapped_at": "2026-05-27",
    }


def _populated_payload() -> dict[str, Any]:
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
                        "knowledge_excerpt_refs": [0, 1],
                    }
                ],
                "use_case_framing": (
                    "Lab-emulate the planned SD-WAN topology with "
                    "realistic latency and loss before cutover."
                ),
                "why_this_fits": (
                    "Briefing flags an active SD-WAN programme; "
                    "Netropy supports the bandwidth and impairment "
                    "ranges the design will hit."
                ),
                "confidence": "HIGH",
            }
        ],
        "unmatched_needs": [
            {
                "need_priority": 2,
                "need_summary": "Refresh SIEM tooling",
                "reason": "Outside ANS lab/network testing portfolio.",
            }
        ],
        "knowledge_excerpts": [
            {
                "source_file": "products/emulators.md",
                "heading": "Netropy Network Emulators",
                "rationale": "Bandwidth and impairment range for SD-WAN.",
            },
            {
                "source_file": "case_studies.md",
                "heading": "SD-WAN lab validation",
                "rationale": "Prior example of similar engagement.",
            },
        ],
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


# ---------------------------------------------------------------------------
# 1. Enum closure
# ---------------------------------------------------------------------------

def test_product_line_enum_values() -> None:
    """ProductLine is closed to exactly the two product files today."""
    assert {member.value for member in ProductLine} == {
        "emulators",
        "traffic_generators",
    }


def test_knowledge_source_enum_values() -> None:
    """KnowledgeSource enumerates exactly the seven files that exist
    under ans_knowledge/. Widening this enum requires adding a real
    markdown file on disk first."""
    assert {member.value for member in KnowledgeSource} == {
        "products/emulators.md",
        "products/traffic_generators.md",
        "company.md",
        "case_studies.md",
        "competitive_landscape.md",
        "known_objections.md",
        "pricing_tiers.md",
    }


def test_confidence_is_shared_with_research_models() -> None:
    """Single source of truth — re-importing the enum from a parallel
    module would silently drift over time."""
    assert Confidence is research_models_module.Confidence


# ---------------------------------------------------------------------------
# 2. Round-trip — minimal and populated
# ---------------------------------------------------------------------------

def test_minimal_payload_validates() -> None:
    mapping = ProductMapping.model_validate(_minimal_payload())
    assert mapping.company_name == "Acme Ltd"
    assert mapping.matches == []
    assert mapping.unmatched_needs == []
    assert mapping.knowledge_excerpts == []
    assert mapping.gaps == []


def test_populated_payload_round_trips() -> None:
    mapping = ProductMapping.model_validate(_populated_payload())
    assert len(mapping.matches) == 1
    match = mapping.matches[0]
    assert match.need_priority == 1
    assert match.confidence is Confidence.HIGH
    assert len(match.products) == 1
    product = match.products[0]
    assert product.product_line is ProductLine.EMULATORS
    assert product.product_name == "Netropy 100G"
    assert product.knowledge_excerpt_refs == [0, 1]
    assert len(mapping.knowledge_excerpts) == 2
    assert (
        mapping.knowledge_excerpts[0].source_file
        is KnowledgeSource.PRODUCTS_EMULATORS
    )


def test_populated_payload_serialises_and_revalidates() -> None:
    mapping = ProductMapping.model_validate(_populated_payload())
    json_text = mapping.model_dump_json()
    again = ProductMapping.model_validate_json(json_text)
    assert again == mapping


# ---------------------------------------------------------------------------
# 3. extra="forbid" — every nested model
# ---------------------------------------------------------------------------

def test_extra_field_on_product_mapping_rejected() -> None:
    payload = _minimal_payload()
    payload["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_extra_field_on_match_rejected() -> None:
    payload = _populated_payload()
    payload["matches"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_extra_field_on_product_reference_rejected() -> None:
    payload = _populated_payload()
    payload["matches"][0]["products"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_extra_field_on_unmatched_need_rejected() -> None:
    payload = _populated_payload()
    payload["unmatched_needs"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_extra_field_on_knowledge_excerpt_rejected() -> None:
    payload = _populated_payload()
    payload["knowledge_excerpts"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


# ---------------------------------------------------------------------------
# 4. Closed-enum rejections
# ---------------------------------------------------------------------------

def test_invalid_product_line_rejected() -> None:
    """A hallucinated product line outside the closed enum is the
    primary self-fence. Reject at validation time so the retry loop
    fires before the bad value reaches any downstream writer."""
    payload = _populated_payload()
    payload["matches"][0]["products"][0]["product_line"] = "firewalls"
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_invalid_knowledge_source_rejected() -> None:
    payload = _populated_payload()
    payload["knowledge_excerpts"][0]["source_file"] = "not_a_file.md"
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_invalid_confidence_rejected() -> None:
    payload = _populated_payload()
    payload["matches"][0]["confidence"] = "VERY_HIGH"
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


# ---------------------------------------------------------------------------
# 5. Range validators on knowledge_excerpt_refs
# ---------------------------------------------------------------------------

def test_out_of_range_excerpt_ref_rejected() -> None:
    """A knowledge_excerpt_refs value past the end of
    ``knowledge_excerpts`` is a model-level validation failure."""
    payload = _populated_payload()
    # knowledge_excerpts has length 2, so index 2 is out of range.
    payload["matches"][0]["products"][0]["knowledge_excerpt_refs"] = [0, 2]
    with pytest.raises(ValidationError) as exc_info:
        ProductMapping.model_validate(payload)
    assert "out of range" in str(exc_info.value)


def test_negative_excerpt_ref_rejected() -> None:
    payload = _populated_payload()
    payload["matches"][0]["products"][0]["knowledge_excerpt_refs"] = [-1]
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_in_range_excerpt_refs_accepted() -> None:
    payload = _populated_payload()
    # Length is 2 → valid indices are 0 and 1.
    payload["matches"][0]["products"][0]["knowledge_excerpt_refs"] = [0, 1]
    mapping = ProductMapping.model_validate(payload)
    assert mapping.matches[0].products[0].knowledge_excerpt_refs == [0, 1]


def test_empty_excerpt_refs_accepted() -> None:
    payload = _populated_payload()
    payload["matches"][0]["products"][0]["knowledge_excerpt_refs"] = []
    mapping = ProductMapping.model_validate(payload)
    assert mapping.matches[0].products[0].knowledge_excerpt_refs == []


# ---------------------------------------------------------------------------
# 6. Cardinality
# ---------------------------------------------------------------------------

def test_match_must_have_at_least_one_product() -> None:
    """An empty ``products`` list on a NeedProductMatch is illegal — it
    would be an :class:`UnmatchedNeed`, not a match."""
    payload = _populated_payload()
    payload["matches"][0]["products"] = []
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_empty_matches_is_a_legal_outcome() -> None:
    """A briefing with no ranked needs yields an empty matches list.
    Don't force the model to invent matches just to satisfy schema."""
    payload = _populated_payload()
    payload["matches"] = []
    # If matches is empty, knowledge_excerpts can stay populated or
    # empty — both are legal.
    mapping = ProductMapping.model_validate(payload)
    assert mapping.matches == []


def test_need_priority_must_be_positive() -> None:
    payload = _populated_payload()
    payload["matches"][0]["need_priority"] = 0
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


def test_unmatched_need_priority_must_be_positive() -> None:
    payload = _populated_payload()
    payload["unmatched_needs"][0]["need_priority"] = 0
    with pytest.raises(ValidationError):
        ProductMapping.model_validate(payload)


# ---------------------------------------------------------------------------
# 7. Direct model construction (smoke)
# ---------------------------------------------------------------------------

def test_can_construct_via_python_objects() -> None:
    """Sanity: every public model can be constructed from Python
    objects, not just JSON. Catches accidental field renames at the
    Python level."""
    ref = KnowledgeExcerptRef(
        source_file=KnowledgeSource.PRODUCTS_EMULATORS,
        heading="Netropy",
        rationale="Datasheet for emulator throughput.",
    )
    product = ProductReference(
        product_line=ProductLine.EMULATORS,
        product_name="Netropy 100G",
        knowledge_excerpt_refs=[0],
    )
    match = NeedProductMatch(
        need_priority=1,
        need_summary="De-risk SD-WAN rollout",
        products=[product],
        use_case_framing="Emulate planned topology before cutover.",
        why_this_fits="Briefing flags an active SD-WAN programme.",
        confidence=Confidence.HIGH,
    )
    unmatched = UnmatchedNeed(
        need_priority=2,
        need_summary="Refresh SIEM tooling",
        reason="Outside ANS portfolio.",
    )
    mapping = ProductMapping(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        mapped_at="2026-05-27",
        matches=[match],
        unmatched_needs=[unmatched],
        knowledge_excerpts=[ref],
        gaps=[],
    )
    assert mapping.matches[0].products[0].product_line is ProductLine.EMULATORS
    assert mapping.knowledge_excerpts[0].source_file is (
        KnowledgeSource.PRODUCTS_EMULATORS
    )
