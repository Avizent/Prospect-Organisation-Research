"""Schema tests for :mod:`backend.agents.faq_models`.

Pure Pydantic round-trip and validation checks. No agent, no fake
client — the agent's behaviour tests live in
``tests/agents/test_faq_writer_agent.py``.

Coverage pins:

* ``FAQCategory`` is closed to exactly the five handover-named groups
* ``Confidence`` is reused from :mod:`backend.agents.research_models`
* ``FAQDocument`` rejects ``extra`` fields on every nested model
* Entry-count cardinality is 12..15 inclusive (below 12 / above 15
  rejected)
* No per-category floor is enforced
* Negative ``knowledge_excerpt_refs`` / ``briefing_source_refs`` are
  rejected at both the entry and the document level
* Empty refs lists are accepted (an answer can stand on adjacent
  evidence without an explicit citation list)
* ``gaps`` is optional and defaults to ``[]``
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.faq_models import (
    Confidence,
    FAQCategory,
    FAQDocument,
    FAQEntry,
)
from backend.agents import research_models as research_models_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    *,
    question: str = "What does ANS do for SD-WAN rollouts?",
    answer: str = (
        "ANS runs lab validation of planned SD-WAN topologies with "
        "realistic latency, loss, and jitter before any production "
        "change."
    ),
    category: str = "ABOUT_ANS",
    knowledge_excerpt_refs: list[int] | None = None,
    briefing_source_refs: list[int] | None = None,
    confidence: str = "HIGH",
) -> dict[str, Any]:
    return {
        "question": question,
        "answer": answer,
        "category": category,
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


def _entries(n: int) -> list[dict[str, Any]]:
    """Return *n* legal :class:`FAQEntry` payloads. Used by the
    cardinality tests."""
    return [_entry(question=f"Question {i}?") for i in range(n)]


def _minimal_payload() -> dict[str, Any]:
    """Smallest dict that validates as :class:`FAQDocument`. Exactly
    12 entries (the schema floor) and no gaps."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "entries": _entries(12),
    }


def _populated_payload() -> dict[str, Any]:
    """A populated payload that exercises every category and a real
    ``gaps`` entry. 15 entries — the schema ceiling."""
    categories = [
        "ABOUT_ANS",
        "PRODUCTS",
        "IMPLEMENTATION",
        "COMMERCIAL",
        "SUPPORT",
    ]
    entries = [
        _entry(
            question=f"Question about {cat} (#{i})?",
            category=cat,
        )
        for i, cat in enumerate(categories * 3)  # 15 entries
    ]
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "entries": entries,
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


# ---------------------------------------------------------------------------
# 1. Enum closure / Confidence re-use
# ---------------------------------------------------------------------------

def test_faq_category_enum_values() -> None:
    """FAQCategory is closed to exactly the five handover-named
    groups."""
    assert {member.value for member in FAQCategory} == {
        "ABOUT_ANS",
        "PRODUCTS",
        "IMPLEMENTATION",
        "COMMERCIAL",
        "SUPPORT",
    }


def test_confidence_is_shared_with_research_models() -> None:
    """Single source of truth — re-importing the enum from a parallel
    module would silently drift over time."""
    assert Confidence is research_models_module.Confidence


# ---------------------------------------------------------------------------
# 2. Round-trip — minimal and populated
# ---------------------------------------------------------------------------

def test_minimal_payload_validates() -> None:
    doc = FAQDocument.model_validate(_minimal_payload())
    assert doc.company_name == "Acme Ltd"
    assert len(doc.entries) == 12
    assert doc.gaps == []


def test_populated_payload_validates_with_fifteen_entries() -> None:
    doc = FAQDocument.model_validate(_populated_payload())
    assert len(doc.entries) == 15
    assert doc.entries[0].confidence is Confidence.HIGH
    assert doc.gaps == [
        "No datasheet detail on Netropy 100G jitter floor."
    ]


def test_populated_payload_serialises_and_revalidates() -> None:
    doc = FAQDocument.model_validate(_populated_payload())
    json_text = doc.model_dump_json()
    again = FAQDocument.model_validate_json(json_text)
    assert again == doc


def test_gaps_defaults_to_empty_list() -> None:
    payload = _populated_payload()
    payload.pop("gaps")
    doc = FAQDocument.model_validate(payload)
    assert doc.gaps == []


# ---------------------------------------------------------------------------
# 3. Cardinality — 12 floor, 15 ceiling
# ---------------------------------------------------------------------------

def test_eleven_entries_rejected() -> None:
    """11 entries is below the handover floor of 12 — schema fail so
    the writer cannot accidentally ship an under-spec FAQ."""
    payload = _minimal_payload()
    payload["entries"] = _entries(11)
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


def test_twelve_entries_accepted() -> None:
    payload = _minimal_payload()
    payload["entries"] = _entries(12)
    doc = FAQDocument.model_validate(payload)
    assert len(doc.entries) == 12


def test_fifteen_entries_accepted() -> None:
    payload = _minimal_payload()
    payload["entries"] = _entries(15)
    doc = FAQDocument.model_validate(payload)
    assert len(doc.entries) == 15


def test_sixteen_entries_rejected() -> None:
    """16 entries is above the handover ceiling of 15 — schema fail
    so the writer cannot blow past the PDF page budget."""
    payload = _minimal_payload()
    payload["entries"] = _entries(16)
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


# ---------------------------------------------------------------------------
# 4. extra="forbid" — every nested model
# ---------------------------------------------------------------------------

def test_extra_field_on_document_rejected() -> None:
    payload = _minimal_payload()
    payload["surprise"] = "nope"
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


def test_extra_field_on_entry_rejected() -> None:
    payload = _minimal_payload()
    payload["entries"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


# ---------------------------------------------------------------------------
# 5. Closed-enum rejections
# ---------------------------------------------------------------------------

def test_invalid_category_rejected() -> None:
    """A hallucinated category (e.g. ``PRICING``) is the schema's
    primary self-fence. Reject at validation time so the retry loop
    fires before the bad value reaches the renderer."""
    payload = _minimal_payload()
    payload["entries"][0]["category"] = "PRICING"
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


def test_invalid_confidence_rejected() -> None:
    payload = _minimal_payload()
    payload["entries"][0]["confidence"] = "VERY_HIGH"
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


# ---------------------------------------------------------------------------
# 6. Refs non-negativity
# ---------------------------------------------------------------------------

def test_negative_knowledge_excerpt_ref_rejected() -> None:
    payload = _minimal_payload()
    payload["entries"][0]["knowledge_excerpt_refs"] = [-1]
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


def test_negative_briefing_source_ref_rejected() -> None:
    payload = _minimal_payload()
    payload["entries"][0]["briefing_source_refs"] = [-2]
    with pytest.raises(ValidationError):
        FAQDocument.model_validate(payload)


def test_empty_refs_lists_accepted() -> None:
    """An answer can stand on adjacent evidence without an explicit
    ref list — the writer should not be forced to invent one."""
    payload = _minimal_payload()
    payload["entries"][0]["knowledge_excerpt_refs"] = []
    payload["entries"][0]["briefing_source_refs"] = []
    doc = FAQDocument.model_validate(payload)
    assert doc.entries[0].knowledge_excerpt_refs == []
    assert doc.entries[0].briefing_source_refs == []


# ---------------------------------------------------------------------------
# 7. No per-category floor
# ---------------------------------------------------------------------------

def test_all_entries_in_one_category_is_legal() -> None:
    """The schema deliberately does NOT enforce that every category
    appears. A thin-coverage category is recorded under ``gaps``, not
    forced into the entries list — pin the absence of that floor so
    a future schema bump doesn't quietly add it."""
    payload = _minimal_payload()
    for entry in payload["entries"]:
        entry["category"] = "ABOUT_ANS"
    doc = FAQDocument.model_validate(payload)
    assert all(
        e.category is FAQCategory.ABOUT_ANS for e in doc.entries
    )


# ---------------------------------------------------------------------------
# 8. Direct model construction (smoke)
# ---------------------------------------------------------------------------

def test_can_construct_via_python_objects() -> None:
    """Sanity: every public model can be constructed from Python
    objects, not just JSON. Catches accidental field renames at the
    Python level."""
    entries = [
        FAQEntry(
            question=f"Question {i}?",
            answer=(
                "ANS runs lab validation of planned topologies before "
                "any production change."
            ),
            category=FAQCategory.ABOUT_ANS,
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            confidence=Confidence.HIGH,
        )
        for i in range(12)
    ]
    doc = FAQDocument(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at="2026-05-27",  # type: ignore[arg-type]
        entries=entries,
        gaps=[],
    )
    assert len(doc.entries) == 12
    assert doc.entries[0].category is FAQCategory.ABOUT_ANS
    assert doc.entries[0].confidence is Confidence.HIGH
