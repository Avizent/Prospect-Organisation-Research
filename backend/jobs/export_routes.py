"""HTTP API for deterministic export generation + retrieval.

Scope
-----
Exposes four routes — two per supported format:

    POST /api/jobs/{job_id}/export/pdf
    POST /api/jobs/{job_id}/export/docx
    GET  /api/jobs/{job_id}/exports/pdf
    GET  /api/jobs/{job_id}/exports/docx

The POST routes are thin HTTP adapters over the pure renderers in
:mod:`backend.exporters.pdf` and :mod:`backend.exporters.docx` — they
gate on job state and markdown integrity, render deterministic bytes,
atomically write them to
``~/.ans-tool/jobs/{job_id}/exports/prospect_brief.<fmt>``, and
atomically upsert an entry into the manifest's ``exports[]`` array.

The GET routes are strictly read-only: they read the bytes off disk
and stream them back. They do NOT rewrite the manifest, do NOT
recompute provenance, and do NOT touch ``state.json``. This property
is pinned by
``tests/jobs_routes/test_export_pdf_routes.py::test_pdf_retrieval_does_not_touch_manifest_mtime``
and the matching DOCX test in ``test_export_docx_routes.py``.

Step 39 consolidation
---------------------

Steps 36 and 38 landed the PDF and DOCX renderers as two near-
identical route bodies. Step 39 collapses the duplication behind
three internal helpers:

  * :func:`_generate_export` — the shared POST orchestration: state
    check, manifest read, markdown-drift check, renderer call, atomic
    write, manifest upsert.
  * :func:`_retrieve_export` — the shared GET orchestration: state
    check, bytes read, stale-header projection, ETag/304 handling,
    response framing.
  * :func:`_compute_stale_headers` — the per-entry staleness
    projection used by both retrieval paths. Lifted verbatim from the
    Step-36 PDF handler to preserve behaviour.

Renderer-specific knowledge (which function to call, which media
type to stream, which filename to advertise, whether to emit a
``template_sha256`` provenance field) lives in
:data:`backend.exporters.registry.EXPORT_RENDERERS`. The four public
routes stay as named handlers so FastAPI emits stable per-format
OpenAPI shapes and so the static fence at
``tests/jobs_routes/test_export_routes_static_fence.py`` can pin
their count at four.

Fence relaxation
----------------
This module imports :mod:`backend.exporters` (pdf, docx, base,
determinism, lifecycle, registry) — these are allowed. A static
fence at ``tests/jobs_routes/test_export_routes_static_fence.py``
enforces the rule with AST analysis + substring scanning, and
includes a positive-shape assertion that both the exporters import
and the registry import are present.

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
  ``record_failure`` — the route reads ``state.json`` only.

Precondition checks (POST)
--------------------------
Before rendering, the helper validates four preconditions in order. A
failure is a 4xx response and NO files are written:

1. The job exists (``state.json`` is readable).        → 404
2. ``current_state == "approved"``.                    → 409
3. ``document_manifest.json`` exists.                  → 409
4. The Markdown on disk hashes to the manifest's
   recorded ``markdown_sha256``.                       → 400

The fourth check defends against on-disk drift between the
assembler's last write and a manual edit of ``prospect_brief.md``
underneath us. If the hashes disagree the export would carry stale
content with a stale ``source_markdown_sha256`` — better to refuse
and force the caller to re-assemble.

201 vs 200 semantics
--------------------
* ``201 Created`` — the export file did not exist before the call.
* ``200 OK``     — the export file existed (idempotent regeneration).

Body shape is identical in both cases.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from backend.auth.sessions import current_username
from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.exporters.base import ExportError
from backend.exporters.determinism import sha256_hex
from backend.exporters.lifecycle import (
    compute_export_lifecycle,
    compute_manifest_lifecycle,
)
from backend.exporters.registry import EXPORT_RENDERERS, RendererSpec
from backend.jobs.state import JobState
from backend.jobs.storage import (
    JobNotFound,
    export_path_for,
    read_document_manifest,
    read_export_bytes,
    read_prospect_brief_markdown,
    read_state,
    upsert_export_entry,
    write_export,
)


log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs", "exports"])


# ---------------------------------------------------------------------------
# Route formats — declared as a module-level frozenset so the invariant
# test ``test_registry_formats_match_route_formats`` can pin it against
# the registry / lifecycle / storage frozensets in one assert.
# ---------------------------------------------------------------------------

ROUTE_FORMATS: frozenset[str] = frozenset({"pdf", "docx"})


# ---------------------------------------------------------------------------
# Brand asset loader
# ---------------------------------------------------------------------------
#
# The ANS logo is a mandatory brand asset; the prospect logo is
# optional and degrades to "no co-brand". Both are resolved relative
# to the repo root so the production install reads from
# ``ans_knowledge/brand/``. We do NOT take a base path as a parameter
# — the asset's location is a project constant, not a per-request
# input.

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_ANS_LOGO_PATH: Path = _REPO_ROOT / "ans_knowledge" / "brand" / "ans_logo.png"


def _read_ans_logo_bytes() -> bytes:
    """Return the ANS brand logo bytes.

    Raises :class:`HTTPException` 500 with ``reason=missing_brand_asset``
    if the file is absent — the exporter would raise the same reason,
    but we surface it as a route-layer 500 here so the operator gets
    a clear "fix your install" message rather than a renderer
    backtrace.
    """
    if not _ANS_LOGO_PATH.exists():
        raise _http_500(
            "missing_brand_asset",
            message=f"ANS logo not found at {_ANS_LOGO_PATH}",
        )
    return _ANS_LOGO_PATH.read_bytes()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
#
# Two near-identical response models. Each is a thin projection of the
# manifest's ``exports[]`` entry onto the wire. ``ExportDocxResponse``
# carries one additional Step-38 provenance field — ``template_sha256``
# — which records the SHA-256 of the loaded ``base.docx`` template;
# PDF entries deliberately do NOT carry the field because the PDF
# template lives in plain-text HTML/CSS source whose drift is already
# covered by ``template_version``.

class ExportPdfResponse(BaseModel):
    """POST /api/jobs/{job_id}/export/pdf response."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    format: str
    filename: str
    byte_length: int
    sha256: str
    source_markdown_sha256: str
    generated_at: str
    exporter_version: str
    template_version: str
    renderer: dict[str, str]
    markdown_renderer: dict[str, str]
    regeneration_count: int
    warnings: list[str]


class ExportDocxResponse(BaseModel):
    """POST /api/jobs/{job_id}/export/docx response.

    Mirrors :class:`ExportPdfResponse` plus the ``template_sha256``
    provenance field (Step 38 amendment 2).
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    format: str
    filename: str
    byte_length: int
    sha256: str
    source_markdown_sha256: str
    generated_at: str
    exporter_version: str
    template_version: str
    template_sha256: str
    renderer: dict[str, str]
    markdown_renderer: dict[str, str]
    regeneration_count: int
    warnings: list[str]


# ---------------------------------------------------------------------------
# Failure helpers
# ---------------------------------------------------------------------------

def _http_400(reason: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {"reason": reason}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=detail,
    )


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
# Shared generation helper
# ---------------------------------------------------------------------------

def _generate_export(
    *, fmt: str, job_id: str,
) -> tuple[dict[str, Any], bool]:
    """Run the shared POST orchestration for ``fmt`` on ``job_id``.

    Returns ``(final_entry, pre_existed)`` where ``final_entry`` is the
    post-upsert manifest entry (canonical projection over the wire) and
    ``pre_existed`` is the snapshot taken before writing — the caller
    chooses 200 vs 201 from this boolean.

    Raises :class:`HTTPException` with the documented status/reason
    pairs on any precondition failure. Behaviour is identical to the
    pre-Step-39 per-format handlers; this function is line-for-line the
    union of the two.
    """
    spec: RendererSpec = EXPORT_RENDERERS[fmt]

    # 1. Validate job exists.
    try:
        state = read_state(job_id)
    except JobNotFound as exc:
        raise _http_404("job_not_found", message=str(exc)) from exc
    except ValueError as exc:
        raise _http_404(
            "job_not_found", message=f"invalid job id: {exc}",
        ) from exc

    # 2. Validate approved state.
    if state.current_state is not JobState.APPROVED:
        raise _http_409(
            "job_not_approved",
            current_state=state.current_state.value,
            required_state=JobState.APPROVED.value,
        )

    # 3. Validate the manifest is on disk.
    try:
        manifest = read_document_manifest(job_id)
    except JobNotFound as exc:
        raise _http_409("manifest_required", message=str(exc)) from exc

    # 4. Validate markdown hasn't drifted under us.
    try:
        markdown_text = read_prospect_brief_markdown(job_id)
    except JobNotFound as exc:
        raise _http_409("brief_required", message=str(exc)) from exc

    on_disk_sha = sha256_hex(markdown_text.encode("utf-8"))
    expected_sha = manifest.get("markdown_sha256")
    if expected_sha != on_disk_sha:
        raise _http_400(
            "markdown_drift",
            expected=expected_sha,
            actual=on_disk_sha,
        )

    # 5. Snapshot existence so the caller can choose 201 vs 200.
    pre_existed = export_path_for(job_id, fmt).exists()

    # 6. Render.
    ans_logo = _read_ans_logo_bytes()
    try:
        result = spec.render(
            job_id=job_id,
            markdown_text=markdown_text,
            markdown_sha256=on_disk_sha,
            company_name=str(manifest.get("company_name") or ""),
            ans_logo_bytes=ans_logo,
            prospect_logo_bytes=b"",  # no per-prospect logo yet.
            now=datetime.now(timezone.utc),
        )
    except ExportError as exc:
        log.exception("%s render failed for job %s", fmt.upper(), job_id)
        raise _http_500(
            exc.reason,
            message=exc.detail or "render failed",
        ) from exc

    # 7. Write atomically: bytes first, then manifest. A crash between
    #    the two leaves an orphan file (invisible to retrieval because
    #    the manifest has no entry); the next successful run replaces
    #    it atomically.
    write_export(job_id, fmt, result.bytes)

    generated_at = _iso_utc(datetime.now(timezone.utc))
    entry: dict[str, Any] = {
        "format": fmt,
        "filename": spec.filename,
        "byte_length": len(result.bytes),
        "sha256": result.sha256,
        "source_markdown_sha256": on_disk_sha,
        "generated_at": generated_at,
        "exporter_version": EXPORTER_VERSION,
        "template_version": TEMPLATE_VERSION,
        "renderer": spec.renderer_provenance(),
        "markdown_renderer": spec.markdown_renderer_provenance(),
        "regeneration_count": 0,  # overwritten by upsert helper.
        "warnings": list(result.warnings),
    }
    if spec.template_sha256 is not None:
        entry["template_sha256"] = spec.template_sha256()

    final_entry = upsert_export_entry(job_id, entry)
    return final_entry, pre_existed


# ---------------------------------------------------------------------------
# Route — POST /export/pdf
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/export/pdf",
    response_model=ExportPdfResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_export_pdf(
    job_id: str,
    response: Response,
    _username: str = Depends(current_username),
) -> ExportPdfResponse:
    """Render and persist the deterministic PDF for ``job_id``."""
    final_entry, pre_existed = _generate_export(fmt="pdf", job_id=job_id)
    response.status_code = (
        status.HTTP_200_OK if pre_existed else status.HTTP_201_CREATED
    )
    return ExportPdfResponse(
        job_id=job_id,
        format=str(final_entry["format"]),
        filename=str(final_entry["filename"]),
        byte_length=int(final_entry["byte_length"]),
        sha256=str(final_entry["sha256"]),
        source_markdown_sha256=str(final_entry["source_markdown_sha256"]),
        generated_at=str(final_entry["generated_at"]),
        exporter_version=str(final_entry["exporter_version"]),
        template_version=str(final_entry["template_version"]),
        renderer=dict(final_entry["renderer"]),
        markdown_renderer=dict(final_entry["markdown_renderer"]),
        regeneration_count=int(final_entry["regeneration_count"]),
        warnings=list(final_entry["warnings"]),
    )


# ---------------------------------------------------------------------------
# Route — POST /export/docx
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/export/docx",
    response_model=ExportDocxResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_export_docx(
    job_id: str,
    response: Response,
    _username: str = Depends(current_username),
) -> ExportDocxResponse:
    """Render and persist the deterministic DOCX for ``job_id``."""
    final_entry, pre_existed = _generate_export(fmt="docx", job_id=job_id)
    response.status_code = (
        status.HTTP_200_OK if pre_existed else status.HTTP_201_CREATED
    )
    return ExportDocxResponse(
        job_id=job_id,
        format=str(final_entry["format"]),
        filename=str(final_entry["filename"]),
        byte_length=int(final_entry["byte_length"]),
        sha256=str(final_entry["sha256"]),
        source_markdown_sha256=str(final_entry["source_markdown_sha256"]),
        generated_at=str(final_entry["generated_at"]),
        exporter_version=str(final_entry["exporter_version"]),
        template_version=str(final_entry["template_version"]),
        template_sha256=str(final_entry["template_sha256"]),
        renderer=dict(final_entry["renderer"]),
        markdown_renderer=dict(final_entry["markdown_renderer"]),
        regeneration_count=int(final_entry["regeneration_count"]),
        warnings=list(final_entry["warnings"]),
    )


# ---------------------------------------------------------------------------
# Shared retrieval helper
# ---------------------------------------------------------------------------

def _compute_stale_headers(
    *, job_id: str, fmt: str,
) -> dict[str, str]:
    """Project per-entry staleness onto the three documented headers.

    Returns a dict with three keys — ``X-Export-Stale``,
    ``X-Export-Stale-Reasons``, ``X-Export-Source-Markdown-SHA256``.
    Degrades gracefully when the manifest is absent: ``stale=false``,
    no reasons, empty source-sha. This shape was finalised in Step 37
    and is pinned by the per-format ``*_stale_headers`` test modules.
    """
    stale_value = "false"
    stale_reasons_value = ""
    source_md_sha_value = ""

    try:
        manifest = read_document_manifest(job_id)
    except JobNotFound:
        manifest = None
    if not isinstance(manifest, dict):
        return {
            "X-Export-Stale": stale_value,
            "X-Export-Stale-Reasons": stale_reasons_value,
            "X-Export-Source-Markdown-SHA256": source_md_sha_value,
        }

    manifest_sha = manifest.get("markdown_sha256")
    manifest_sha_str = (
        manifest_sha if isinstance(manifest_sha, str) else None
    )
    try:
        md_text = read_prospect_brief_markdown(job_id)
    except JobNotFound:
        on_disk_sha: str | None = None
    else:
        on_disk_sha = sha256_hex(md_text.encode("utf-8"))

    manifest_lifecycle = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=on_disk_sha,
    )
    if manifest_sha_str:
        source_md_sha_value = manifest_sha_str

    entry: dict[str, Any] | None = None
    exports = manifest.get("exports")
    if isinstance(exports, list):
        for candidate in exports:
            if (
                isinstance(candidate, dict)
                and candidate.get("format") == fmt
            ):
                entry = candidate
                break
    if entry is not None:
        entry_lifecycle = compute_export_lifecycle(
            entry,
            manifest_markdown_sha256=manifest_sha_str,
            on_disk_markdown_sha256=on_disk_sha,
        )
        if entry_lifecycle["stale"]:
            stale_value = "true"
            stale_reasons_value = ",".join(entry_lifecycle["reasons"])
    elif manifest_lifecycle["source_markdown_drift"]:
        stale_value = "true"
        stale_reasons_value = "source_markdown_drift"

    return {
        "X-Export-Stale": stale_value,
        "X-Export-Stale-Reasons": stale_reasons_value,
        "X-Export-Source-Markdown-SHA256": source_md_sha_value,
    }


def _retrieve_export(
    *, fmt: str, job_id: str, request: Request,
) -> Response:
    """Stream the rendered ``fmt`` bytes back to the caller.

    Strictly read-only: does NOT touch the manifest, does NOT recompute
    provenance, and does NOT mutate ``state.json``. The ETag is
    computed from the on-disk file's sha256 so we can short-circuit
    304s without consulting the manifest.
    """
    spec: RendererSpec = EXPORT_RENDERERS[fmt]

    # 1. Validate job exists.
    try:
        read_state(job_id)
    except JobNotFound as exc:
        raise _http_404("job_not_found", message=str(exc)) from exc
    except ValueError as exc:
        raise _http_404(
            "job_not_found", message=f"invalid job id: {exc}",
        ) from exc

    # 2. Read the export bytes — 404 if absent.
    try:
        payload = read_export_bytes(job_id, fmt)
    except JobNotFound as exc:
        raise _http_404("export_not_found", message=str(exc)) from exc

    # 3. Compute the per-entry staleness projection so we can advertise
    #    it on response headers. Staleness is INFORMATIONAL — a stale
    #    export is still downloadable. The retrieval handler must not
    #    auto-regenerate.
    stale_headers = _compute_stale_headers(job_id=job_id, fmt=fmt)

    # 4. Compute ETag from the bytes.
    etag = f'"{sha256_hex(payload)}"'

    if request.headers.get("if-none-match") == etag:
        headers_304 = {"ETag": etag, **stale_headers}
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers=headers_304,
        )

    return Response(
        content=payload,
        media_type=spec.media_type,
        headers={
            "ETag": etag,
            "Content-Disposition": f'inline; filename="{spec.filename}"',
            **stale_headers,
        },
    )


# ---------------------------------------------------------------------------
# Route — GET /exports/pdf
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}/exports/pdf",
)
def get_export_pdf(
    job_id: str,
    request: Request,
    _username: str = Depends(current_username),
) -> Response:
    """Stream the rendered PDF bytes back to the caller."""
    return _retrieve_export(fmt="pdf", job_id=job_id, request=request)


# ---------------------------------------------------------------------------
# Route — GET /exports/docx
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}/exports/docx",
)
def get_export_docx(
    job_id: str,
    request: Request,
    _username: str = Depends(current_username),
) -> Response:
    """Stream the rendered DOCX bytes back to the caller."""
    return _retrieve_export(fmt="docx", job_id=job_id, request=request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_utc(dt: datetime) -> str:
    """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
