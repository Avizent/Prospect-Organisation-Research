"""Schema-level tests for :mod:`backend.agents.research_models`.

The agent's Pydantic output model is the contract between Stage 1
research and every downstream agent. These tests pin the parts that
are easy to get wrong by accident:

* every Finding and Source requires an explicit ``confidence``
* the four :class:`Confidence` values are accepted; arbitrary strings
  are rejected
* the named eight-section dossier layout (no ad-hoc string tags)
* minimum payload validates (so the agent can legitimately return an
  empty-but-attributed dossier)
* unknown top-level fields are rejected (``extra="forbid"``)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.research_models import (
    Confidence,
    Finding,
    ResearchDossier,
    Source,
)


# ---------------------------------------------------------------------------
# Confidence enum
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["HIGH", "MEDIUM", "LOW", "INFERRED"])
def test_confidence_accepts_each_documented_value(value: str) -> None:
    assert Confidence(value).value == value


def test_confidence_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        Confidence("MAYBE")


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

def test_source_requires_confidence() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Source.model_validate({
            "url": "https://example.com/",
            "title": "Annual report",
            "retrieved_at": "2026-05-27",
        })
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("confidence",) for err in errors)


def test_source_validates_with_all_required_fields() -> None:
    src = Source.model_validate({
        "url": "https://example.com/report.pdf",
        "title": "Annual report 2025",
        "retrieved_at": "2026-05-27",
        "confidence": "HIGH",
    })
    assert src.confidence is Confidence.HIGH
    assert src.title == "Annual report 2025"


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

def test_finding_requires_confidence() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Finding.model_validate({
            "summary": "Senior CISO appointed",
            "detail": "Press release dated 2026-04-01.",
            "sources": [],
        })
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("confidence",) for err in errors)


def test_finding_accepts_inferred_confidence() -> None:
    finding = Finding.model_validate({
        "summary": "Likely IT modernisation programme",
        "detail": "Multiple senior infra hires plus a vendor RFP suggest "
                  "an active modernisation, although not announced.",
        "confidence": "INFERRED",
        "sources": [],
    })
    assert finding.confidence is Confidence.INFERRED


# ---------------------------------------------------------------------------
# ResearchDossier
# ---------------------------------------------------------------------------

def test_minimum_dossier_payload_validates() -> None:
    """An agent that finds nothing in any section must still be able to
    return a structurally-valid dossier (with ``gaps`` populated)."""
    dossier = ResearchDossier.model_validate({
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "retrieved_at": "2026-05-27",
    })
    assert dossier.job_postings == []
    assert dossier.gaps == []


def test_dossier_rejects_missing_company_name() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ResearchDossier.model_validate({
            "company_url": "https://acme.example.com/",
            "retrieved_at": "2026-05-27",
        })
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("company_name",) for err in errors)


def test_dossier_rejects_unknown_top_level_field() -> None:
    """``extra="forbid"`` prevents a model from inventing an extra
    section (e.g. ``competitor_intel``) that no downstream agent reads."""
    with pytest.raises(ValidationError) as exc_info:
        ResearchDossier.model_validate({
            "company_name": "Acme Ltd",
            "company_url": "https://acme.example.com/",
            "retrieved_at": "2026-05-27",
            "competitor_intel": [],
        })
    errors = exc_info.value.errors()
    assert any(err["type"] == "extra_forbidden" for err in errors)


def test_dossier_has_all_eight_named_sections() -> None:
    """The named-section layout is part of the cross-agent contract.
    A renaming would silently break downstream agents — pin the names."""
    expected = {
        "job_postings",
        "annual_reports",
        "regulatory_exposure",
        "vendor_footprint",
        "m_and_a",
        "network_incidents",
        "senior_hires",
        "other",
    }
    assert expected.issubset(ResearchDossier.model_fields.keys())


def test_dossier_populated_sections_round_trip() -> None:
    payload = {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "retrieved_at": "2026-05-27",
        "senior_hires": [
            {
                "summary": "New CIO appointed April 2026",
                "detail": "Press release on company website confirms hire.",
                "confidence": "HIGH",
                "sources": [
                    {
                        "url": "https://acme.example.com/news/cio",
                        "title": "Acme Ltd appoints new CIO",
                        "retrieved_at": "2026-05-27",
                        "confidence": "HIGH",
                    }
                ],
            }
        ],
        "gaps": ["no public information on regulatory exposure"],
    }
    dossier = ResearchDossier.model_validate(payload)
    assert len(dossier.senior_hires) == 1
    assert dossier.senior_hires[0].sources[0].confidence is Confidence.HIGH
    assert dossier.gaps == ["no public information on regulatory exposure"]
