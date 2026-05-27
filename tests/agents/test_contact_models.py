"""Schema-validation tests for :mod:`backend.agents.contact_models`.

The schema is the GDPR backstop: even if a future prompt drift lets the
model emit something dubious, the Pydantic layer catches it. These
tests pin the load-bearing constraints so a refactor cannot quietly
relax them.

Five areas of coverage:
1. ``ContactCandidate`` required-field guards (every load-bearing field
   raises on omit).
2. ``extra="forbid"`` on both models — silent typos must not slide
   through.
3. ``Function`` enum is exactly the six handover §15 values.
4. The ``Confidence`` enum is **the same object** as
   :class:`backend.agents.research_models.Confidence` — drift between
   the two would split the meaning of "HIGH" across schemas.
5. The email validator rejects the canonical personal-domain providers
   case-insensitively, and accepts business addresses unchanged.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.agents.contact_models import (
    Confidence,
    ContactCandidate,
    ContactExtractionResult,
    Function,
    PERSONAL_EMAIL_DOMAINS,
    Seniority,
)


# Minimal kwargs every required field needs. Tests start from this and
# override only the field under inspection.
_VALID_CANDIDATE: dict = {
    "name": "Alice Example",
    "function": Function.CIO,
    "source_url": "https://acme.example.com/news/cio",
    "retrieved_at": date(2026, 5, 27),
    "confidence": Confidence.HIGH,
}


# ---------------------------------------------------------------------------
# 1. Required-field guards
# ---------------------------------------------------------------------------

def test_minimum_required_fields_validate() -> None:
    contact = ContactCandidate(**_VALID_CANDIDATE)
    assert contact.name == "Alice Example"
    assert contact.function is Function.CIO
    assert contact.confidence is Confidence.HIGH
    # Optional fields default to None — they are not silently coerced.
    assert contact.email is None
    assert contact.job_title is None
    assert contact.seniority is None
    assert contact.linkedin_url is None
    assert contact.notes is None


@pytest.mark.parametrize(
    "field",
    ["name", "function", "source_url", "retrieved_at", "confidence"],
)
def test_required_field_omitted_raises(field: str) -> None:
    payload = dict(_VALID_CANDIDATE)
    del payload[field]
    with pytest.raises(ValidationError):
        ContactCandidate(**payload)


def test_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(**{**_VALID_CANDIDATE, "name": ""})


def test_source_url_must_be_a_url() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(**{**_VALID_CANDIDATE, "source_url": "not-a-url"})


def test_retrieved_at_must_be_a_date() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(
            **{**_VALID_CANDIDATE, "retrieved_at": "not-a-date"}
        )


# ---------------------------------------------------------------------------
# 2. extra="forbid"
# ---------------------------------------------------------------------------

def test_unexpected_field_on_candidate_rejected() -> None:
    """Silent typos (or fabricated compliance fields like ``age``) must
    raise — Pydantic's ``extra="forbid"`` is the backstop."""
    with pytest.raises(ValidationError):
        ContactCandidate(
            **_VALID_CANDIDATE,
            age=28,  # explicitly not part of the schema
        )


def test_unexpected_field_on_extraction_result_rejected() -> None:
    with pytest.raises(ValidationError):
        ContactExtractionResult(
            company_name="Acme Ltd",
            contacts=[],
            gaps=[],
            extra_field="oops",
        )


# ---------------------------------------------------------------------------
# 3. Function enum
# ---------------------------------------------------------------------------

def test_function_enum_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(**{**_VALID_CANDIDATE, "function": "MARKETING"})


@pytest.mark.parametrize(
    "value",
    [
        "NETWORK",
        "INFRASTRUCTURE",
        "IT_EXECUTIVE",
        "SECURITY_EXECUTIVE",
        "CTO",
        "CIO",
    ],
)
def test_all_six_handover_functions_accepted(value: str) -> None:
    contact = ContactCandidate(**{**_VALID_CANDIDATE, "function": value})
    assert contact.function.value == value


def test_function_enum_exactly_six_values() -> None:
    """A seventh value sliding in would expand the compliance perimeter
    silently — pin the set."""
    assert {member.value for member in Function} == {
        "NETWORK",
        "INFRASTRUCTURE",
        "IT_EXECUTIVE",
        "SECURITY_EXECUTIVE",
        "CTO",
        "CIO",
    }


# ---------------------------------------------------------------------------
# 4. Confidence enum is shared with research_models
# ---------------------------------------------------------------------------

def test_confidence_enum_is_shared_with_research_models() -> None:
    """The dossier's Confidence and the contact extraction's Confidence
    are the same object — re-importing the symbol must not silently
    create a parallel enum that means subtly different things."""
    from backend.agents.research_models import Confidence as ResearchConfidence

    assert Confidence is ResearchConfidence


# ---------------------------------------------------------------------------
# 5. Email validator — personal-domain block list
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "address",
    [
        "alice@gmail.com",
        "Alice@GMAIL.com",  # case-insensitive
        "bob@hotmail.co.uk",
        "carol@yahoo.com",
        "dan@outlook.com",
        "eve@icloud.com",
        "frank@aol.com",
        "grace@protonmail.com",
        "heidi@proton.me",
        "ivan@live.com",
        "judy@gmx.de",
    ],
)
def test_personal_email_domains_rejected(address: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ContactCandidate(**{**_VALID_CANDIDATE, "email": address})
    # The error message names "personal email" so an operator reading
    # the log can see why the row was rejected without leaking the
    # address itself.
    assert "personal email" in str(exc_info.value).lower()


def test_business_email_accepted() -> None:
    contact = ContactCandidate(
        **{**_VALID_CANDIDATE, "email": "alice@acme.example.com"}
    )
    assert contact.email == "alice@acme.example.com"


def test_business_email_is_stripped() -> None:
    contact = ContactCandidate(
        **{**_VALID_CANDIDATE, "email": "  alice@acme.example.com  "}
    )
    assert contact.email == "alice@acme.example.com"


def test_empty_email_string_treated_as_none() -> None:
    contact = ContactCandidate(**{**_VALID_CANDIDATE, "email": "   "})
    assert contact.email is None


def test_email_without_at_sign_rejected() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(**{**_VALID_CANDIDATE, "email": "alice-at-acme"})


def test_email_with_empty_local_part_rejected() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(**{**_VALID_CANDIDATE, "email": "@acme.example.com"})


def test_personal_email_domains_set_includes_handover_examples() -> None:
    """Handover §15 names gmail, hotmail, yahoo as canonical examples.
    Pin that the constant covers all three so a future trim of the
    list cannot silently drop a load-bearing entry."""
    for required in ("gmail.com", "hotmail.com", "yahoo.com"):
        assert required in PERSONAL_EMAIL_DOMAINS


# ---------------------------------------------------------------------------
# Seniority enum sanity
# ---------------------------------------------------------------------------

def test_seniority_enum_optional_on_candidate() -> None:
    """``seniority`` may be omitted entirely — the prompt instructs the
    model to use ``null`` rather than guess. Pin that the schema
    allows this."""
    contact = ContactCandidate(**_VALID_CANDIDATE)
    assert contact.seniority is None


def test_seniority_enum_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ContactCandidate(
            **{**_VALID_CANDIDATE, "seniority": "ARCHDUKE"}
        )


@pytest.mark.parametrize(
    "value",
    [
        "C_LEVEL",
        "VP",
        "DIRECTOR",
        "HEAD",
        "MANAGER",
        "INDIVIDUAL_CONTRIBUTOR",
    ],
)
def test_seniority_enum_accepts_each_value(value: str) -> None:
    contact = ContactCandidate(**{**_VALID_CANDIDATE, "seniority": value})
    assert contact.seniority is Seniority(value)


# ---------------------------------------------------------------------------
# ContactExtractionResult
# ---------------------------------------------------------------------------

def test_extraction_result_empty_contacts_is_valid() -> None:
    """An extraction that found nothing is a legal outcome — surface
    via ``gaps`` rather than raising."""
    result = ContactExtractionResult(
        company_name="Acme Ltd",
        contacts=[],
        gaps=["no public network leadership named"],
    )
    assert result.contacts == []
    assert result.gaps == ["no public network leadership named"]


def test_extraction_result_with_a_candidate_round_trips() -> None:
    candidate = ContactCandidate(**_VALID_CANDIDATE)
    result = ContactExtractionResult(
        company_name="Acme Ltd",
        contacts=[candidate],
    )
    dumped = result.model_dump_json()
    rebuilt = ContactExtractionResult.model_validate_json(dumped)
    assert rebuilt.contacts[0].name == "Alice Example"
    assert rebuilt.contacts[0].function is Function.CIO
    assert rebuilt.contacts[0].confidence is Confidence.HIGH
    assert rebuilt.gaps == []


def test_extraction_result_company_name_required() -> None:
    with pytest.raises(ValidationError):
        ContactExtractionResult(contacts=[], gaps=[])


def test_extraction_result_empty_company_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ContactExtractionResult(company_name="", contacts=[], gaps=[])
