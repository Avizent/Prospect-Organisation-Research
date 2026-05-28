"""HTTP API for export lifecycle governance (Step 37).

Scope
-----
Exposes a single route:

    GET /api/jobs/{job_id}/exports/validate

The handler is strictly read-only: it walks
:func:`backend.exporters.lifecycle.validate_export_integrity` and
projects the resulting :class:`IntegrityIssue` list onto a JSON
envelope. It does NOT touch ``state.json``, does NOT rewrite the
manifest, and does NOT regenerate or remove any export bytes.

Fence relaxation
----------------
This module imports :mod:`backend.exporters.lifecycle`. Like
``backend.jobs.export_routes`` (Step 36), this is the ONE place in
``backend/jobs/`` allowed to do so — the relaxation is pinned by
``tests/jobs_routes/test_export_governance_routes_static_fence.py``.

Strict scope
------------
This module deliberately:

* does **not** import the Anthropic SDK,
* does **not** import :mod:`keyring` or :mod:`backend.credentials`,
* does **not** import :mod:`backend.delivery`,
* does **not** import :mod:`backend.cost_control.cloud_client`,
* does **not** import :mod:`backend.tools.cloud_client`,
* does **not** import :mod:`backend.orchestrator`,
* does **not** import :mod:`backend.assembly`,
* does **not** call ``append_transition`` / ``write_state`` /
  ``record_failure`` — governance is state-machine-invariant.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from backend.auth.sessions import current_username
from backend.exporters.lifecycle import (
    IntegrityIssue,
    validate_export_integrity,
)
from backend.jobs.storage import (
    JobNotFound,
    read_state,
)


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs", "exports", "governance"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class IntegrityIssueResponse(BaseModel):
    """One :class:`IntegrityIssue` projected onto the wire."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    message: str
    format: str = ""
    filename: str = ""


class ValidateExportsResponse(BaseModel):
    """GET /api/jobs/{job_id}/exports/validate response envelope."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    issues: list[IntegrityIssueResponse]


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------

def _http_404(reason: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=detail,
    )


# ---------------------------------------------------------------------------
# Route — GET /exports/validate
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}/exports/validate",
    response_model=ValidateExportsResponse,
)
def get_export_validation(
    job_id: str,
    _username: str = Depends(current_username),
) -> ValidateExportsResponse:
    """Walk the export integrity validator and return the issue list."""
    # 1. Validate job exists. We use ``read_state`` (not
    # ``read_document_manifest`` directly) so a job that hasn't
    # produced a manifest yet returns 404 with a clear reason rather
    # than the manifest-specific 404.
    try:
        read_state(job_id)
    except JobNotFound as exc:
        raise _http_404("job_not_found", message=str(exc)) from exc
    except ValueError as exc:
        raise _http_404(
            "job_not_found", message=f"invalid job id: {exc}",
        ) from exc

    # 2. Run the validator. A missing manifest is mapped to a clean
    # empty-issue response so the frontend can render "no manifest yet"
    # uniformly without branching on HTTP status.
    try:
        issues: list[IntegrityIssue] = validate_export_integrity(job_id)
    except JobNotFound as exc:
        raise _http_404(
            "manifest_not_found", message=str(exc),
        ) from exc

    return ValidateExportsResponse(
        job_id=job_id,
        issues=[
            IntegrityIssueResponse(**issue.as_dict()) for issue in issues
        ],
    )
