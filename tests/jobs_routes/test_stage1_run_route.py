"""Tests for ``POST /api/jobs/{job_id}/stage1/run`` (Step 52).

The route is a thin HTTP adapter over
:meth:`backend.orchestrator.Orchestrator.run_stage1` running as a
FastAPI ``BackgroundTask``. The orchestrator's own behaviour is
exhaustively covered in ``tests/orchestrator/test_orchestrator_stage1.py``
— this module proves the **HTTP surface**: dependency injection,
request/response shape, exception mapping, and the fake-safe invariants.

FastAPI's ``TestClient`` runs background tasks synchronously before
returning the response. Tests that assert post-request state (artefacts
on disk, state transitions) can therefore query ``GET /api/jobs/{id}``
immediately and observe the fully-completed (or fully-failed) state.

Layers tested:

1. Happy-path 202: response shape, ``accepted=True``, all four Stage 1
   artefacts on disk, ``current_state == "briefing_ready"`` after run.
2. Default 503 when no dependency overrides are bound.
3. Wrong-state 409 (job not at ``created``).
4. 404 for unknown job.
5. Background failure: ``last_error`` stamped, ``current_state == "failed"``.
6. Optional/absent body shape.
7. Auth required (covered via ``test_authentication_required.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.agents.briefing_models import Briefing
from backend.agents.contact_models import ContactExtractionResult
from backend.agents.needs_models import NeedsAssessment
from backend.agents.output import AgentOutputInvalid
from backend.agents.research_models import ResearchDossier
from backend.cost_control.config_loader import Models
from backend.jobs import stage1_routes
from backend.jobs.state import JobState
from backend.main import app
from tests.agents.conftest import FakeCloudClient, make_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(model_instance) -> object:
    """Wrap a Pydantic model instance as a scripted CloudCallResult."""
    return make_response(text=model_instance.model_dump_json())


def _install_fake(
    authed_client: TestClient,
    session_factory: Callable[[], Session],
    scripted_responses: list,
) -> FakeCloudClient:
    """Override the three stage1_routes dependency providers with fakes."""
    fake = FakeCloudClient()
    fake.script(scripted_responses)

    app.dependency_overrides[stage1_routes.get_cloud_client] = lambda: fake
    app.dependency_overrides[stage1_routes.get_models] = lambda: Models(
        writer_model="fake-writer",
        research_model="fake-research",
        critic_model="fake-critic",
    )
    app.dependency_overrides[stage1_routes.get_db_session_factory] = (
        lambda: session_factory
    )
    return fake


def _uninstall_fake() -> None:
    for key in (
        stage1_routes.get_cloud_client,
        stage1_routes.get_models,
        stage1_routes.get_db_session_factory,
    ):
        app.dependency_overrides.pop(key, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def stage1_responses(
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs: NeedsAssessment,
    sample_briefing: Briefing,
) -> list:
    """Four scripted responses for the four Stage 1 agents in order."""
    return [
        _resp(sample_dossier),
        _resp(sample_contacts),
        _resp(sample_needs),
        _resp(sample_briefing),
    ]


@pytest.fixture()
def fake_client(
    authed_client: TestClient,
    session_factory,
    stage1_responses: list,
) -> Iterator[FakeCloudClient]:
    """Install a happy-path FakeCloudClient and tear it down after the test."""
    fake = _install_fake(authed_client, session_factory, stage1_responses)
    yield fake
    _uninstall_fake()


@pytest.fixture()
def crashing_client(
    authed_client: TestClient,
    session_factory,
) -> Iterator[FakeCloudClient]:
    """Install a FakeCloudClient whose first response is an agent crash."""
    responses = [AgentOutputInvalid(
        "scripted crash",
        agent="ResearchAgent",
        model="fake-research",
        attempt=1,
    )]
    fake = _install_fake(authed_client, session_factory, responses)
    yield fake
    _uninstall_fake()


# ---------------------------------------------------------------------------
# 1. Happy-path 202
# ---------------------------------------------------------------------------

def test_happy_path_202(authed_client: TestClient, created_job: str, fake_client) -> None:
    """POST on a CREATED job returns 202 with the documented envelope."""
    r = authed_client.post(
        f"/api/jobs/{created_job}/stage1/run",
        json={},
    )
    assert r.status_code == 202, r.text


def test_response_shape(authed_client: TestClient, created_job: str, fake_client) -> None:
    """Response contains job_id, accepted=True, current_state='researching'."""
    r = authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    body = r.json()
    assert body["job_id"] == created_job
    assert body["accepted"] is True
    assert body["current_state"] == "researching"


def test_stage1_completes_to_briefing_ready(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """After a happy-path POST, the background task runs to briefing_ready.

    TestClient executes background tasks synchronously before returning,
    so GET immediately reflects the final state.
    """
    authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    r = authed_client.get(f"/api/jobs/{created_job}")
    data = r.json()
    assert data["current_state"] == "briefing_ready"


def test_all_four_artefacts_written(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """After a happy-path POST, all four Stage 1 artefacts exist on disk."""
    authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    r = authed_client.get(f"/api/jobs/{created_job}")
    artefacts = r.json()["available_artefacts"]
    assert artefacts["research_dossier"] is True
    assert artefacts["contacts"] is True
    assert artefacts["needs_assessment"] is True
    assert artefacts["briefing"] is True


def test_job_not_at_created_after_run(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """After POST the job has advanced beyond 'created'."""
    authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.json()["current_state"] != "created"


# ---------------------------------------------------------------------------
# 2. Default 503 — unbound providers
# ---------------------------------------------------------------------------

def test_503_default_no_client_bound(authed_client: TestClient, created_job: str) -> None:
    """Without dependency overrides the route refuses with 503."""
    # Ensure stage1 overrides are NOT installed.
    _uninstall_fake()
    r = authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "stage1_run_route_has_no_client_bound"


# ---------------------------------------------------------------------------
# 3. Wrong-state 409
# ---------------------------------------------------------------------------

def test_409_if_already_researching(
    authed_client: TestClient,
    researching_job: str,
    fake_client,
) -> None:
    """POST on a RESEARCHING job returns 409."""
    r = authed_client.post(f"/api/jobs/{researching_job}/stage1/run", json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "wrong_state"
    assert detail["actual"] == "researching"
    assert detail["expected"] == "created"


def test_409_if_briefing_ready(
    authed_client: TestClient,
    briefing_ready_job: str,
    fake_client,
) -> None:
    """POST on a BRIEFING_READY job returns 409."""
    r = authed_client.post(f"/api/jobs/{briefing_ready_job}/stage1/run", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["actual"] == "briefing_ready"


def test_409_if_approved(
    authed_client: TestClient,
    approved_job: str,
    fake_client,
) -> None:
    """POST on an APPROVED job returns 409."""
    r = authed_client.post(f"/api/jobs/{approved_job}/stage1/run", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["actual"] == "approved"


# ---------------------------------------------------------------------------
# 4. Missing job 404
# ---------------------------------------------------------------------------

def test_404_for_missing_job(authed_client: TestClient, fake_client) -> None:
    """POST on an unknown job_id returns 404."""
    r = authed_client.post(
        "/api/jobs/00000000-0000-4000-8000-000000000000/stage1/run",
        json={},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 5. Background failure stamps last_error
# ---------------------------------------------------------------------------

def test_background_failure_stamps_last_error(
    authed_client: TestClient,
    created_job: str,
    crashing_client,
) -> None:
    """An agent crash leaves the job at 'failed' with last_error stamped."""
    authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    r = authed_client.get(f"/api/jobs/{created_job}")
    data = r.json()
    assert data["current_state"] == "failed"
    assert data["last_error"] is not None
    assert data["last_error"]["category"] in (
        "runaway_trap", "output_invalid", "crashed"
    )


def test_background_failure_still_returns_202(
    authed_client: TestClient,
    created_job: str,
    crashing_client,
) -> None:
    """Even when the background task will fail, the POST returns 202."""
    r = authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    assert r.status_code == 202, r.text


# ---------------------------------------------------------------------------
# 6. Request body variants
# ---------------------------------------------------------------------------

def test_empty_body_accepted(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """An empty JSON body ``{}`` is valid (user_context defaults to null)."""
    r = authed_client.post(f"/api/jobs/{created_job}/stage1/run", json={})
    assert r.status_code == 202


def test_user_context_accepted(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """A user_context string is forwarded without error."""
    r = authed_client.post(
        f"/api/jobs/{created_job}/stage1/run",
        json={"user_context": "Focus on cloud migration angles."},
    )
    assert r.status_code == 202


def test_extra_fields_rejected(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """Extra fields in the body are rejected (ConfigDict extra='forbid')."""
    r = authed_client.post(
        f"/api/jobs/{created_job}/stage1/run",
        json={"knowledge_bundle": "should not be here"},
    )
    assert r.status_code == 422


def test_null_user_context_accepted(
    authed_client: TestClient,
    created_job: str,
    fake_client,
) -> None:
    """Explicit ``null`` user_context is valid."""
    r = authed_client.post(
        f"/api/jobs/{created_job}/stage1/run",
        json={"user_context": None},
    )
    assert r.status_code == 202
