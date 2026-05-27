"""Tests for the two approval-boundary routes:

* ``POST /api/jobs/{job_id}/approval/open``     — briefing_ready → user_editing
* ``POST /api/jobs/{job_id}/approval/approve``  — briefing_ready or
                                                   user_editing → approved

We assert the Step 10 invariants are preserved through the HTTP
surface: legal transitions succeed, illegal ones return 409, and
the audit trail records the authenticated session username as the
approver (never a value from the request body).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.jobs.storage import read_state


# ---------------------------------------------------------------------------
# /approval/open
# ---------------------------------------------------------------------------

def test_open_for_editing_from_briefing_ready(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{briefing_ready_job}/approval/open"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "briefing_ready"
    assert body["to_state"] == "user_editing"
    assert body["job_id"] == briefing_ready_job

    state = read_state(briefing_ready_job)
    assert state.current_state.value == "user_editing"


def test_open_for_editing_already_editing_returns_409(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/open"
    )
    assert r.status_code == 409, r.text


def test_open_for_editing_from_created_returns_409(
    authed_client: TestClient,
    created_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{created_job}/approval/open"
    )
    assert r.status_code == 409, r.text


def test_open_for_editing_unknown_job_returns_404(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        "/api/jobs/00000000-0000-4000-8000-000000000000/approval/open"
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# /approval/approve — both edges
# ---------------------------------------------------------------------------

def test_approve_from_briefing_ready(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{briefing_ready_job}/approval/approve"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "briefing_ready"
    assert body["to_state"] == "approved"

    state = read_state(briefing_ready_job)
    assert state.current_state.value == "approved"


def test_approve_from_user_editing(
    authed_client: TestClient,
    editing_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{editing_job}/approval/approve"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_state"] == "user_editing"
    assert body["to_state"] == "approved"


def test_approve_records_authenticated_username(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    """The audit trail must capture *who* approved, sourced from the
    session, not the request body."""
    r = authed_client.post(
        f"/api/jobs/{briefing_ready_job}/approval/approve"
    )
    assert r.status_code == 200, r.text

    state = read_state(briefing_ready_job)
    last = state.transitions[-1]
    # The session username is "ans-admin" (see conftest VALID_USERNAME).
    assert "approved_by=ans-admin" in last["reason"]


def test_approve_ignores_body_payload(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    """Even if the caller sends a body with approved_by, it must not
    affect the audit trail — the route does not consume one."""
    r = authed_client.post(
        f"/api/jobs/{briefing_ready_job}/approval/approve",
        json={"approved_by": "attacker@evil.example.com"},
    )
    # Server may or may not accept the body — what matters is the audit
    # trail records the session user, not the body claim.
    assert r.status_code == 200, r.text

    state = read_state(briefing_ready_job)
    assert "approved_by=ans-admin" in state.transitions[-1]["reason"]
    assert "attacker" not in state.transitions[-1]["reason"]


def test_approve_in_created_returns_409(
    authed_client: TestClient,
    created_job: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{created_job}/approval/approve"
    )
    assert r.status_code == 409, r.text


def test_approve_in_approved_returns_409(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Re-approving an already-approved job is rejected — approved is
    terminal-side for the approval gate."""
    r = authed_client.post(
        f"/api/jobs/{approved_job}/approval/approve"
    )
    assert r.status_code == 409, r.text


def test_approve_to_generating_documents_remains_illegal(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Step 11 must not have introduced the next forbidden edge.

    There is no route that advances ``approved → generating_documents``
    — that edge belongs to Stage 2 and is deliberately not live yet.
    Hitting any approval route on an approved job returns 409.
    """
    for path in (
        "approval/open",
        "approval/approve",
    ):
        r = authed_client.post(f"/api/jobs/{approved_job}/{path}")
        assert r.status_code == 409, (path, r.text)


def test_approve_unknown_job_returns_404(authed_client: TestClient) -> None:
    r = authed_client.post(
        "/api/jobs/00000000-0000-4000-8000-000000000000/approval/approve"
    )
    assert r.status_code == 404, r.text
