"""Tests for ``POST /api/jobs/{job_id}/export/pdf`` and
``GET /api/jobs/{job_id}/exports/pdf`` (Step 36).

Covers:

  * Happy path POST: 201 → 200 idempotent regeneration, with
    ``regeneration_count`` incrementing on the manifest.
  * Failure modes: 404 (unknown job / non-UUID), 409 (state not
    approved / manifest missing), 400 (markdown drift), 401
    (anonymous).
  * GET returns the bytes verbatim with ``application/pdf``.
  * GET supports ETag / 304.
  * **Required behavioural test**:
    ``test_pdf_retrieval_does_not_touch_manifest_mtime`` — pins that
    GET is strictly read-only with respect to ``document_manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.jobs.storage import (
    job_folder,
    write_document_manifest,
    write_prospect_brief_markdown,
)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------

_MARKDOWN_TEXT = (
    "# Acme Ltd\n\nBrief body for the PDF export.\n\n"
    "## Snapshot\n\nHeadcount band 1000-5000.\n"
)


def _markdown_sha() -> str:
    return hashlib.sha256(_MARKDOWN_TEXT.encode("utf-8")).hexdigest()


@pytest.fixture()
def approved_job_ready_for_export(
    approved_job: str,
) -> Iterator[str]:
    """An APPROVED job with a v2 manifest + prospect_brief.md on disk."""
    job_id = approved_job
    folder = job_folder(job_id)
    write_prospect_brief_markdown(job_id, _MARKDOWN_TEXT)
    manifest = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": _markdown_sha(),
        "markdown_byte_length": len(_MARKDOWN_TEXT.encode("utf-8")),
        "sections": [],
        "artefacts": {},
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": "READY",
        "warnings": [],
        "exports": [],
    }
    write_document_manifest(job_id, manifest)
    yield job_id


def _post_path(job_id: str) -> str:
    return f"/api/jobs/{job_id}/export/pdf"


def _get_path(job_id: str) -> str:
    return f"/api/jobs/{job_id}/exports/pdf"


# ---------------------------------------------------------------------------
# Happy path — POST
# ---------------------------------------------------------------------------


def test_post_returns_201_on_first_generation(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "pdf"
    assert body["filename"] == "prospect_brief.pdf"
    assert body["source_markdown_sha256"] == _markdown_sha()
    assert body["regeneration_count"] == 0
    assert body["exporter_version"]
    assert body["template_version"]
    # Provenance dicts are present and shaped.
    assert "name" in body["renderer"] and "version" in body["renderer"]
    assert (
        "name" in body["markdown_renderer"]
        and "version" in body["markdown_renderer"]
    )


def test_post_returns_200_on_regeneration_and_bumps_count(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """Step 40 — append-only lineage with deterministic idempotency.

    The PDF renderer is byte-deterministic over a fixed (markdown,
    sha) input, so repeated POSTs with the same source must NOT append
    new lineage entries; ``regeneration_count`` stays at 0 and
    ``regenerated`` is ``False`` on the no-op calls. The 200 status on
    r2/r3 reflects "alias file pre-existed" (idempotent retrieval
    semantics), which is independent of whether the lineage advanced.

    To exercise an actual append we mutate the source markdown between
    calls and refresh the manifest's recorded ``markdown_sha256`` so
    the precondition check still passes — only then does the renderer
    produce different bytes, the new entry appends, and the counter
    bumps to 1.
    """
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(_post_path(job_id))
    assert r1.status_code == 201
    assert r1.json()["regeneration_count"] == 0
    assert r1.json()["regenerated"] is True
    assert r1.json()["version"] == 1

    r2 = authed_client.post(_post_path(job_id))
    assert r2.status_code == 200
    # Same source → same bytes → idempotent no-op under Step 40.
    assert r2.json()["regeneration_count"] == 0
    assert r2.json()["regenerated"] is False
    assert r2.json()["version"] == 1

    # Mutate the source so the renderer produces different bytes; the
    # manifest's recorded markdown_sha256 must follow or the
    # markdown_drift precondition would 400.
    new_md = _MARKDOWN_TEXT + "\nAnother paragraph.\n"
    new_sha = hashlib.sha256(new_md.encode("utf-8")).hexdigest()
    write_prospect_brief_markdown(job_id, new_md)
    manifest_path = job_folder(job_id) / "document_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["markdown_sha256"] = new_sha
    manifest["markdown_byte_length"] = len(new_md.encode("utf-8"))
    write_document_manifest(job_id, manifest)

    r3 = authed_client.post(_post_path(job_id))
    assert r3.status_code == 200
    assert r3.json()["regeneration_count"] == 1
    assert r3.json()["regenerated"] is True
    assert r3.json()["version"] == 2
    assert r3.json()["supersedes"] == r1.json()["sha256"]


def test_post_writes_pdf_and_manifest_entry(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 201

    pdf_path = job_folder(job_id) / "exports" / "prospect_brief.pdf"
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    manifest = json.loads(
        (job_folder(job_id) / "document_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(manifest["exports"]) == 1
    assert manifest["exports"][0]["format"] == "pdf"
    assert "markdown_renderer" in manifest["exports"][0]


# ---------------------------------------------------------------------------
# Failure modes — POST
# ---------------------------------------------------------------------------


def test_post_returns_404_for_unknown_job(
    authed_client: TestClient,
) -> None:
    unknown = "00000000-0000-4000-8000-deadbeefdead"
    r = authed_client.post(_post_path(unknown))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_post_returns_404_for_non_uuid_job_id(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(_post_path("not-a-uuid"))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_post_returns_409_when_job_not_approved(
    authed_client: TestClient,
    briefing_ready_job: str,
) -> None:
    r = authed_client.post(_post_path(briefing_ready_job))
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "job_not_approved"


def test_post_returns_409_when_manifest_missing(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Approved but no manifest yet (assemble hasn't been run)."""
    r = authed_client.post(_post_path(approved_job))
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] in {"manifest_required", "brief_required"}


def test_post_returns_400_on_markdown_drift(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """Tamper with prospect_brief.md after the manifest is written —
    the on-disk sha will no longer match the manifest's recorded sha."""
    job_id = approved_job_ready_for_export
    write_prospect_brief_markdown(job_id, "# Acme Ltd\n\nTampered.\n")
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["reason"] == "markdown_drift"
    assert detail["expected"] == _markdown_sha()


def test_post_requires_authentication(
    anon_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    r = anon_client.post(_post_path(approved_job_ready_for_export))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Happy path — GET
# ---------------------------------------------------------------------------


def test_get_returns_pdf_bytes_after_post(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    authed_client.post(_post_path(job_id))
    r = authed_client.get(_get_path(job_id))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert "etag" in r.headers


def test_get_returns_404_when_no_pdf_yet(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    r = authed_client.get(_get_path(approved_job_ready_for_export))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "export_not_found"


def test_get_returns_404_for_unknown_job(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(_get_path("00000000-0000-4000-8000-deadbeefdead"))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_get_supports_etag_304(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    authed_client.post(_post_path(job_id))
    r = authed_client.get(_get_path(job_id))
    etag = r.headers["etag"]
    r2 = authed_client.get(
        _get_path(job_id), headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304


def test_get_requires_authentication(
    anon_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    r = anon_client.get(_get_path(approved_job_ready_for_export))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Required behavioural test (per amendment 3)
# ---------------------------------------------------------------------------


def test_pdf_retrieval_does_not_touch_manifest_mtime(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """``GET /api/jobs/{id}/exports/pdf`` must be strictly read-only.

    We POST once to materialise the PDF + manifest entry, then
    snapshot the manifest file's mtime and content, perform several
    GETs (including an ETag/304 round-trip), and assert the manifest
    mtime AND content are byte-identical afterwards.
    """
    job_id = approved_job_ready_for_export
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 201

    manifest_path = job_folder(job_id) / "document_manifest.json"
    mtime_before = os.stat(manifest_path).st_mtime_ns
    bytes_before = manifest_path.read_bytes()

    # A small sleep so a hypothetical rewrite would land on a
    # measurably different mtime even on coarse-grained filesystems.
    time.sleep(0.01)

    r = authed_client.get(_get_path(job_id))
    assert r.status_code == 200
    etag = r.headers["etag"]
    # And a 304 round-trip.
    r304 = authed_client.get(
        _get_path(job_id), headers={"If-None-Match": etag},
    )
    assert r304.status_code == 304

    mtime_after = os.stat(manifest_path).st_mtime_ns
    bytes_after = manifest_path.read_bytes()

    assert mtime_after == mtime_before, (
        "GET /exports/pdf must not rewrite document_manifest.json "
        f"(mtime moved from {mtime_before} → {mtime_after})"
    )
    assert bytes_after == bytes_before, (
        "GET /exports/pdf must not mutate document_manifest.json content"
    )
