"""Tests for the five Stage 2 artefact GET routes.

* ``GET /api/jobs/{job_id}/artefacts/product-mapping``
* ``GET /api/jobs/{job_id}/artefacts/benefits``
* ``GET /api/jobs/{job_id}/artefacts/faq``
* ``GET /api/jobs/{job_id}/artefacts/objections``
* ``GET /api/jobs/{job_id}/artefacts/critic-report``

Same error contract as the four Stage 1 routes in
``test_artefact_reads.py``:

* artefact absent on disk          → 404
* artefact present, payload OK      → 200 + raw ``model_dump(mode="json")``
* artefact corrupt (bad JSON / schema-invalid) → 500
* job_id is not a UUID              → 404

URL/filename naming
-------------------
The URL uses kebab-case (``critic-report``); the on-disk file uses
snake-case (``critic_report.json``). The 404-on-non-UUID and the
500-on-schema-invalid tests pin both the URL convention and the
exception → status mapping.

Critic-specific test
--------------------
The :class:`Stage2CriticReport` verdict/severity cross-field validator
runs on :meth:`model_validate`. A hand-edit that pairs ``verdict =
READY`` with a ``BLOCKING`` issue is logically self-contradictory and
must surface as 500 on read — *not* a 200 that misleads a downstream
consumer about whether the writer artefacts are shippable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Pairs of (URL segment, on-disk filename, expected top-level field) —
# the top-level field is just a cheap sanity check that we're reading
# the right artefact back, not a strict-shape assertion.
_STAGE2_ARTEFACTS = [
    ("product-mapping", "product_mapping.json", "matches"),
    ("benefits", "benefits.json", "executive_summary"),
    ("faq", "faq.json", "entries"),
    ("objections", "objections.json", "rows"),
    ("critic-report", "critic_report.json", "verdict"),
]


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_present_returns_payload(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """Each Stage 2 route returns the raw payload after seeding."""
    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/artefacts/{url_segment}"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert expected_top_field in body
    # Identity smoke check — fixtures all carry the same company.
    assert body.get("company_name") == "Acme Ltd"


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_absent_returns_404(
    authed_client: TestClient,
    created_job: str,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """A freshly-created job has no Stage 2 artefacts; every read 404s."""
    r = authed_client.get(
        f"/api/jobs/{created_job}/artefacts/{url_segment}"
    )
    assert r.status_code == 404, r.text


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_unparsable_returns_500(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    isolated_jobs_root: Path,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """A file that isn't JSON at all surfaces as 500."""
    artefact_path = (
        isolated_jobs_root
        / job_with_all_stage2_artefacts
        / filename
    )
    artefact_path.write_text("not json at all", encoding="utf-8")
    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/artefacts/{url_segment}"
    )
    assert r.status_code == 500, r.text
    assert "corrupt" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_schema_invalid_returns_500(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    isolated_jobs_root: Path,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """Valid JSON, wrong shape → Pydantic ValidationError → 500."""
    artefact_path = (
        isolated_jobs_root
        / job_with_all_stage2_artefacts
        / filename
    )
    artefact_path.write_text(
        json.dumps({"company_name": "Acme Ltd"}), encoding="utf-8"
    )
    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/artefacts/{url_segment}"
    )
    assert r.status_code == 500, r.text
    assert "corrupt" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_unknown_job_returns_404(
    authed_client: TestClient,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """An unknown but well-formed UUID 404s on every Stage 2 route."""
    r = authed_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        f"/artefacts/{url_segment}"
    )
    assert r.status_code == 404, r.text


@pytest.mark.parametrize(
    "url_segment, filename, expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_non_uuid_returns_404(
    authed_client: TestClient,
    url_segment: str,
    filename: str,
    expected_top_field: str,
) -> None:
    """A non-UUID job_id 404s — the storage layer validates the id
    before composing the on-disk path, defending against directory
    traversal via the URL."""
    r = authed_client.get(
        f"/api/jobs/not-a-uuid/artefacts/{url_segment}"
    )
    assert r.status_code == 404, r.text


def test_critic_report_verdict_contradiction_returns_500(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    isolated_jobs_root: Path,
) -> None:
    """A tampered critic_report.json that contradicts itself
    (``verdict = READY`` alongside a ``BLOCKING`` issue) must surface
    as 500 on read.

    The cross-field validator on :class:`Stage2CriticReport` re-runs
    on :meth:`model_validate`. A 200 here would silently mislead a
    downstream consumer about whether the writer artefacts are
    shippable — *the* failure mode the route is supposed to surface.
    """
    artefact_path = (
        isolated_jobs_root
        / job_with_all_stage2_artefacts
        / "critic_report.json"
    )
    tampered = {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "reviewed_at": "2026-05-27",
        "verdict": "READY",  # contradicts the BLOCKING issue below
        "issues": [
            {
                "artefact": "BENEFITS",
                "locator": "executive_summary.body[0].claim",
                "severity": "BLOCKING",
                "category": "UNSUPPORTED_PRODUCT_CLAIM",
                "description": (
                    "Claim has no support in the knowledge bundle."
                ),
                "suggested_fix": None,
            }
        ],
        "summary": "All clear.",
        "gaps": [],
    }
    artefact_path.write_text(json.dumps(tampered), encoding="utf-8")
    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}"
        "/artefacts/critic-report"
    )
    assert r.status_code == 500, r.text
    assert "corrupt" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "url_segment, _filename, _expected_top_field", _STAGE2_ARTEFACTS,
)
def test_stage2_artefact_requires_authentication(
    anon_client: TestClient,
    job_with_all_stage2_artefacts: str,
    url_segment: str,
    _filename: str,
    _expected_top_field: str,
) -> None:
    """Every Stage 2 route is behind ``current_username`` — anonymous
    callers must receive 401, matching the existing Stage 1 routes."""
    r = anon_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/artefacts/{url_segment}"
    )
    assert r.status_code == 401, r.text
