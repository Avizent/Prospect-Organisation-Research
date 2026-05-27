"""Schema tests for :mod:`backend.agents.critic_models`.

Pure Pydantic round-trip and validation checks. No agent, no fake
client — the agent's behaviour tests live in
``tests/agents/test_critic_agent.py``.

Coverage pins:

* The four closed enums (:class:`Verdict`, :class:`Severity`,
  :class:`IssueCategory`, :class:`CitedArtefact`) are closed to
  exactly the values the handover names; widening any of them
  requires a deliberate test diff
* ``Stage2CriticReport`` rejects ``extra`` fields on every nested
  model
* The cross-field ``_verdict_matches_issue_severity`` validator
  enforces all four rules:
    - ``READY`` ⇒ no ``BLOCKING`` and no ``WARNING`` issues
    - ``READY_WITH_WARNINGS`` ⇒ no ``BLOCKING`` issues; at least one
      ``WARNING`` issue
    - ``NEEDS_REVISION`` ⇒ at least one ``BLOCKING`` issue
    - A ``BLOCKING`` issue forces ``NEEDS_REVISION`` (the inverse
      direction of the previous rule, pinned separately because
      breaking it would let a confident-but-inconsistent critic
      ship)
* Empty ``description`` / ``locator`` / ``summary`` are rejected
* ``suggested_fix`` is optional and defaults to ``None``
* ``issues`` defaults to ``[]``; the empty list is legal as long as
  the verdict is ``READY``
* ``issues.max_length = 50`` is enforced (51 issues fails)
* ``gaps`` is optional and defaults to ``[]``
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from backend.agents.critic_models import (
    CitedArtefact,
    CriticIssue,
    IssueCategory,
    Severity,
    Stage2CriticReport,
    Verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(
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


def _ready_payload() -> dict[str, Any]:
    """A minimal :class:`Stage2CriticReport` with verdict READY and
    no issues. Used by tests that only care about the wrapper."""
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


def _warning_payload() -> dict[str, Any]:
    """A populated report with one WARNING issue and verdict
    READY_WITH_WARNINGS — exercises the warning rule."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "READY_WITH_WARNINGS",
        "issues": [
            _issue(
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
        ],
        "summary": (
            "Artefacts are otherwise sound; one tone slip in the FAQ "
            "register is the only flag."
        ),
        "gaps": ["No datasheet detail on Netropy 100G jitter floor."],
    }


def _needs_revision_payload() -> dict[str, Any]:
    """A populated report with one BLOCKING issue and verdict
    NEEDS_REVISION — exercises the blocking rule."""
    return {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "NEEDS_REVISION",
        "issues": [
            _issue(
                artefact="BENEFITS",
                locator="technical_fit.body[0].detail",
                severity="BLOCKING",
                category="UNSUPPORTED_PRODUCT_CLAIM",
                description=(
                    "Claim cites a Netropy capability that does not "
                    "appear in the supplied knowledge bundle."
                ),
                suggested_fix=(
                    "Drop the claim or replace with the supported "
                    "100 Gbps line-rate framing."
                ),
            ),
            _issue(
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
            "One unsupported product claim in the benefits brief "
            "must be fixed before shipping."
        ),
    }


# ---------------------------------------------------------------------------
# 1. Enum closure
# ---------------------------------------------------------------------------

def test_verdict_enum_values() -> None:
    """Verdict is closed to exactly the three handover-named states."""
    assert {member.value for member in Verdict} == {
        "READY",
        "READY_WITH_WARNINGS",
        "NEEDS_REVISION",
    }


def test_severity_enum_values() -> None:
    """Severity is closed to exactly the three weights the
    cross-field validator depends on."""
    assert {member.value for member in Severity} == {
        "BLOCKING",
        "WARNING",
        "INFO",
    }


def test_issue_category_enum_values() -> None:
    """IssueCategory is closed to exactly the seven values: four
    handover-named checks (UNSUPPORTED_COMPANY_CLAIM,
    UNSUPPORTED_PRODUCT_CLAIM, FACTUAL_INCONSISTENCY,
    OFF_BRAND_TONE) plus CITATION_REF_ISSUE, EVIDENCE_GAP, and the
    last-resort OTHER hatch."""
    assert {member.value for member in IssueCategory} == {
        "UNSUPPORTED_COMPANY_CLAIM",
        "UNSUPPORTED_PRODUCT_CLAIM",
        "FACTUAL_INCONSISTENCY",
        "OFF_BRAND_TONE",
        "CITATION_REF_ISSUE",
        "EVIDENCE_GAP",
        "OTHER",
    }


def test_cited_artefact_enum_values() -> None:
    """CitedArtefact is closed to exactly the three writer artefacts
    the critic reviews."""
    assert {member.value for member in CitedArtefact} == {
        "BENEFITS",
        "FAQ",
        "OBJECTIONS",
    }


# ---------------------------------------------------------------------------
# 2. Round-trip — minimal and populated
# ---------------------------------------------------------------------------

def test_ready_payload_validates() -> None:
    report = Stage2CriticReport.model_validate(_ready_payload())
    assert report.company_name == "Acme Ltd"
    assert report.verdict is Verdict.READY
    assert report.issues == []
    assert report.gaps == []


def test_warning_payload_validates() -> None:
    report = Stage2CriticReport.model_validate(_warning_payload())
    assert report.verdict is Verdict.READY_WITH_WARNINGS
    assert len(report.issues) == 1
    assert report.issues[0].severity is Severity.WARNING
    assert report.issues[0].artefact is CitedArtefact.FAQ


def test_needs_revision_payload_validates() -> None:
    report = Stage2CriticReport.model_validate(_needs_revision_payload())
    assert report.verdict is Verdict.NEEDS_REVISION
    assert len(report.issues) == 2
    assert any(
        i.severity is Severity.BLOCKING for i in report.issues
    )


def test_populated_payload_serialises_and_revalidates() -> None:
    report = Stage2CriticReport.model_validate(_needs_revision_payload())
    json_text = report.model_dump_json()
    again = Stage2CriticReport.model_validate_json(json_text)
    assert again == report


def test_issues_defaults_to_empty_list() -> None:
    payload = _ready_payload()
    payload.pop("issues")
    report = Stage2CriticReport.model_validate(payload)
    assert report.issues == []


def test_gaps_defaults_to_empty_list() -> None:
    payload = _ready_payload()
    assert "gaps" not in payload
    report = Stage2CriticReport.model_validate(payload)
    assert report.gaps == []


def test_non_empty_gaps_accepted() -> None:
    payload = _ready_payload()
    payload["gaps"] = [
        "Could not verify Netropy 100G jitter floor without datasheet.",
        "Competitive landscape entry for Spirent is thin.",
    ]
    report = Stage2CriticReport.model_validate(payload)
    assert len(report.gaps) == 2


# ---------------------------------------------------------------------------
# 3. extra="forbid" — every nested model
# ---------------------------------------------------------------------------

def test_extra_field_on_report_rejected() -> None:
    payload = _ready_payload()
    payload["surprise"] = "nope"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_extra_field_on_issue_rejected() -> None:
    payload = _warning_payload()
    payload["issues"][0]["bogus"] = True
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


# ---------------------------------------------------------------------------
# 4. Closed-enum rejections (end-to-end through the report)
# ---------------------------------------------------------------------------

def test_invalid_verdict_rejected() -> None:
    """A hallucinated verdict (e.g. ``MAYBE_READY``) is the schema's
    primary self-fence. Reject at validation time so the retry loop
    fires before the bad value reaches any consumer."""
    payload = _ready_payload()
    payload["verdict"] = "MAYBE_READY"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_invalid_severity_rejected() -> None:
    payload = _warning_payload()
    payload["issues"][0]["severity"] = "CRITICAL"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_invalid_category_rejected() -> None:
    payload = _warning_payload()
    payload["issues"][0]["category"] = "MARKETING_PUFFERY"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_invalid_artefact_rejected() -> None:
    payload = _warning_payload()
    payload["issues"][0]["artefact"] = "CASE_STUDIES"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


# ---------------------------------------------------------------------------
# 5. Cross-field verdict/severity validator — four rules
# ---------------------------------------------------------------------------

def test_ready_with_warning_issue_rejected() -> None:
    """Rule: ``READY`` does not allow ``WARNING`` issues. A model that
    raises a warning but stamps READY must fail parse so the retry
    loop fires."""
    payload = _warning_payload()
    payload["verdict"] = "READY"
    with pytest.raises(ValidationError) as exc_info:
        Stage2CriticReport.model_validate(payload)
    assert "READY" in str(exc_info.value)


def test_ready_with_blocking_issue_rejected() -> None:
    """Rule: a ``BLOCKING`` issue forces ``NEEDS_REVISION``. A model
    that emits a blocking issue but stamps READY must fail parse."""
    payload = _needs_revision_payload()
    payload["verdict"] = "READY"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_ready_with_warnings_without_warning_rejected() -> None:
    """Rule: ``READY_WITH_WARNINGS`` requires at least one
    ``WARNING`` issue. Stamping the verdict with no warnings is
    nonsense — fail parse."""
    payload = _ready_payload()
    payload["verdict"] = "READY_WITH_WARNINGS"
    with pytest.raises(ValidationError) as exc_info:
        Stage2CriticReport.model_validate(payload)
    assert "READY_WITH_WARNINGS" in str(exc_info.value)


def test_ready_with_warnings_with_blocking_rejected() -> None:
    """Rule: ``READY_WITH_WARNINGS`` forbids ``BLOCKING`` issues. A
    model that emits a blocking issue but stamps
    READY_WITH_WARNINGS must fail parse — the blocking-forces-
    needs-revision rule covers this from the other direction too."""
    payload = _needs_revision_payload()
    payload["verdict"] = "READY_WITH_WARNINGS"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_needs_revision_without_blocking_rejected() -> None:
    """Rule: ``NEEDS_REVISION`` requires at least one ``BLOCKING``
    issue. A model that escalates the verdict on warnings alone is
    overreacting — fail parse so the operator sees a consistent
    artefact."""
    payload = _warning_payload()
    payload["verdict"] = "NEEDS_REVISION"
    with pytest.raises(ValidationError) as exc_info:
        Stage2CriticReport.model_validate(payload)
    assert "NEEDS_REVISION" in str(exc_info.value)


def test_needs_revision_without_any_issues_rejected() -> None:
    """A NEEDS_REVISION verdict with an empty issues list is the
    most egregious form of the cross-field mismatch — pin it
    explicitly so it cannot regress."""
    payload = _ready_payload()
    payload["verdict"] = "NEEDS_REVISION"
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_ready_with_info_only_issues_accepted() -> None:
    """INFO issues do not affect the verdict — a READY report with
    only INFO entries is legal and must round-trip."""
    payload = _ready_payload()
    payload["issues"] = [
        _issue(
            severity="INFO",
            category="OTHER",
            description=(
                "Cosmetic note: heading capitalisation drifts in two "
                "rows of the FAQ."
            ),
        ),
    ]
    report = Stage2CriticReport.model_validate(payload)
    assert report.verdict is Verdict.READY
    assert report.issues[0].severity is Severity.INFO


# ---------------------------------------------------------------------------
# 6. Field-level min/max length and optional fields
# ---------------------------------------------------------------------------

def test_empty_summary_rejected() -> None:
    """A blank summary is useless to the operator — fail parse."""
    payload = _ready_payload()
    payload["summary"] = ""
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_empty_company_name_rejected() -> None:
    payload = _ready_payload()
    payload["company_name"] = ""
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_empty_locator_rejected() -> None:
    """A blank locator is useless to the renderer — fail parse."""
    payload = _warning_payload()
    payload["issues"][0]["locator"] = ""
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_empty_description_rejected() -> None:
    """A blank description is useless to the operator — fail parse."""
    payload = _warning_payload()
    payload["issues"][0]["description"] = ""
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


def test_suggested_fix_optional_and_defaults_to_none() -> None:
    """Not every issue carries a remediation — making suggested_fix
    optional matches reality and avoids forcing the critic to
    fabricate one."""
    payload = _warning_payload()
    payload["issues"][0].pop("suggested_fix", None)
    report = Stage2CriticReport.model_validate(payload)
    assert report.issues[0].suggested_fix is None


# ---------------------------------------------------------------------------
# 7. issues.max_length ceiling
# ---------------------------------------------------------------------------

def test_fifty_issues_accepted() -> None:
    """The handover ceiling is 50 issues (three artefacts × ~17
    cells each ≈ 51 — pin the boundary so it cannot drift)."""
    payload = _ready_payload()
    # 50 INFO-only issues keep the verdict consistent with READY.
    payload["issues"] = [_issue() for _ in range(50)]
    report = Stage2CriticReport.model_validate(payload)
    assert len(report.issues) == 50


def test_fifty_one_issues_rejected() -> None:
    """A runaway critic that emits more than 50 issues should trip
    the schema and be retried, not silently shipped."""
    payload = _ready_payload()
    payload["issues"] = [_issue() for _ in range(51)]
    with pytest.raises(ValidationError):
        Stage2CriticReport.model_validate(payload)


# ---------------------------------------------------------------------------
# 8. Direct model construction (smoke)
# ---------------------------------------------------------------------------

def test_can_construct_via_python_objects() -> None:
    """Sanity: every public model can be constructed from Python
    objects, not just JSON. Catches accidental field renames at the
    Python level."""
    issue = CriticIssue(
        artefact=CitedArtefact.BENEFITS,
        locator="executive_summary.body[0].claim",
        severity=Severity.BLOCKING,
        category=IssueCategory.UNSUPPORTED_PRODUCT_CLAIM,
        description=(
            "Claim cites a Netropy capability that does not appear "
            "in the supplied knowledge bundle."
        ),
        suggested_fix="Drop the claim or replace with supported framing.",
    )
    report = Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at="2026-05-27",  # type: ignore[arg-type]
        verdict=Verdict.NEEDS_REVISION,
        issues=[issue],
        summary="One unsupported product claim blocks shipping.",
        gaps=[],
    )
    assert report.verdict is Verdict.NEEDS_REVISION
    assert report.issues[0].artefact is CitedArtefact.BENEFITS
    assert report.issues[0].severity is Severity.BLOCKING
