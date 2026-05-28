"""Step 38 — ``GET /api/jobs/{id}/exports/docx`` advertises staleness on headers.

Mirrors :mod:`tests.jobs_routes.test_export_pdf_stale_headers` with
the format swapped. The DOCX retrieval handler is documented as
strictly read-only, so it must not auto-regenerate a stale export.
Instead, it advertises staleness on three response headers so a
caller can render a "regenerate" prompt without a second round-trip:

  * ``X-Export-Stale`` — ``"true"`` / ``"false"``.
  * ``X-Export-Stale-Reasons`` — comma-separated taxonomy codes
    (empty when not stale).
  * ``X-Export-Source-Markdown-SHA256`` — the manifest's recorded
    Markdown SHA-256 (empty when no manifest is on disk).

The headers ride on BOTH the 200 byte response AND the 304 ETag
short-circuit so an ETag-aware client still gets the staleness signal.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.jobs.storage import (
    write_document_manifest,
    write_export,
    write_prospect_brief_markdown,
)


_MD_TEXT = "# Acme Ltd\n\nStale header fixture.\n"
_MD_BYTES = _MD_TEXT.encode("utf-8")
_MD_SHA = hashlib.sha256(_MD_BYTES).hexdigest()

# DOCX is a ZIP — a stub file with valid magic bytes is enough for the
# read-only handler since it never opens the ZIP.
_DOCX_BYTES = b"PK\x03\x04 stale headers fixture\n"
_DOCX_SHA = hashlib.sha256(_DOCX_BYTES).hexdigest()
# A pinned (fake) template sha so the entry is shaped exactly like the
# real renderer would emit. The header logic does not inspect it.
_TEMPLATE_SHA = "f" * 64


def _entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "docx",
        "filename": "prospect_brief.docx",
        "byte_length": len(_DOCX_BYTES),
        "sha256": _DOCX_SHA,
        "source_markdown_sha256": _MD_SHA,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": EXPORTER_VERSION,
        "template_version": TEMPLATE_VERSION,
        "template_sha256": _TEMPLATE_SHA,
        "renderer": {"name": "python-docx", "version": "1.2.0"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "regeneration_count": 0,
        "warnings": [],
    }
    base.update(overrides)
    return base


def _seed(
    job_id: str,
    *,
    entry_overrides: dict[str, Any] | None = None,
    write_docx: bool = True,
    write_manifest: bool = True,
) -> None:
    write_prospect_brief_markdown(job_id, _MD_TEXT)
    if write_manifest:
        manifest: dict[str, Any] = {
            "schema_version": 2,
            "job_id": job_id,
            "company_name": "Acme Ltd",
            "company_url": "https://acme.example.com/",
            "generated_at": "2026-05-28T00:00:00Z",
            "markdown_filename": "prospect_brief.md",
            "markdown_sha256": _MD_SHA,
            "markdown_byte_length": len(_MD_BYTES),
            "sections": [],
            "artefacts": {},
            "outputs": [
                {"key": "markdown", "filename": "prospect_brief.md"},
                {"key": "manifest", "filename": "document_manifest.json"},
            ],
            "critic_verdict": None,
            "warnings": [],
            "exports": [_entry(**(entry_overrides or {}))],
        }
        write_document_manifest(job_id, manifest)
    if write_docx:
        write_export(job_id, "docx", _DOCX_BYTES)


def _path(job_id: str) -> str:
    return f"/api/jobs/{job_id}/exports/docx"


# ---------------------------------------------------------------------------
# Header presence
# ---------------------------------------------------------------------------


def test_clean_state_advertises_stale_false(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    assert r.headers["X-Export-Stale"] == "false"
    assert r.headers["X-Export-Stale-Reasons"] == ""
    assert r.headers["X-Export-Source-Markdown-SHA256"] == _MD_SHA


def test_source_markdown_drift_surfaces_on_headers(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Tamper with the Markdown — the entry projection picks up the drift."""
    _seed(approved_job)
    write_prospect_brief_markdown(approved_job, _MD_TEXT + "edit\n")
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    assert r.headers["X-Export-Stale"] == "true"
    assert "source_markdown_drift" in r.headers["X-Export-Stale-Reasons"]


def test_entry_drift_surfaces_on_headers(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Entry rendered from an earlier Markdown sha — entry-drift reason."""
    _seed(
        approved_job,
        entry_overrides={"source_markdown_sha256": "f" * 64},
    )
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    assert r.headers["X-Export-Stale"] == "true"
    assert "source_entry_drift" in r.headers["X-Export-Stale-Reasons"]


def test_stale_headers_ride_on_304_response(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """An ETag-aware client must still see the staleness signal on a 304."""
    _seed(approved_job)
    # Force the entry to be stale so we can pin both ``true`` and ``false``.
    write_prospect_brief_markdown(approved_job, _MD_TEXT + "edit\n")
    r1 = authed_client.get(_path(approved_job))
    etag = r1.headers["etag"]

    r2 = authed_client.get(
        _path(approved_job), headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.headers["X-Export-Stale"] == "true"
    assert "source_markdown_drift" in r2.headers["X-Export-Stale-Reasons"]


# ---------------------------------------------------------------------------
# Manifest absent — degrades gracefully
# ---------------------------------------------------------------------------


def test_manifest_absent_falls_back_to_false(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """No manifest yet — staleness defaults to ``false`` rather than 500."""
    # Write the DOCX but no manifest.
    write_export(approved_job, "docx", _DOCX_BYTES)
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    assert r.headers["X-Export-Stale"] == "false"
    assert r.headers["X-Export-Stale-Reasons"] == ""
    assert r.headers["X-Export-Source-Markdown-SHA256"] == ""
