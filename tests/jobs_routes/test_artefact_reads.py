"""Tests for the four artefact GET routes.

* ``GET /api/jobs/{job_id}/artefacts/research-dossier``
* ``GET /api/jobs/{job_id}/artefacts/contacts``
* ``GET /api/jobs/{job_id}/artefacts/needs-assessment``
* ``GET /api/jobs/{job_id}/briefing``

Each route returns the raw ``model_dump(mode="json")`` payload of
its Pydantic artefact — no wrapper, no rename. The error contract:

* artefact absent on disk → 404
* artefact corrupt (bad JSON or schema violation) → 500
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "path, key, expected_top_field",
    [
        ("artefacts/research-dossier", "research_dossier",
         "company_name"),
        ("artefacts/contacts", "contacts", "company_name"),
        ("artefacts/needs-assessment", "needs_assessment",
         "lab_maturity"),
        ("briefing", "briefing", "snapshot"),
    ],
)
def test_artefact_present_returns_payload(
    authed_client: TestClient,
    job_with_all_artefacts: str,
    path: str,
    key: str,
    expected_top_field: str,
) -> None:
    """Each artefact route returns the raw payload after seeding."""
    r = authed_client.get(
        f"/api/jobs/{job_with_all_artefacts}/{path}"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert expected_top_field in body


@pytest.mark.parametrize(
    "path",
    [
        "artefacts/research-dossier",
        "artefacts/contacts",
        "artefacts/needs-assessment",
        "briefing",
    ],
)
def test_artefact_absent_returns_404(
    authed_client: TestClient,
    created_job: str,
    path: str,
) -> None:
    """A freshly-created job has no artefacts; every read returns 404."""
    r = authed_client.get(f"/api/jobs/{created_job}/{path}")
    assert r.status_code == 404, r.text


def test_artefact_unknown_job_returns_404(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000/briefing"
    )
    assert r.status_code == 404, r.text


def test_corrupt_briefing_returns_500(
    authed_client: TestClient,
    job_with_all_artefacts: str,
    isolated_jobs_root: Path,
) -> None:
    """A briefing.json whose schema breaks surfaces as 500 (server-side).

    We rewrite the file with valid JSON but an invalid shape — the
    Pydantic validator on read should reject it.
    """
    briefing_path = (
        isolated_jobs_root / job_with_all_artefacts / "briefing.json"
    )
    briefing_path.write_text('{"not": "a valid briefing"}', encoding="utf-8")
    r = authed_client.get(
        f"/api/jobs/{job_with_all_artefacts}/briefing"
    )
    assert r.status_code == 500, r.text
    assert "corrupt" in r.json()["detail"].lower()


def test_unparsable_briefing_returns_500(
    authed_client: TestClient,
    job_with_all_artefacts: str,
    isolated_jobs_root: Path,
) -> None:
    """A briefing.json that isn't JSON at all surfaces as 500."""
    briefing_path = (
        isolated_jobs_root / job_with_all_artefacts / "briefing.json"
    )
    briefing_path.write_text("not json at all", encoding="utf-8")
    r = authed_client.get(
        f"/api/jobs/{job_with_all_artefacts}/briefing"
    )
    assert r.status_code == 500, r.text
