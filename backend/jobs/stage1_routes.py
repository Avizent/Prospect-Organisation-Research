"""HTTP API for triggering Stage 1 research orchestration.

Scope (step 52)
---------------
Exposes a single new route:

    POST /api/jobs/{job_id}/stage1/run

The handler validates the job is at ``CREATED``, builds an
:class:`backend.orchestrator.Orchestrator` with injected dependencies,
and enqueues :meth:`Orchestrator.run_stage1` as a FastAPI
``BackgroundTask``. The route returns 202 immediately; the orchestrator
drives the full lifecycle on a background coroutine:

    created → researching → briefing_ready   (success)
    created/researching → failed             (any agent failure)

Fake-safe by construction
-------------------------
The three dependency providers mirror ``stage2_routes.py`` exactly: all
three default to HTTP 503. Tests override them via
``app.dependency_overrides``; a future production step will install a
real cloud client via the same surface.

This module deliberately:

* does **not** import the Anthropic SDK,
* does **not** import :mod:`keyring` or :mod:`backend.credentials`,
* does **not** import :mod:`backend.tools.cloud_client`,
* does **not** construct any production model client,
* does **not** read the macOS Keychain.

A static fence test
(:mod:`tests.jobs_routes.test_stage1_routes_static_fence`) enforces
this with AST analysis plus a substring scan.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from backend.agents._protocols import CloudClientProtocol
from backend.auth.sessions import current_username
from backend.cost_control.config_loader import Models
from backend.jobs.state import JobState
from backend.jobs.storage import JobNotFound, read_state
from backend.orchestrator import Orchestrator


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs", "stage1"])

_USER_CONTEXT_MAX = 4_000

_UNBOUND_CLIENT     = "stage1_run_route_has_no_client_bound"
_UNBOUND_MODELS     = "stage1_run_route_has_no_models_bound"
_UNBOUND_DB_FACTORY = "stage1_run_route_has_no_db_factory_bound"


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class Stage1RunBody(BaseModel):
    """POST /api/jobs/{job_id}/stage1/run body.

    ``user_context`` is forwarded to every Stage 1 agent as an optional
    operator note. Omitting it (or passing ``null``) is fine — agents
    treat it as absent.
    """

    model_config = ConfigDict(extra="forbid")

    user_context: str | None = Field(default=None, max_length=_USER_CONTEXT_MAX)


class Stage1RunResponse(BaseModel):
    """POST /api/jobs/{job_id}/stage1/run response.

    ``current_state`` reflects the state the job will enter as the
    background task starts (``"researching"``). Poll
    ``GET /api/jobs/{job_id}`` for live state updates.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    accepted: bool
    current_state: str


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------

def get_cloud_client() -> CloudClientProtocol:  # pragma: no cover
    """Return the model client. Default: refuse with 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_CLIENT},
    )


def get_models() -> Models:  # pragma: no cover
    """Return the :class:`Models` config. Default: refuse with 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_MODELS},
    )


def get_db_session_factory() -> Callable[[], DbSession]:  # pragma: no cover
    """Return a zero-arg session factory. Default: refuse with 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_DB_FACTORY},
    )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

async def _run_stage1_background(
    orch: Orchestrator,
    job_id: str,
    user_context: str | None,
) -> None:
    """Background coroutine: drives the full Stage 1 chain to completion.

    Calls :meth:`Orchestrator.run_stage1` which handles:

    * ``created → researching → briefing_ready``  on success
    * ``created/researching → failed`` + ``last_error`` stamp on failure

    All state writes, ``last_error`` stamping, and trap-counter
    bookkeeping are handled by the orchestrator. Exceptions are swallowed
    after logging so the :class:`BackgroundTask` lifecycle completes
    cleanly — the operator observes failure via ``GET /api/jobs/{id}``.
    """
    try:
        await orch.run_stage1(job_id=job_id, user_context=user_context)
    except Exception:
        # The orchestrator has already written last_error and applied
        # researching→failed before re-raising. Log and swallow so the
        # BackgroundTask does not surface an unhandled exception.
        log.exception("stage1 background run failed for job %s", job_id)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/stage1/run",
    response_model=Stage1RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_stage1_run(
    job_id: str,
    body: Stage1RunBody,
    background: BackgroundTasks,
    _username: str = Depends(current_username),
    client: CloudClientProtocol = Depends(get_cloud_client),
    models: Models = Depends(get_models),
    session_factory: Callable[[], DbSession] = Depends(get_db_session_factory),
) -> Stage1RunResponse:
    """Trigger Stage 1 research for a job at ``CREATED``.

    Pre-conditions
    --------------
    * Job exists (else 404).
    * ``current_state == "created"`` (else 409 ``wrong_state``).

    Behaviour
    ---------
    * Validates the job state synchronously in the handler.
    * Enqueues the four-agent Stage 1 chain as a ``BackgroundTask``.
    * Returns ``202 Accepted`` immediately with ``current_state:
      "researching"`` — the state the job enters at the start of the
      background run.

    Poll ``GET /api/jobs/{job_id}`` to observe ``researching →
    briefing_ready`` progress.  On agent failure the job moves to
    ``failed`` and ``last_error`` is stamped; no HTTP error is returned
    for background failures.
    """
    # --- Pre-flight: job must exist at CREATED ----------------------------
    try:
        state_file = read_state(job_id)
    except JobNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"job {job_id!r} not found",
        )

    if state_file.current_state is not JobState.CREATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "wrong_state",
                "actual": state_file.current_state.value,
                "expected": JobState.CREATED.value,
            },
        )

    # --- Enqueue Stage 1 ----------------------------------------------------
    orch = Orchestrator(
        client=client,
        models=models,
        db_session_factory=session_factory,
    )
    background.add_task(_run_stage1_background, orch, job_id, body.user_context)

    return Stage1RunResponse(
        job_id=job_id,
        accepted=True,
        current_state=JobState.RESEARCHING.value,
    )
