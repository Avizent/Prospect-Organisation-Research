"""HTTP API for the fake-safe job/briefing/approval workflow.

Scope (step 11)
---------------
Exposes the existing Step 7a (intake), Step 9a (artefact reads), and
Step 10 (approval gate) primitives over HTTP. **No Stage 1 run**,
**no Stage 1 dry-run**, **no production model-client wrapper**,
**no Keychain reads**, **no real Anthropic calls**, **no M365**,
**no document generation**, **no frontend**, **no Stage 2 agents**.

Every route is a thin adapter: it parses the incoming request,
delegates to a backend module that already exists, and maps the
documented exception contract onto HTTP status codes. The routes
add no business logic of their own — the Step 10 invariants
(legality of every state transition, briefing-revalidation gate
on :func:`approve`, ``approved_by`` audit trail) are preserved by
calling the approval-module functions directly.

Routes
------
POST   /api/jobs                                          intake.create_job
GET    /api/jobs/{job_id}                                 read_state projection
GET    /api/jobs/{job_id}/artefacts/research-dossier      read_dossier
GET    /api/jobs/{job_id}/artefacts/contacts              read_contacts
GET    /api/jobs/{job_id}/artefacts/needs-assessment      read_needs_assessment
GET    /api/jobs/{job_id}/briefing                        read_briefing
GET    /api/jobs/{job_id}/artefacts/product-mapping       read_product_mapping
GET    /api/jobs/{job_id}/artefacts/benefits              read_benefits
GET    /api/jobs/{job_id}/artefacts/faq                   read_faq
GET    /api/jobs/{job_id}/artefacts/objections            read_objections
GET    /api/jobs/{job_id}/artefacts/critic-report         read_critic_report
GET    /api/jobs/{job_id}/brief/markdown                  read_prospect_brief_markdown
GET    /api/jobs/{job_id}/manifest                        read_document_manifest
POST   /api/jobs/{job_id}/approval/open                   open_for_editing
PATCH  /api/jobs/{job_id}/briefing                        apply_briefing_edit
POST   /api/jobs/{job_id}/approval/approve                approve
POST   /api/jobs/{job_id}/approval/regenerate/request     request_regeneration
POST   /api/jobs/{job_id}/approval/regenerate/complete    complete_regeneration
POST   /api/jobs/{job_id}/approval/regenerate/fail        fail_regeneration

Error mapping
-------------
* :class:`JobNotFound`                              → 404 (folder/file missing)
* :class:`EditNotAllowed` / :class:`IllegalTransition` → 409 (wrong state)
* :class:`IntakeValidationError`                    → 400 (intake input bad)
* :class:`pydantic.ValidationError` on **request**  → 422 (FastAPI handles
  body shape automatically; we also catch validation that fires inside
  :func:`apply_briefing_edit` when the merged briefing is rejected by the
  top-level validator)
* :class:`pydantic.ValidationError` on **on-disk artefact** → 500. The
  artefact was written by the backend itself; a malformed on-disk file
  is server-side corruption, not a request defect — 422 would imply
  "fix your request and retry" which is false.
* :class:`json.JSONDecodeError` on on-disk artefact  → 500 (same reasoning)

Security
--------
Every route requires an authenticated session
(:func:`backend.auth.sessions.current_username`). Anonymous callers
receive HTTP 401 from the dependency. ``approved_by`` for
:func:`approve` is **always** taken from the authenticated session,
never from the request body — there is no ``ApproveBody`` schema.

Static-fence
------------
``tests/jobs_routes/test_no_production_client_or_keychain.py``
asserts via AST that this module never imports any of:
``anthropic``, ``keyring``, ``backend.credentials``,
``backend.delivery``, ``backend.assembly``, ``backend.orchestrator``,
``backend.tools.cloud_client``, ``frontend``. FastAPI *is* allowed
here — this is the route module.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session as DbSession

from backend.agents.briefing_models import Briefing
from backend.auth.sessions import current_username
from backend.db.session import get_db
from backend.jobs.approval import (
    BriefingPatch,
    BriefingSection,
    EditNotAllowed,
    apply_briefing_edit,
    approve,
    complete_regeneration,
    fail_regeneration,
    open_for_editing,
    request_regeneration,
)
from backend.jobs.intake import IntakeValidationError, create_job
from backend.jobs.state import IllegalTransition
from backend.jobs.storage import (
    JobNotFound,
    job_folder,
    read_benefits,
    read_briefing,
    read_contacts,
    read_critic_report,
    read_document_manifest,
    read_dossier,
    read_faq,
    read_needs_assessment,
    read_objections,
    read_product_mapping,
    read_prospect_brief_markdown,
    read_state,
)


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CreateJobBody(BaseModel):
    """POST /api/jobs body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=300)
    company_url: str = Field(min_length=1, max_length=2048)


class CreateJobResponse(BaseModel):
    """POST /api/jobs response — job_id and the canonical folder path."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    company_id: int
    folder_path: str
    status: str = Field(description="Initial JobState value (always 'created')")


class AvailableArtefacts(BaseModel):
    """Which artefacts exist on disk for the job.

    Stage 1 artefacts: ``research_dossier``, ``contacts``,
    ``needs_assessment``, ``briefing``.

    Stage 2 artefacts: ``product_mapping``, ``benefits``, ``faq``,
    ``objections``, ``critic_report``. The frontend inspector
    (``frontend/js/screens/job_status.js``) iterates this dict and
    builds ``/api/jobs/{id}/artefacts/<key-with-hyphens>`` URLs from
    each ``True`` key, so adding a new field here automatically
    surfaces an "open JSON" link once the matching route exists.

    Assembly artefact: ``prospect_brief`` (Step 31) flips ``True`` once
    :mod:`backend.assembly.markdown` has written ``prospect_brief.md``.
    The Markdown is a *display* artefact, not a JSON one — the
    inspector treats this boolean specially and links to the in-app
    viewer at ``#/jobs/{id}/brief`` rather than to a JSON URL.

    Provenance artefact: ``document_manifest`` (Step 34) flips ``True``
    once the assembler has written ``document_manifest.json``. Like
    ``prospect_brief`` this is a *display* artefact — the inspector
    links to the in-app provenance viewer at ``#/jobs/{id}/manifest``
    rather than to a raw JSON URL (a secondary "Open raw JSON" link
    inside that screen targets the new GET route directly). The two
    flags are deliberately surfaced separately because the Markdown
    and the manifest are written by two distinct ``_atomic_write_json``
    / ``write_prospect_brief_markdown`` calls and can drift on disk in
    pathological scenarios.
    """

    model_config = ConfigDict(extra="forbid")

    research_dossier: bool
    contacts: bool
    needs_assessment: bool
    briefing: bool
    product_mapping: bool
    benefits: bool
    faq: bool
    objections: bool
    critic_report: bool
    prospect_brief: bool
    document_manifest: bool


class JobStatusResponse(BaseModel):
    """GET /api/jobs/{job_id} response."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    company_name: str
    company_url: str
    current_state: str
    created_at: str
    transitions: list[dict[str, Any]]
    last_error: dict[str, Any] | None
    available_artefacts: AvailableArtefacts


class TransitionAppliedResponse(BaseModel):
    """Response shape for the five state-changing approval endpoints."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    from_state: str
    to_state: str
    at: str


class RequestRegenerationBody(BaseModel):
    """POST /api/jobs/{job_id}/approval/regenerate/request body."""

    model_config = ConfigDict(extra="forbid")

    section: BriefingSection
    instructions: str = Field(min_length=1, max_length=4000)


class CompleteRegenerationBody(BaseModel):
    """POST /api/jobs/{job_id}/approval/regenerate/complete body.

    Accepts the full updated briefing produced by the (out-of-scope)
    regeneration agent. We validate the company identity matches
    ``state.json`` before writing — a regeneration that returned a
    briefing for the wrong company would be a fatal protocol error.
    """

    model_config = ConfigDict(extra="forbid")

    section: BriefingSection
    briefing: Briefing


class FailRegenerationBody(BaseModel):
    """POST /api/jobs/{job_id}/approval/regenerate/fail body."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=64)
    details: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_404(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _http_409(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _http_500_corrupt(artefact: str) -> HTTPException:
    """Map on-disk corruption to 500 with a deliberately generic detail.

    The artefact path is omitted from the detail to avoid leaking the
    user's home directory in error responses.
    """
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"on-disk {artefact} is corrupt or malformed",
    )


def _job_state_or_404(job_id: str):
    """Resolve and validate a job_id as a UUID, returning the read_state.

    Wraps the storage layer's exception contract:
      - non-UUID job_id           → 404 (we do not echo the raw id back)
      - missing state.json         → 404 (JobNotFound)
      - corrupt state.json         → 500

    Order matters: :class:`pydantic.ValidationError` and
    :class:`json.JSONDecodeError` both subclass :class:`ValueError`,
    so we must match the corruption case before the catch-all
    UUID-validation ValueError.
    """
    try:
        return read_state(job_id)
    except JobNotFound as exc:
        raise _http_404(str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        log.exception("state.json corrupt for job %s", job_id)
        raise _http_500_corrupt("state.json") from exc
    except ValueError as exc:
        # _validate_job_id raises ValueError on non-UUID input.
        raise _http_404(f"job not found: {exc}") from exc


def _transition_response(result) -> TransitionAppliedResponse:
    """Convert a :class:`TransitionApplied` dataclass into the wire shape."""
    return TransitionAppliedResponse(
        job_id=result.job_id,
        from_state=result.from_state.value,
        to_state=result.to_state.value,
        at=result.at.isoformat(),
    )


# ---------------------------------------------------------------------------
# 1. POST /api/jobs
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=CreateJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_create_job(
    body: CreateJobBody,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> CreateJobResponse:
    """Create a job at :attr:`JobState.CREATED`. No agents run."""
    try:
        result = create_job(
            db=db,
            company_name=body.company_name,
            company_url=body.company_url,
        )
    except IntakeValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"field": exc.field, "reason": exc.reason},
        ) from exc
    return CreateJobResponse(
        job_id=result.job_id,
        company_id=result.company_id,
        folder_path=str(result.folder_path),
        status="created",
    )


# ---------------------------------------------------------------------------
# 2. GET /api/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    _username: str = Depends(current_username),
) -> JobStatusResponse:
    """Return current state, transitions, last_error, available artefacts.

    A job whose folder is missing returns 404. A job whose ``state.json``
    is malformed returns 500 (server-side corruption).
    """
    snapshot = _job_state_or_404(job_id)
    folder = job_folder(job_id)
    available = AvailableArtefacts(
        research_dossier=(folder / "research_dossier.json").exists(),
        contacts=(folder / "contacts.json").exists(),
        needs_assessment=(folder / "needs_assessment.json").exists(),
        briefing=(folder / "briefing.json").exists(),
        product_mapping=(folder / "product_mapping.json").exists(),
        benefits=(folder / "benefits.json").exists(),
        faq=(folder / "faq.json").exists(),
        objections=(folder / "objections.json").exists(),
        critic_report=(folder / "critic_report.json").exists(),
        prospect_brief=(folder / "prospect_brief.md").exists(),
        document_manifest=(folder / "document_manifest.json").exists(),
    )
    return JobStatusResponse(
        job_id=snapshot.job_id,
        company_name=snapshot.company_name,
        company_url=snapshot.company_url,
        current_state=snapshot.current_state.value,
        created_at=snapshot.created_at.isoformat(),
        transitions=list(snapshot.transitions),
        last_error=snapshot.last_error,
        available_artefacts=available,
    )


# ---------------------------------------------------------------------------
# 3-5. Artefact reads
# ---------------------------------------------------------------------------

def _serve_artefact(
    *,
    job_id: str,
    artefact_name: str,
    reader,
) -> dict[str, Any]:
    """Common artefact-read flow used by the three GET artefact routes.

    Validates ``job_id`` is a UUID (404 otherwise), then delegates to
    ``reader(job_id)`` and maps its exception contract:

      * JobNotFound           → 404 (file absent)
      * JSONDecodeError       → 500 (corrupt)
      * pydantic.ValidationError → 500 (corrupt)
    """
    try:
        artefact = reader(job_id)
    except JobNotFound as exc:
        raise _http_404(str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        log.exception("%s corrupt for job %s", artefact_name, job_id)
        raise _http_500_corrupt(artefact_name) from exc
    except ValueError as exc:
        # _validate_job_id raised ValueError on non-UUID input.
        # Matched last because ValidationError/JSONDecodeError both
        # subclass ValueError.
        raise _http_404(f"job not found: {exc}") from exc
    return artefact.model_dump(mode="json")


@router.get("/{job_id}/artefacts/research-dossier")
def get_research_dossier(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``research_dossier.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="research_dossier.json",
        reader=read_dossier,
    )


@router.get("/{job_id}/artefacts/contacts")
def get_contacts(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``contacts.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="contacts.json",
        reader=read_contacts,
    )


@router.get("/{job_id}/artefacts/needs-assessment")
def get_needs_assessment(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``needs_assessment.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="needs_assessment.json",
        reader=read_needs_assessment,
    )


# ---------------------------------------------------------------------------
# 6. GET /api/jobs/{job_id}/briefing
# ---------------------------------------------------------------------------

@router.get("/{job_id}/briefing")
def get_briefing(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``briefing.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="briefing.json",
        reader=read_briefing,
    )


# ---------------------------------------------------------------------------
# 7. Stage 2 artefact reads (read-only inspection)
# ---------------------------------------------------------------------------
#
# Five GET routes, each a thin wrapper around :func:`_serve_artefact`.
# These exist purely so operators (and the frontend inspector) can
# view Stage 2 outputs written by the orchestrator. **No** orchestration,
# no agent invocation, no document generation, no Claude calls — the
# routes only read files that the Stage 2 pipeline has already produced.
#
# URL convention: kebab-case in the URL ↔ snake_case in the filename.
# ``artefacts/critic-report`` deliberately matches ``critic_report.json``
# (rather than the shorter ``critic``) so future companion artefacts
# (e.g. ``critic_followup.json``) can take the obvious URL without a
# rename.

@router.get("/{job_id}/artefacts/product-mapping")
def get_product_mapping(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``product_mapping.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="product_mapping.json",
        reader=read_product_mapping,
    )


@router.get("/{job_id}/artefacts/benefits")
def get_benefits(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``benefits.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="benefits.json",
        reader=read_benefits,
    )


@router.get("/{job_id}/artefacts/faq")
def get_faq(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``faq.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="faq.json",
        reader=read_faq,
    )


@router.get("/{job_id}/artefacts/objections")
def get_objections(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``objections.json`` payload."""
    return _serve_artefact(
        job_id=job_id,
        artefact_name="objections.json",
        reader=read_objections,
    )


@router.get("/{job_id}/artefacts/critic-report")
def get_critic_report(
    job_id: str,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Return the raw ``critic_report.json`` payload.

    The :class:`Stage2CriticReport` validator re-runs on read,
    including the verdict/severity cross-field rule — a tampered
    file that pairs ``verdict = READY`` with a ``BLOCKING`` issue
    yields HTTP 500 (on-disk corruption), not a misleading 200.
    """
    return _serve_artefact(
        job_id=job_id,
        artefact_name="critic_report.json",
        reader=read_critic_report,
    )


# ---------------------------------------------------------------------------
# 7b. GET /api/jobs/{job_id}/brief/markdown — Step 31 viewer feed
# ---------------------------------------------------------------------------
#
# Step 31 exposes the Step 29 assembly output (``prospect_brief.md``) to
# the in-app viewer. Unlike the JSON artefact routes above, the body is
# raw UTF-8 Markdown — there is no Pydantic round-trip and no schema
# validation on the way out. We wrap the text in a small JSON envelope
# (`{"markdown": "..."}`) so the response keeps the same JSON-everywhere
# contract as the rest of the API and the frontend's ``request()``
# helper continues to parse with ``response.json()``.
#
# The route is strictly read-only: it does NOT trigger assembly, does
# NOT change the job status, does NOT append any transition, and does
# NOT touch ``state.json``. If ``prospect_brief.md`` is absent the
# response is 404. The static fence in
# ``tests/jobs_routes/test_no_production_client_or_keychain.py``
# continues to forbid importing :mod:`backend.assembly` from this
# module, which is why we route through the storage layer alone.

class ProspectBriefMarkdownResponse(BaseModel):
    """GET /api/jobs/{job_id}/brief/markdown response envelope.

    The Markdown body is returned verbatim under the ``markdown`` key.
    A wrapper object (rather than ``text/markdown``) keeps the response
    inside the project's JSON-everywhere contract and lets future
    additions (e.g. ``manifest`` provenance) extend the shape without
    breaking existing clients.
    """

    model_config = ConfigDict(extra="forbid")

    markdown: str


@router.get(
    "/{job_id}/brief/markdown",
    response_model=ProspectBriefMarkdownResponse,
)
def get_prospect_brief_markdown(
    job_id: str,
    _username: str = Depends(current_username),
) -> ProspectBriefMarkdownResponse:
    """Return the assembled ``prospect_brief.md`` as a JSON envelope.

    Error mapping mirrors the other artefact reads:

    * non-UUID ``job_id``        → 404
    * file absent (JobNotFound)  → 404

    There is no 500 case here: the storage reader returns raw UTF-8
    bytes without parsing, so there is no corrupt-on-disk path to map.
    """
    try:
        text = read_prospect_brief_markdown(job_id)
    except JobNotFound as exc:
        raise _http_404(str(exc)) from exc
    except ValueError as exc:
        # _validate_job_id raised ValueError on a non-UUID job_id.
        raise _http_404(f"job not found: {exc}") from exc
    return ProspectBriefMarkdownResponse(markdown=text)


# ---------------------------------------------------------------------------
# 7c. GET /api/jobs/{job_id}/manifest — Step 34 provenance feed
# ---------------------------------------------------------------------------
#
# Step 34 exposes the Step 29 assembly companion (``document_manifest.json``)
# to the in-app provenance viewer. Like the ``/brief/markdown`` route above
# this is strictly read-only: it does NOT trigger assembly, does NOT change
# job status, does NOT append any transition, and does NOT touch
# ``state.json``. If ``document_manifest.json`` is absent the response is
# 404; if the file exists but cannot be parsed as JSON the response is 500
# with ``detail.reason == "manifest_corrupt"`` (mirroring the corrupt-on-disk
# pattern used by the other artefact reads).
#
# Fence note
# ----------
# The route deliberately does NOT import :mod:`backend.assembly` — the
# manifest is on-disk JSON and the read goes through the storage layer
# only. The existing static fence in
# ``tests/jobs_routes/test_no_production_client_or_keychain.py`` continues
# to apply unchanged; the route-count assertion there bumps from 18 → 19
# as a deliberate surface change reviewed in Step 34.

class DocumentManifestResponse(BaseModel):
    """GET /api/jobs/{job_id}/manifest response envelope.

    The Step 29 manifest is a plain ``dict`` with a documented set of
    keys (``schema_version``, ``job_id``, ``company_name``,
    ``company_url``, ``generated_at``, ``markdown_filename``,
    ``markdown_sha256``, ``markdown_byte_length``, ``sections``,
    ``artefacts``, ``outputs``, ``critic_verdict``, ``warnings``). We
    pass it through verbatim under the ``manifest`` key — the
    assembler is the single source of truth for the shape, and
    re-modelling those fields in a typed Pydantic shape here would
    only invite drift. ``extra="forbid"`` on the envelope still
    blocks accidental top-level additions.
    """

    model_config = ConfigDict(extra="forbid")

    manifest: dict[str, Any]


@router.get(
    "/{job_id}/manifest",
    response_model=DocumentManifestResponse,
)
def get_document_manifest(
    job_id: str,
    _username: str = Depends(current_username),
) -> DocumentManifestResponse:
    """Return the Step 29 ``document_manifest.json`` as a JSON envelope.

    Error mapping:

    * non-UUID ``job_id``                → 404
    * file absent (JobNotFound)          → 404
    * file present but malformed JSON    → 500 with
      ``detail = {"reason": "manifest_corrupt"}``
    """
    try:
        manifest = read_document_manifest(job_id)
    except JobNotFound as exc:
        raise _http_404(str(exc)) from exc
    except json.JSONDecodeError as exc:
        log.exception("document_manifest.json corrupt for job %s", job_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "manifest_corrupt"},
        ) from exc
    except ValueError as exc:
        # _validate_job_id raised ValueError on a non-UUID job_id.
        # Matched last because JSONDecodeError subclasses ValueError.
        raise _http_404(f"job not found: {exc}") from exc
    return DocumentManifestResponse(manifest=manifest)


# ---------------------------------------------------------------------------
# 8. POST /api/jobs/{job_id}/approval/open
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approval/open",
    response_model=TransitionAppliedResponse,
)
def post_open_for_editing(
    job_id: str,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> TransitionAppliedResponse:
    """Transition ``briefing_ready → user_editing``."""
    # Guard: job must exist before approval calls (so a bad job_id is 404
    # not 409 from the state-machine).
    _job_state_or_404(job_id)
    try:
        result = open_for_editing(db=db, job_id=job_id)
    except EditNotAllowed as exc:
        raise _http_409(str(exc)) from exc
    except IllegalTransition as exc:
        raise _http_409(str(exc)) from exc
    return _transition_response(result)


# ---------------------------------------------------------------------------
# 8. PATCH /api/jobs/{job_id}/briefing
# ---------------------------------------------------------------------------

@router.patch("/{job_id}/briefing")
def patch_briefing(
    job_id: str,
    patch: BriefingPatch,
    _username: str = Depends(current_username),
) -> dict[str, Any]:
    """Merge ``patch`` into the on-disk briefing and re-validate.

    Returns the full updated briefing on success.

    Error mapping:
      * EditNotAllowed (wrong state)  → 409
      * JobNotFound (briefing absent)  → 404
      * ValidationError (merged briefing rejects)  → 422
      * JSONDecodeError (briefing corrupt)  → 500
    """
    _job_state_or_404(job_id)
    try:
        updated = apply_briefing_edit(job_id=job_id, patch=patch)
    except EditNotAllowed as exc:
        raise _http_409(str(exc)) from exc
    except JobNotFound as exc:
        raise _http_404(str(exc)) from exc
    except ValidationError as exc:
        # The merged briefing failed top-level revalidation.
        # 422 is right here: the patch (the request) introduced the
        # problem, and the caller can fix it and retry. We serialise
        # the error list through ``exc.json()`` so any nested
        # ``HttpUrl`` / non-JSON-native objects in ``ctx``/``input``
        # round-trip safely through the response encoder.
        errors = json.loads(exc.json(include_url=False))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "patch rejected by briefing validator",
                    "errors": errors},
        ) from exc
    except json.JSONDecodeError as exc:
        log.exception("briefing.json corrupt for job %s", job_id)
        raise _http_500_corrupt("briefing.json") from exc
    return updated.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 9. POST /api/jobs/{job_id}/approval/approve
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approval/approve",
    response_model=TransitionAppliedResponse,
)
def post_approve(
    job_id: str,
    username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> TransitionAppliedResponse:
    """Approve the briefing for Stage 2.

    The ``approved_by`` value comes from the authenticated session
    username — there is no request body. The approval module reloads
    and Pydantic-validates ``briefing.json``; if it fails, the
    transition is aborted and the on-disk state is unchanged.
    """
    _job_state_or_404(job_id)
    try:
        result = approve(db=db, job_id=job_id, approved_by=username)
    except EditNotAllowed as exc:
        raise _http_409(str(exc)) from exc
    except IllegalTransition as exc:
        raise _http_409(str(exc)) from exc
    except JobNotFound as exc:
        # briefing.json missing — the state guard passed but the
        # artefact has been deleted under us.
        raise _http_404(str(exc)) from exc
    except ValidationError as exc:
        log.exception("briefing.json failed revalidation for job %s", job_id)
        raise _http_500_corrupt("briefing.json") from exc
    except json.JSONDecodeError as exc:
        log.exception("briefing.json unparsable for job %s", job_id)
        raise _http_500_corrupt("briefing.json") from exc
    return _transition_response(result)


# ---------------------------------------------------------------------------
# 10. POST /api/jobs/{job_id}/approval/regenerate/request
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approval/regenerate/request",
    response_model=TransitionAppliedResponse,
)
def post_request_regeneration(
    job_id: str,
    body: RequestRegenerationBody,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> TransitionAppliedResponse:
    """Transition ``user_editing → regenerating_section``.

    No agent is invoked. This route only records the operator's
    intent; the regeneration worker that consumes
    ``regenerating_section`` jobs is out of scope for Step 11.
    """
    _job_state_or_404(job_id)
    try:
        result = request_regeneration(
            db=db,
            job_id=job_id,
            section=body.section,
            instructions=body.instructions,
        )
    except EditNotAllowed as exc:
        raise _http_409(str(exc)) from exc
    except IllegalTransition as exc:
        raise _http_409(str(exc)) from exc
    except ValueError as exc:
        # approval.request_regeneration raises ValueError on empty
        # instructions — Field(min_length=1) on the body schema makes
        # this defence-in-depth, but we still map it cleanly.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _transition_response(result)


# ---------------------------------------------------------------------------
# 11. POST /api/jobs/{job_id}/approval/regenerate/complete
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approval/regenerate/complete",
    response_model=TransitionAppliedResponse,
)
def post_complete_regeneration(
    job_id: str,
    body: CompleteRegenerationBody,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> TransitionAppliedResponse:
    """Transition ``regenerating_section → user_editing``.

    Accepts the full updated :class:`Briefing` produced by the
    (out-of-scope) regeneration agent. We validate the identity
    (``company_name`` and ``company_url``) matches ``state.json``
    before persisting — a briefing for the wrong company is a fatal
    protocol error, never a successful regeneration.

    Writes the new briefing first; only on a successful write do we
    apply the state transition. A failed write therefore leaves the
    job in ``regenerating_section`` for retry rather than silently
    dropping back to ``user_editing`` with stale content.
    """
    from backend.jobs.storage import write_briefing

    snapshot = _job_state_or_404(job_id)

    # Identity check before any side effects.
    new_briefing = body.briefing
    if new_briefing.company_name != snapshot.company_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "field": "briefing.company_name",
                "reason": (
                    "regenerated briefing's company_name does not match "
                    "the job's state.json"
                ),
            },
        )
    if str(new_briefing.company_url) != snapshot.company_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "field": "briefing.company_url",
                "reason": (
                    "regenerated briefing's company_url does not match "
                    "the job's state.json"
                ),
            },
        )

    # Guard the state *before* writing — we don't want to overwrite
    # briefing.json for a job that's not in regenerating_section.
    if snapshot.current_state.value != "regenerating_section":
        raise _http_409(
            f"complete_regeneration not allowed for job {job_id}: "
            f"current state is {snapshot.current_state.value} "
            f"(expected one of: regenerating_section)"
        )

    write_briefing(job_id, new_briefing)

    try:
        result = complete_regeneration(
            db=db, job_id=job_id, section=body.section,
        )
    except EditNotAllowed as exc:
        # Race: state changed between snapshot and call.
        raise _http_409(str(exc)) from exc
    except IllegalTransition as exc:
        raise _http_409(str(exc)) from exc
    return _transition_response(result)


# ---------------------------------------------------------------------------
# 12. POST /api/jobs/{job_id}/approval/regenerate/fail
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approval/regenerate/fail",
    response_model=TransitionAppliedResponse,
)
def post_fail_regeneration(
    job_id: str,
    body: FailRegenerationBody,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> TransitionAppliedResponse:
    """Transition ``regenerating_section → failed`` and record the error."""
    _job_state_or_404(job_id)
    try:
        result = fail_regeneration(
            db=db,
            job_id=job_id,
            category=body.category,
            details=body.details,
        )
    except EditNotAllowed as exc:
        raise _http_409(str(exc)) from exc
    except IllegalTransition as exc:
        raise _http_409(str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _transition_response(result)
