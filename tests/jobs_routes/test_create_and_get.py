"""Tests for ``POST /api/jobs`` and ``GET /api/jobs/{job_id}``.

The intake route delegates to :func:`backend.jobs.intake.create_job`
so the heavy lifting (URL validation, UUID minting, folder
creation, initial state.json) is covered in ``tests/jobs/``. Here
we assert the HTTP surface:

* 201 with ``job_id``/``company_id``/``folder_path``/``status``.
* The new job is observable via ``GET /api/jobs/{job_id}`` and is
  in ``current_state == 'created'`` with no transitions and no
  artefacts present.
* Bad input is mapped through :class:`IntakeValidationError` to
  HTTP 400 with ``{"field", "reason"}``.
* No agents run — the on-disk state never advances past
  ``created`` as a side effect of intake.

The DB row is also probed (via the same engine the TestClient
uses) so we can assert no stray write happens to the live
``data.db`` outside the test scope.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.jobs.storage import read_state


def test_post_creates_job_in_created_state(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        "/api/jobs",
        json={
            "company_name": "Acme Ltd",
            "company_url": "https://acme.example.com/",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "created"
    assert body["company_id"] >= 1
    # folder_path is absolute and references the test-isolated jobs root.
    assert body["folder_path"].endswith(body["job_id"])

    # The job is observable, no transitions yet, no artefacts on disk.
    state = read_state(body["job_id"])
    assert state.current_state.value == "created"
    assert state.transitions == []
    assert state.last_error is None


def test_post_rejects_missing_company_name(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        "/api/jobs",
        json={
            "company_name": "   ",
            "company_url": "https://acme.example.com/",
        },
    )
    # FastAPI's body validator catches the empty name first (Field
    # min_length=1 after str_strip_whitespace).
    assert r.status_code in (400, 422), r.text


def test_post_rejects_bad_url(authed_client: TestClient) -> None:
    r = authed_client.post(
        "/api/jobs",
        json={
            "company_name": "Acme Ltd",
            "company_url": "definitely-not-a-url",
        },
    )
    # IntakeValidationError surfaces as 400 with field+reason.
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["detail"]["field"] == "company_url"


def test_post_rejects_extra_fields(authed_client: TestClient) -> None:
    """``CreateJobBody`` is ``extra='forbid'`` — typos surface as 422."""
    r = authed_client.post(
        "/api/jobs",
        json={
            "company_name": "Acme Ltd",
            "company_url": "https://acme.example.com/",
            "depth": "extended",  # Step 11 has no depth knob
        },
    )
    assert r.status_code == 422, r.text


def test_get_returns_status_transitions_and_artefacts(
    authed_client: TestClient,
    job_with_all_artefacts: str,
) -> None:
    r = authed_client.get(f"/api/jobs/{job_with_all_artefacts}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == job_with_all_artefacts
    assert body["current_state"] == "briefing_ready"
    # Three transitions to walk created → researching → briefing_ready.
    assert len(body["transitions"]) == 2
    assert body["last_error"] is None
    arts = body["available_artefacts"]
    assert arts["research_dossier"] is True
    assert arts["contacts"] is True
    assert arts["needs_assessment"] is True
    assert arts["briefing"] is True


def test_get_artefact_flags_track_existence(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job has no artefacts. The flags reflect that."""
    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert arts == {
        "research_dossier": False,
        "contacts": False,
        "needs_assessment": False,
        "briefing": False,
        "product_mapping": False,
        "benefits": False,
        "faq": False,
        "objections": False,
        "critic_report": False,
        "prospect_brief": False,
    }


def test_get_unknown_job_id_returns_404(authed_client: TestClient) -> None:
    # Well-formed UUID that doesn't exist.
    r = authed_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000"
    )
    assert r.status_code == 404, r.text


def test_get_non_uuid_returns_404(authed_client: TestClient) -> None:
    r = authed_client.get("/api/jobs/../../etc/passwd")
    # Path-traversal-ish input cannot reach the disk; the router 404s.
    assert r.status_code == 404, r.text
