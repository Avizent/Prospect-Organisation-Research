"""Tests for ``PATCH /api/jobs/{job_id}/briefing``.

The route is a thin wrapper around
:func:`backend.jobs.approval.apply_briefing_edit`. We assert the
HTTP-surface contract:

* 200 with the full updated briefing on success
* 409 when the job is not in ``user_editing``
* 422 when the merged briefing fails revalidation (e.g. an out-of-
  range ``source_indices``); the on-disk briefing is unchanged
* 422 when the request body itself rejects (e.g. unknown field)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.jobs.storage import read_briefing


def test_patch_in_user_editing_returns_updated_briefing(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.patch(
        f"/api/jobs/{editing_job}/briefing",
        json={"user_context": "Lead with the manufacturing angle."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_context"] == "Lead with the manufacturing angle."

    # On disk matches the response.
    reloaded = read_briefing(editing_job)
    assert reloaded.user_context == "Lead with the manufacturing angle."


def test_patch_in_briefing_ready_returns_409(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    r = authed_client.patch(
        f"/api/jobs/{briefing_ready_job}/briefing",
        json={"user_context": "should not apply"},
    )
    assert r.status_code == 409, r.text


def test_patch_in_approved_returns_409(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    r = authed_client.patch(
        f"/api/jobs/{approved_job}/briefing",
        json={"user_context": "should not apply"},
    )
    assert r.status_code == 409, r.text


def test_patch_out_of_range_source_index_returns_422(
    authed_client: TestClient,
    editing_job: str,
    isolated_jobs_root: Path,
) -> None:
    """A patch whose source_indices points past sources.entries is
    rejected with 422 and the on-disk briefing is unchanged."""
    briefing_path = isolated_jobs_root / editing_job / "briefing.json"
    before_bytes = briefing_path.read_bytes()

    bad_business = {
        "news": [
            {
                "summary": "bogus citation",
                "detail": "references a source that does not exist",
                "confidence": "HIGH",
                "source_indices": [99],  # well out of range
            }
        ],
    }
    r = authed_client.patch(
        f"/api/jobs/{editing_job}/briefing",
        json={"business_context": bad_business},
    )
    assert r.status_code == 422, r.text

    # The on-disk briefing was not mutated.
    after_bytes = briefing_path.read_bytes()
    assert before_bytes == after_bytes


def test_patch_unknown_field_returns_422(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    """BriefingPatch is ``extra='forbid'`` — typos surface as 422."""
    r = authed_client.patch(
        f"/api/jobs/{editing_job}/briefing",
        json={"definitely_not_a_field": "oops"},
    )
    assert r.status_code == 422, r.text


def test_patch_unknown_job_returns_404(authed_client: TestClient) -> None:
    r = authed_client.patch(
        "/api/jobs/00000000-0000-4000-8000-000000000000/briefing",
        json={"user_context": "x"},
    )
    assert r.status_code == 404, r.text


def test_patch_does_not_change_job_state(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    """A successful PATCH leaves ``current_state == user_editing``."""
    r = authed_client.patch(
        f"/api/jobs/{editing_job}/briefing",
        json={"user_context": "anything"},
    )
    assert r.status_code == 200

    s = authed_client.get(f"/api/jobs/{editing_job}").json()
    assert s["current_state"] == "user_editing"


def test_patch_attempt_on_identity_field_rejected(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    """``company_name`` is intentionally not on the BriefingPatch
    surface — it must surface as 422."""
    r = authed_client.patch(
        f"/api/jobs/{editing_job}/briefing",
        json={"company_name": "Different Co"},
    )
    assert r.status_code == 422, r.text
