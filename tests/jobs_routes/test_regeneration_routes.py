"""Tests for the three regeneration routes.

* ``POST /api/jobs/{job_id}/approval/regenerate/request``
                                user_editing → regenerating_section
* ``POST /api/jobs/{job_id}/approval/regenerate/complete``
                                regenerating_section → user_editing
* ``POST /api/jobs/{job_id}/approval/regenerate/fail``
                                regenerating_section → failed

No Stage 2 agent runs from any of these routes. The complete-route
accepts a full :class:`Briefing` body and validates the company
identity matches state.json before writing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.agents.briefing_models import Briefing
from backend.jobs.storage import read_briefing, read_state


# ---------------------------------------------------------------------------
# /regenerate/request
# ---------------------------------------------------------------------------

def test_request_from_user_editing(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/regenerate/request",
        json={
            "section": "snapshot",
            "instructions": "Make the headline punchier.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "user_editing"
    assert body["to_state"] == "regenerating_section"

    state = read_state(editing_job)
    assert state.current_state.value == "regenerating_section"
    last = state.transitions[-1]
    assert "section=snapshot" in last["reason"]
    assert "Make the headline punchier" in last["reason"]


def test_request_invalid_section_returns_422(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/regenerate/request",
        json={
            "section": "not_a_real_section",
            "instructions": "anything",
        },
    )
    assert r.status_code == 422, r.text


def test_request_empty_instructions_returns_422(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/regenerate/request",
        json={
            "section": "snapshot",
            "instructions": "",
        },
    )
    assert r.status_code == 422, r.text


def test_request_in_briefing_ready_returns_409(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{briefing_ready_job}/approval/regenerate/request",
        json={"section": "snapshot", "instructions": "anything"},
    )
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# /regenerate/complete
# ---------------------------------------------------------------------------

def _briefing_payload(briefing: Briefing) -> dict:
    return briefing.model_dump(mode="json")


def test_complete_from_regenerating_section(
    authed_client: TestClient,
    regenerating_job: str,
    sample_briefing: Briefing,
) -> None:
    """Happy path — supply a valid briefing that matches identity."""
    # Mutate the snapshot to prove the body is what gets written.
    updated = sample_briefing.model_copy(
        update={
            "snapshot": sample_briefing.snapshot.model_copy(
                update={"headline": "Acme Ltd — regenerated headline"}
            )
        }
    )
    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/complete",
        json={
            "section": "snapshot",
            "briefing": _briefing_payload(updated),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "regenerating_section"
    assert body["to_state"] == "user_editing"

    # The new briefing is on disk.
    reloaded = read_briefing(regenerating_job)
    assert reloaded.snapshot.headline == "Acme Ltd — regenerated headline"


def test_complete_rejects_mismatched_company_name(
    authed_client: TestClient,
    regenerating_job: str,
    sample_briefing: Briefing,
    isolated_jobs_root: Path,
    expected_company_name: str,
) -> None:
    bad = sample_briefing.model_copy(
        update={"company_name": "Different Co"}
    )
    before_bytes = (
        isolated_jobs_root / regenerating_job / "briefing.json"
    ).read_bytes()

    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/complete",
        json={"section": "snapshot", "briefing": _briefing_payload(bad)},
    )
    assert r.status_code == 400, r.text
    assert "company_name" in str(r.json()["detail"])

    # State unchanged, briefing.json unchanged.
    state = read_state(regenerating_job)
    assert state.current_state.value == "regenerating_section"
    after_bytes = (
        isolated_jobs_root / regenerating_job / "briefing.json"
    ).read_bytes()
    assert before_bytes == after_bytes
    assert state.company_name == expected_company_name


def test_complete_rejects_mismatched_company_url(
    authed_client: TestClient,
    regenerating_job: str,
    sample_briefing: Briefing,
) -> None:
    bad = sample_briefing.model_copy(
        update={"company_url": "https://other-company.example.com/"}  # type: ignore[arg-type]
    )
    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/complete",
        json={"section": "snapshot", "briefing": _briefing_payload(bad)},
    )
    assert r.status_code == 400, r.text
    assert "company_url" in str(r.json()["detail"])


def test_complete_in_user_editing_returns_409(
    authed_client: TestClient,
    editing_job: str,
    sample_briefing: Briefing,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/regenerate/complete",
        json={
            "section": "snapshot",
            "briefing": _briefing_payload(sample_briefing),
        },
    )
    assert r.status_code == 409, r.text


def test_complete_with_invalid_briefing_returns_422(
    authed_client: TestClient,
    regenerating_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/complete",
        json={"section": "snapshot", "briefing": {"shape": "wrong"}},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# /regenerate/fail
# ---------------------------------------------------------------------------

def test_fail_from_regenerating_section(
    authed_client: TestClient,
    regenerating_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/fail",
        json={"category": "crashed", "details": "regen agent crashed"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "regenerating_section"
    assert body["to_state"] == "failed"

    state = read_state(regenerating_job)
    assert state.current_state.value == "failed"
    assert state.last_error == {
        "category": "crashed",
        "details": "regen agent crashed",
    }


def test_fail_empty_category_returns_422(
    authed_client: TestClient,
    regenerating_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{regenerating_job}/approval/regenerate/fail",
        json={"category": "", "details": "x"},
    )
    assert r.status_code == 422, r.text


def test_fail_in_user_editing_returns_409(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/regenerate/fail",
        json={"category": "crashed", "details": "x"},
    )
    assert r.status_code == 409, r.text


def test_fail_in_approved_returns_409(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{approved_job}/approval/regenerate/fail",
        json={"category": "crashed", "details": "x"},
    )
    assert r.status_code == 409, r.text
