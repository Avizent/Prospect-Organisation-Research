"""Schema tests for :mod:`backend.agents.objections_models`.

Pure Pydantic round-trip and validation checks. No agent, no fake
client — the agent's behaviour tests live in
``tests/agents/test_objections_writer_agent.py``.

Coverage pins:

* ``ObjectionCategory`` is closed to exactly the eight chosen values
* ``Confidence`` is reused from :mod:`backend.agents.research_models`
* ``ObjectionsRegister`` rejects ``extra`` fields on every nested
  model
* Row cardinality is 15..20 inclusive (below 15 / above 20 rejected)
* No per-category floor is enforced
* Negative ``knowledge_excerpt_refs`` / ``briefing_source_refs`` are
  rejected at both the row and the register level
* Empty refs lists are accepted
* Empty ``escalation_path`` is rejected (column 6 contract)
* ``gaps`` is optional and defaults to ``[]``
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.objections_models import (
    Confidence,
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
)
from backend.agents import research_models as research_models_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    *,
    category: str = "PRICE",
    objection: str = "Your emulators look expensive vs. open-source.",
    underlying_concern: str = (
        "Worried that procurement won't approve a six-figure lab "
        "spend when free tools exist."
    ),
    response: str = (
        "ANS Netropy emulators are line-rate and impairment-accurate; "
        "open-source tools cap out before SD-WAN aggregate bandwidth "
        "and miss the realistic-jitter contract the rollout needs."
    ),
    knowledge_excerpt_refs: list[int] | None = None,
    briefing_source_refs: list[int] | None = None,
    escalation_path: str = "Account owner brings in solutions architect.",
    confidence: str = "HIGH",
) -> dict[str, Any]:
    return {
        "category": category,
        "objection": objection,
        "underlying_concern": underlying_concern,
        "response": response,
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
    """Return *n* legal :class:`ObjectionRow` payloads. Used by the
    cardinality tests."""
    return [_row(objection=f"Stated objection #{i}.") for i in range(n)]


def _minimal_payload() -> dict[str, Any]:
    """Smallest dict that validates as :class:`ObjectionsRegister`.
    Exactly 15 rows (the schema floor) and no gaps."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "rows": _rows(15),
    }


def _populated_payload() -> dict[str, Any]:
    """A populated payload that exercises every category at least
    once and a real ``gaps`` entry. 20 rows — the schema ceiling."""
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
    # 20 rows: each category at least twice, then four more PRICE
    # rows to fill up to 20.
    rows = []
    for i in range(20):
        rows.append(
            _row(
                category=categories[i % len(categories)],
                objection=f"Stated objection #{i} ({categories[i % 8]}).",
            )
        )
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "written_at": "2026-05-27",
        "rows": rows,
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


# ---------------------------------------------------------------------------
# 1. Enum closure / Confidence re-use
# ---------------------------------------------------------------------------

def test_objection_category_enum_values() -> None:
    """ObjectionCategory is closed to exactly the eight chosen
    values."""
    assert {member.value for member in ObjectionCategory} == {
        "PRICE",
        "TIMING",
        "TECHNICAL_FIT",
        "COMPETITION",
        "AUTHORITY",
        "TRUST",
        "INTEGRATION",
        "COMPLIANCE",
    }


def test_confidence_is_shared_with_research_models() -> None:
    """Single source of truth — re-importing the enum from a parallel
    module would silently drift over time."""
    assert Confidence is research_models_module.Confidence


# ---------------------------------------------------------------------------
# 2. Round-trip — minimal and populated
# ---------------------------------------------------------------------------

def test_minimal_payload_validates() -> None:
    register = ObjectionsRegister.model_validate(_minimal_payload())
    assert register.company_name == "Acme Ltd"
    assert len(register.rows) == 15
    assert register.gaps == []


def test_populated_payload_validates_with_twenty_rows() -> None:
    register = ObjectionsRegister.model_validate(_populated_payload())
    assert len(register.rows) == 20
    assert register.rows[0].confidence is Confidence.HIGH
    assert register.gaps == [
        "No datasheet detail on Netropy 100G jitter floor."
    ]


def test_populated_payload_serialises_and_revalidates() -> None:
    register = ObjectionsRegister.model_validate(_populated_payload())
    json_text = register.model_dump_json()
    again = ObjectionsRegister.model_validate_json(json_text)
    assert again == register


def test_gaps_defaults_to_empty_list() -> None:
    payload = _populated_payload()
    payload.pop("gaps")
    register = ObjectionsRegister.model_validate(payload)
    assert register.gaps == []


def test_non_empty_gaps_accepted() -> None:
    payload = _minimal_payload()
    payload["gaps"] = [
        "No datasheet detail on Netropy 100G jitter floor.",
        "Competitive landscape entry for Spirent is thin.",
    ]
    register = ObjectionsRegister.model_validate(payload)
    assert len(register.gaps) == 2


# ---------------------------------------------------------------------------
# 3. Cardinality — 15 floor, 20 ceiling
# ---------------------------------------------------------------------------

def test_fourteen_rows_rejected() -> None:
    """14 rows is below the handover floor of 15 — schema fail so the
    writer cannot accidentally ship an under-spec XLSX."""
    payload = _minimal_payload()
    payload["rows"] = _rows(14)
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


def test_fifteen_rows_accepted() -> None:
    payload = _minimal_payload()
    payload["rows"] = _rows(15)
    register = ObjectionsRegister.model_validate(payload)
    assert len(register.rows) == 15


def test_twenty_rows_accepted() -> None:
    payload = _minimal_payload()
    payload["rows"] = _rows(20)
    register = ObjectionsRegister.model_validate(payload)
    assert len(register.rows) == 20


def test_twenty_one_rows_rejected() -> None:
    """21 rows is above the handover ceiling of 20 — schema fail so
    the writer cannot blow past the planned XLSX row budget."""
    payload = _minimal_payload()
    payload["rows"] = _rows(21)
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


# ---------------------------------------------------------------------------
# 4. extra="forbid" — every nested model
# ---------------------------------------------------------------------------

def test_extra_field_on_register_rejected() -> None:
    payload = _minimal_payload()
    payload["surprise"] = "nope"
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


def test_extra_field_on_row_rejected() -> None:
    payload = _minimal_payload()
    payload["rows"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


# ---------------------------------------------------------------------------
# 5. Closed-enum rejections
# ---------------------------------------------------------------------------

def test_invalid_category_rejected() -> None:
    """A hallucinated category (e.g. ``PRICING_AND_PROCUREMENT``) is
    the schema's primary self-fence. Reject at validation time so
    the retry loop fires before the bad value reaches the renderer."""
    payload = _minimal_payload()
    payload["rows"][0]["category"] = "PRICING_AND_PROCUREMENT"
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


def test_invalid_confidence_rejected() -> None:
    payload = _minimal_payload()
    payload["rows"][0]["confidence"] = "VERY_HIGH"
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


# ---------------------------------------------------------------------------
# 6. Refs non-negativity
# ---------------------------------------------------------------------------

def test_negative_knowledge_excerpt_ref_rejected() -> None:
    payload = _minimal_payload()
    payload["rows"][0]["knowledge_excerpt_refs"] = [-1]
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


def test_negative_briefing_source_ref_rejected() -> None:
    payload = _minimal_payload()
    payload["rows"][0]["briefing_source_refs"] = [-2]
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


def test_empty_refs_lists_accepted() -> None:
    """A response can stand on adjacent evidence without an explicit
    ref list — the writer should not be forced to invent one."""
    payload = _minimal_payload()
    payload["rows"][0]["knowledge_excerpt_refs"] = []
    payload["rows"][0]["briefing_source_refs"] = []
    register = ObjectionsRegister.model_validate(payload)
    assert register.rows[0].knowledge_excerpt_refs == []
    assert register.rows[0].briefing_source_refs == []


# ---------------------------------------------------------------------------
# 7. No per-category floor
# ---------------------------------------------------------------------------

def test_all_rows_in_one_category_is_legal() -> None:
    """The schema deliberately does NOT enforce that every category
    appears. A thin-coverage category is recorded under ``gaps``,
    not forced into the rows list — pin the absence of that floor so
    a future schema bump doesn't quietly add it."""
    payload = _minimal_payload()
    for row in payload["rows"]:
        row["category"] = "PRICE"
    register = ObjectionsRegister.model_validate(payload)
    assert all(
        r.category is ObjectionCategory.PRICE for r in register.rows
    )


# ---------------------------------------------------------------------------
# 8. Escalation-path column contract
# ---------------------------------------------------------------------------

def test_empty_escalation_path_rejected() -> None:
    """Column 6 of the XLSX is required and non-empty. An empty
    string would silently render a blank cell and break the
    deliverable contract."""
    payload = _minimal_payload()
    payload["rows"][0]["escalation_path"] = ""
    with pytest.raises(ValidationError):
        ObjectionsRegister.model_validate(payload)


# ---------------------------------------------------------------------------
# 9. Direct model construction (smoke)
# ---------------------------------------------------------------------------

def test_can_construct_via_python_objects() -> None:
    """Sanity: every public model can be constructed from Python
    objects, not just JSON. Catches accidental field renames at the
    Python level."""
    rows = [
        ObjectionRow(
            category=ObjectionCategory.PRICE,
            objection=f"Stated objection #{i}.",
            underlying_concern=(
                "Worried that procurement won't approve the spend."
            ),
            response=(
                "ANS Netropy emulators are line-rate and "
                "impairment-accurate."
            ),
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            escalation_path="Account owner brings in SA.",
            confidence=Confidence.HIGH,
        )
        for i in range(15)
    ]
    register = ObjectionsRegister(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at="2026-05-27",  # type: ignore[arg-type]
        rows=rows,
        gaps=[],
    )
    assert len(register.rows) == 15
    assert register.rows[0].category is ObjectionCategory.PRICE
    assert register.rows[0].confidence is Confidence.HIGH
