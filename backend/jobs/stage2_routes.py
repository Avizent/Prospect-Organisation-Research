"""HTTP API for running fake-safe Stage 2 orchestration.

Scope (step 23)
---------------
Exposes a single new route:

    POST /api/jobs/{job_id}/stage2/run

The handler delegates to the existing
:class:`backend.orchestrator.Orchestrator` Stage 2 entry points in
strict sequence:

  1. :meth:`Orchestrator.run_mapping`
  2. :meth:`Orchestrator.run_stage2_writers`
  3. :meth:`Orchestrator.run_critic`

The orchestrator already encapsulates the in-place / no-transition
semantics for Stage 2 (``current_state`` stays at ``approved``
throughout; partial artefacts are left on disk on failure;
``last_error`` is stamped per orchestrator's existing rules). This
route adds no business logic of its own — it is a thin HTTP
adapter.

Fake-safe by construction
-------------------------
The orchestrator's three dependencies — the model client, the
:class:`Models` config, and a DB session factory — are injected via
FastAPI ``Depends``. The default implementations of those three
dependency providers raise HTTP 503 with a stable ``reason`` code,
so a production deployment that has *not* explicitly wired a real
client cannot make any orchestration call through this route. Tests
install a :class:`FakeCloudClient` (or any object matching
:class:`backend.agents._protocols.CloudClientProtocol`) via
``app.dependency_overrides``.

This module deliberately:

* does **not** import the Anthropic SDK,
* does **not** import :mod:`keyring` or :mod:`backend.credentials`,
* does **not** import :mod:`backend.tools.cloud_client`,
* does **not** construct any production model client,
* does **not** read the macOS Keychain.

A separate static-fence test
(:mod:`tests.jobs_routes.test_stage2_routes_static_fence`) enforces
this with AST analysis plus a substring scan.

State and transition contract
-----------------------------
The route appends **no** transition and changes **no** job state on
success. On failure (a runaway-trap fire, an output-invalid event,
or any other agent crash) the orchestrator stamps
``state.json.last_error`` per its existing rules; the route
re-raises so the caller sees the failure as HTTP 500 with a stable
``reason`` and ``stage`` pair.

``approved → generating_documents`` remains closed. No document
generation, no M365 delivery, no document assembly is performed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from backend.agents._protocols import CloudClientProtocol
from backend.agents.output import AgentOutputInvalid
from backend.auth.sessions import current_username
from backend.cost_control.config_loader import Models
from backend.cost_control.exceptions import RunawayTrapFired
from backend.jobs.state import JobState
from backend.jobs.storage import JobNotFound
from backend.orchestrator import JobNotInExpectedState, Orchestrator


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs", "stage2"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

#: Upper bound on the inbound knowledge_bundle payload. The real
#: ANS knowledge base is well under 200 kB of text; a much larger
#: payload is a misuse and should be rejected by the framework before
#: it reaches the orchestrator.
_KNOWLEDGE_BUNDLE_MAX = 200_000

#: Upper bound on the optional user_context string. Same rationale as
#: existing approval routes' similar fields.
_USER_CONTEXT_MAX = 4_000


class Stage2RunBody(BaseModel):
    """POST /api/jobs/{job_id}/stage2/run body.

    ``knowledge_bundle`` is required because
    :mod:`backend.tools.knowledge_loader` is not implemented yet (it
    is a step-6 deliverable). A future step can swap this to optional
    and fall back to a server-side loader without changing the wire
    format.
    """

    model_config = ConfigDict(extra="forbid")

    knowledge_bundle: str = Field(min_length=1, max_length=_KNOWLEDGE_BUNDLE_MAX)
    user_context: str | None = Field(default=None, max_length=_USER_CONTEXT_MAX)


class Stage2RunResponse(BaseModel):
    """POST /api/jobs/{job_id}/stage2/run response.

    Deliberately small: callers re-fetch the produced artefacts via
    the existing ``GET /api/jobs/{id}/artefacts/...`` routes. Inlining
    the artefacts here would duplicate wire format and force this
    module to import the five Stage 2 model classes.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    stages_completed: list[str]
    current_state: str


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------
#
# These three providers are the route's single injection point for the
# orchestrator's dependencies. The defaults raise HTTP 503 with a
# stable ``reason`` so production deployments that have not deliberately
# bound a real client are safe by default. Tests bind fakes by setting
# ``app.dependency_overrides[get_cloud_client]``,
# ``app.dependency_overrides[get_models]`` and
# ``app.dependency_overrides[get_db_session_factory]`` to callables that
# return a fake client, a :class:`Models` dataclass and a session
# factory respectively.
#
# A future step that wires the production client will replace these
# providers (or set the overrides at app startup) — explicitly and
# review-visibly.

_UNBOUND_CLIENT = "stage2_run_route_has_no_client_bound"
_UNBOUND_MODELS = "stage2_run_route_has_no_models_bound"
_UNBOUND_DB_FACTORY = "stage2_run_route_has_no_db_factory_bound"


def get_cloud_client() -> CloudClientProtocol:  # pragma: no cover - default raises
    """Return the model client. Default: refuse with 503.

    The override site is :attr:`fastapi.FastAPI.dependency_overrides`.
    Tests install a :class:`FakeCloudClient`; a future production step
    will install a real client through the same override surface.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_CLIENT},
    )


def get_models() -> Models:  # pragma: no cover - default raises
    """Return the :class:`Models` config. Default: refuse with 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_MODELS},
    )


def get_db_session_factory() -> Callable[[], DbSession]:  # pragma: no cover - default raises
    """Return a zero-arg session factory. Default: refuse with 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": _UNBOUND_DB_FACTORY},
    )


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------

#: Stage labels reported in the failure body. Stable strings —
#: callers may want to switch on these. Mirror the orchestrator's
#: three logical stages (``run_stage2_writers`` is reported as a
#: single ``writers`` stage because the orchestrator already chains
#: the three writers internally and a partial-write inside that chain
#: surfaces a single exception).
_STAGE_MAPPING = "mapping"
_STAGE_WRITERS = "writers"
_STAGE_CRITIC = "critic"


def _category_for(exc: BaseException) -> str:
    """Map an orchestrator exception to the wire-level ``category``.

    Mirrors the orchestrator's own ``_FAIL_*`` labels so the HTTP
    surface and the on-disk ``last_error.category`` use the same
    vocabulary. We deliberately do not import the private constants
    from :mod:`backend.orchestrator` — they are an internal contract;
    we re-state them here as the public HTTP contract.
    """
    if isinstance(exc, RunawayTrapFired):
        return "runaway_trap"
    if isinstance(exc, AgentOutputInvalid):
        return "output_invalid"
    return "crashed"


def _http_agent_failure(stage: str, exc: BaseException) -> HTTPException:
    """Build a 500 for a mid-run agent failure.

    The orchestrator has already stamped ``state.json.last_error`` by
    the time we reach this point — we surface the same category
    label here so the caller does not have to re-fetch state.json to
    decide what to do.
    """
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "reason": "stage2_agent_failed",
            "stage": stage,
            "category": _category_for(exc),
        },
    )


def _http_upstream_corrupt(stage: str) -> HTTPException:
    """Build a 500 for a missing or schema-invalid upstream artefact.

    Pre-flight failures (``JobNotFound``, ``pydantic.ValidationError``
    on an upstream artefact) are caller mistakes — the operator asked
    Stage 2 to run before the required upstream artefact existed —
    but they still surface as 500 because the route cannot
    distinguish "you ran me too early" from "the on-disk file is
    corrupt" without inspecting state.json itself, and we deliberately
    avoid that inspection here.
    """
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "reason": "stage2_upstream_artefact_unreadable",
            "stage": stage,
        },
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/stage2/run",
    response_model=Stage2RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_stage2_run(
    job_id: str,
    body: Stage2RunBody,
    _username: str = Depends(current_username),
    client: CloudClientProtocol = Depends(get_cloud_client),
    models: Models = Depends(get_models),
    session_factory: Callable[[], DbSession] = Depends(get_db_session_factory),
) -> Stage2RunResponse:
    """Run the three Stage 2 orchestration steps in sequence.

    The orchestrator handles state machinery, semaphore acquisition,
    ``last_error`` stamping, and trap-counter bookkeeping. This route
    only:

    * builds the :class:`Orchestrator` with the injected dependencies,
    * invokes ``run_mapping`` → ``run_stage2_writers`` → ``run_critic``
      in order,
    * maps the orchestrator's documented exception contract onto HTTP
      status codes, and
    * returns a small envelope identifying which stages completed.

    The job stays at :attr:`JobState.APPROVED` throughout — the
    orchestrator's documented in-place contract. No transition is
    appended. ``approved → generating_documents`` remains closed.
    """
    orch = Orchestrator(
        client=client,
        models=models,
        db_session_factory=session_factory,
    )

    completed: list[str] = []

    # ----- Stage 2a: mapping -------------------------------------------------
    try:
        await orch.run_mapping(
            job_id=job_id,
            knowledge_bundle=body.knowledge_bundle,
            user_context=body.user_context,
        )
    except JobNotInExpectedState as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "wrong_state",
                "actual": exc.actual.value,
                "expected": JobState.APPROVED.value,
            },
        ) from exc
    except JobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except (RunawayTrapFired, AgentOutputInvalid) as exc:
        raise _http_agent_failure(_STAGE_MAPPING, exc) from exc
    except Exception as exc:
        # Pre-flight pydantic.ValidationError on briefing.json, or any
        # other unexpected failure. The orchestrator's contract is to
        # re-raise unchanged; we map to 500 here.
        log.exception("stage2 mapping failed for job %s", job_id)
        if "ValidationError" in type(exc).__name__:
            raise _http_upstream_corrupt(_STAGE_MAPPING) from exc
        raise _http_agent_failure(_STAGE_MAPPING, exc) from exc
    completed.append(_STAGE_MAPPING)

    # ----- Stage 2b: writers -------------------------------------------------
    try:
        await orch.run_stage2_writers(
            job_id=job_id,
            knowledge_bundle=body.knowledge_bundle,
            user_context=body.user_context,
        )
    except (RunawayTrapFired, AgentOutputInvalid) as exc:
        raise _http_agent_failure(_STAGE_WRITERS, exc) from exc
    except Exception as exc:
        log.exception("stage2 writers failed for job %s", job_id)
        if "ValidationError" in type(exc).__name__:
            raise _http_upstream_corrupt(_STAGE_WRITERS) from exc
        raise _http_agent_failure(_STAGE_WRITERS, exc) from exc
    completed.append(_STAGE_WRITERS)

    # ----- Stage 2c: critic --------------------------------------------------
    try:
        await orch.run_critic(
            job_id=job_id,
            knowledge_bundle=body.knowledge_bundle,
            user_context=body.user_context,
        )
    except (RunawayTrapFired, AgentOutputInvalid) as exc:
        raise _http_agent_failure(_STAGE_CRITIC, exc) from exc
    except Exception as exc:
        log.exception("stage2 critic failed for job %s", job_id)
        if "ValidationError" in type(exc).__name__:
            raise _http_upstream_corrupt(_STAGE_CRITIC) from exc
        raise _http_agent_failure(_STAGE_CRITIC, exc) from exc
    completed.append(_STAGE_CRITIC)

    return Stage2RunResponse(
        job_id=job_id,
        stages_completed=completed,
        current_state=JobState.APPROVED.value,
    )
