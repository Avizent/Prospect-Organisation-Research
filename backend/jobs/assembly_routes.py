"""HTTP API for the explicit Markdown brief assembly trigger.

Scope (step 32)
---------------
Exposes a single new route:

    POST /api/jobs/{job_id}/brief/assemble

The handler is a thin HTTP adapter over
:func:`backend.assembly.markdown.assemble_job` — the Step 29 pure
renderer that writes ``prospect_brief.md`` and
``document_manifest.json`` to the job folder. No agent runs, no model
client is constructed, no state transition is appended, no source
artefact is mutated.

Fence relaxation
----------------
This is the **one** module under ``backend/jobs/`` that is allowed to
import :mod:`backend.assembly`. A separate static-fence test
(:mod:`tests.jobs_routes.test_assembly_routes_static_fence`) enforces
the rule with AST analysis plus a substring scan, and includes a
positive-shape assertion that the assembler import is present (if a
future refactor moves it out, the route is no longer doing its job).

Strict scope
------------
This module deliberately:

* does **not** import the Anthropic SDK,
* does **not** import :mod:`keyring` or :mod:`backend.credentials`,
* does **not** import :mod:`backend.delivery`,
* does **not** import :mod:`backend.cost_control.cloud_client`,
* does **not** import :mod:`backend.tools.cloud_client`,
* does **not** import :mod:`backend.orchestrator`,
* does **not** construct any model client,
* does **not** read the macOS Keychain,
* does **not** call ``append_transition`` / ``write_state`` /
  ``record_failure`` — the route reads ``state.json`` only.

Precondition checks
-------------------
Before calling the assembler, the route validates three preconditions
in order. A failure of any one is a 4xx response and **no** files are
written:

1. The job exists (``state.json`` is readable). → 404
2. ``current_state == "approved"``.            → 409
3. ``critic_report.json`` is on disk.          → 409

The second and third checks defend against on-disk drift — the state
machine reaches ``approved`` after the critic runs, so the two are
expected to be consistent. The check is still cheap and gives the
frontend a clean recovery path ("re-run Stage 2") instead of a
500.

201 vs 200 semantics
--------------------
The assembler always overwrites via :func:`os.replace`; it has no
"did this file exist?" hook. So the route snapshots the
``prospect_brief.md`` path's existence **before** calling the
assembler and reports:

* ``201 Created`` — the file was absent on entry (first assembly).
* ``200 OK``     — the file existed (idempotent regeneration).

Response shape is identical in both cases — the status code is the
only difference. A client that does not care about the distinction
can treat 200 and 201 the same.

State and transition contract
-----------------------------
The Step 29 assembler is documented as side-effect-free with respect
to the job state machine; this route preserves that property at the
HTTP layer. After a successful call:

* ``current_state`` is unchanged.
* ``transitions[]`` is unchanged.
* ``last_error`` is unchanged.
* No source artefact (briefing / mapping / benefits / faq /
  objections / critic_report) is mutated.

The only on-disk side effects are the two output files the assembler
writes: ``prospect_brief.md`` and ``document_manifest.json``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from backend.assembly.markdown import (
    AssemblyResult,
    BriefingRequiredError,
    assemble_job,
)
from backend.auth.sessions import current_username
from backend.jobs.state import JobState
from backend.jobs.storage import (
    JobNotFound,
    job_folder,
    read_critic_report,
    read_state,
)


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs", "assembly"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class AssembleBriefResponse(BaseModel):
    """POST /api/jobs/{job_id}/brief/assemble response.

    All five non-``job_id`` fields are passed through from
    :data:`AssemblyResult.manifest` so the wire shape stays a thin
    projection of the on-disk manifest. Clients that want the full
    manifest (sections, artefacts, warnings, byte length) can read
    ``document_manifest.json`` from the job folder.

    ``critic_verdict`` is ``None`` when the critic report is absent
    or malformed at assembly time. In Step 32 this never happens on
    the 2xx path — the route refuses to assemble without a critic
    report — but the field is left nullable so a future relaxation
    (e.g. assemble-without-critic for diagnostics) can flow through
    without a schema bump.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    markdown_filename: str
    manifest_filename: str
    markdown_sha256: str
    critic_verdict: str | None
    generated_at: str


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------

def _http_404(reason: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=detail,
    )


def _http_409(reason: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=detail,
    )


def _http_500(reason: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/brief/assemble",
    response_model=AssembleBriefResponse,
    # FastAPI's default response status is overridden per-call below
    # (201 vs 200) by writing to the ``Response`` object directly.
    status_code=status.HTTP_201_CREATED,
)
def post_assemble_brief(
    job_id: str,
    response: Response,
    _username: str = Depends(current_username),
) -> AssembleBriefResponse:
    """Assemble ``prospect_brief.md`` + ``document_manifest.json``.

    See module docstring for the full preconditions / state-contract.
    """
    # ------------------------------------------------------------------
    # 1. Validate the job exists. ``read_state`` raises ``JobNotFound``
    #    if the folder or state.json is missing. ``_validate_job_id``
    #    inside ``read_state``'s code path raises ``ValueError`` for a
    #    non-UUID — both map to 404.
    # ------------------------------------------------------------------
    try:
        state = read_state(job_id)
    except JobNotFound as exc:
        raise _http_404("job_not_found", message=str(exc)) from exc
    except ValueError as exc:
        # _validate_job_id rejected a non-UUID job_id.
        raise _http_404(
            "job_not_found", message=f"invalid job id: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 2. Validate the job is in the approved state. Anything else is a
    #    workflow-precondition failure, surfaced as 409 so the frontend
    #    can prompt the operator to walk the briefing through the
    #    approval gate and Stage 2 first.
    # ------------------------------------------------------------------
    if state.current_state is not JobState.APPROVED:
        raise _http_409(
            "state_not_approved",
            current_state=state.current_state.value,
            required_state=JobState.APPROVED.value,
        )

    # ------------------------------------------------------------------
    # 3. Validate that ``critic_report.json`` is on disk. The state
    #    machine reaches ``approved`` after the critic runs, so this
    #    is a drift/corruption check. We map a missing critic to 409
    #    (recoverable workflow failure: re-run Stage 2) rather than
    #    500 (unexpected server fault). We avoid a full
    #    ``model_validate`` here so a malformed-but-present critic
    #    report does not double-fault between this check and the
    #    assembler's own ``critic_outcome`` branching.
    # ------------------------------------------------------------------
    critic_path = job_folder(job_id) / "critic_report.json"
    if not critic_path.exists():
        raise _http_409("missing_critic_report")

    # ------------------------------------------------------------------
    # 4. Snapshot the markdown-on-disk presence to choose 201 vs 200.
    # ------------------------------------------------------------------
    markdown_path = job_folder(job_id) / "prospect_brief.md"
    pre_existed = markdown_path.exists()

    # ------------------------------------------------------------------
    # 5. Run the assembler. ``BriefingRequiredError`` is mapped to 500
    #    rather than 4xx because reaching this branch means the state
    #    machine said "approved" but the briefing isn't readable on
    #    disk — i.e. genuine corruption, not an operator mistake.
    #    Any other unexpected exception is also a 500 with a stable
    #    reason code.
    # ------------------------------------------------------------------
    try:
        result: AssemblyResult = assemble_job(job_id)
    except BriefingRequiredError as exc:
        log.exception(
            "assembly failed: briefing required for job %s", job_id,
        )
        raise _http_500(
            "briefing_required", message=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        log.exception("assembly failed for job %s", job_id)
        raise _http_500(
            "assembly_failed", message=str(exc),
        ) from exc

    # ------------------------------------------------------------------
    # 6. Project the manifest into the response shape.
    # ------------------------------------------------------------------
    manifest = result.manifest
    body = AssembleBriefResponse(
        job_id=job_id,
        markdown_filename=manifest["markdown_filename"],
        manifest_filename="document_manifest.json",
        markdown_sha256=manifest["markdown_sha256"],
        critic_verdict=manifest.get("critic_verdict"),
        generated_at=manifest["generated_at"],
    )

    # ------------------------------------------------------------------
    # 7. Choose status code: 201 if we just created the file, 200 if
    #    we regenerated it in place.
    # ------------------------------------------------------------------
    response.status_code = (
        status.HTTP_200_OK if pre_existed else status.HTTP_201_CREATED
    )
    return body
