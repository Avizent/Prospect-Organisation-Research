"""Tests for ``POST /api/jobs/{job_id}/export/docx`` and
``GET /api/jobs/{job_id}/exports/docx`` (Step 38).

Mirrors :mod:`tests.jobs_routes.test_export_pdf_routes` line-for-line
with the format swapped, plus a Step 38 amendment 2 check:
``template_sha256`` MUST appear on the POST response body and equal
the on-disk template's SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.jobs.storage import (
    job_folder,
    write_document_manifest,
    write_prospect_brief_markdown,
)


_MARKDOWN_TEXT = (
    "# Acme Ltd\n\nBrief body for the DOCX export.\n\n"
    "## Snapshot\n\nHeadcount band 1000-5000.\n"
)


def _markdown_sha() -> str:
    return hashlib.sha256(_MARKDOWN_TEXT.encode("utf-8")).hexdigest()


@pytest.fixture()
def approved_job_ready_for_export(
    approved_job: str,
) -> Iterator[str]:
    job_id = approved_job
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
    return f"/api/jobs/{job_id}/export/docx"


def _get_path(job_id: str) -> str:
    return f"/api/jobs/{job_id}/exports/docx"


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
    assert body["format"] == "docx"
    assert body["filename"] == "prospect_brief.docx"
    assert body["source_markdown_sha256"] == _markdown_sha()
    assert body["regeneration_count"] == 0
    assert body["exporter_version"]
    assert body["template_version"]
    # Amendment 2: template_sha256 must be present.
    assert "template_sha256" in body
    assert isinstance(body["template_sha256"], str)
    assert len(body["template_sha256"]) == 64
    # Provenance dicts shaped.
    assert body["renderer"]["name"] == "python-docx"
    assert body["renderer"]["version"]
    assert body["markdown_renderer"]["name"] == "markdown"


def test_post_template_sha256_matches_on_disk_template(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """Amendment 2 — the wire value must equal the on-disk file's SHA-256."""
    from backend.exporters.docx import template_sha256

    r = authed_client.post(_post_path(approved_job_ready_for_export))
    assert r.status_code == 201
    assert r.json()["template_sha256"] == template_sha256()


def test_post_returns_200_on_regeneration_and_bumps_count(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(_post_path(job_id))
    assert r1.status_code == 201
    r2 = authed_client.post(_post_path(job_id))
    assert r2.status_code == 200
    assert r2.json()["regeneration_count"] == 1
    r3 = authed_client.post(_post_path(job_id))
    assert r3.status_code == 200
    assert r3.json()["regeneration_count"] == 2


def test_post_writes_docx_and_manifest_entry(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 201

    docx_path = job_folder(job_id) / "exports" / "prospect_brief.docx"
    assert docx_path.exists()
    # DOCX is a ZIP — magic bytes are "PK\x03\x04".
    assert docx_path.read_bytes().startswith(b"PK\x03\x04")

    manifest = json.loads(
        (job_folder(job_id) / "document_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(manifest["exports"]) == 1
    entry = manifest["exports"][0]
    assert entry["format"] == "docx"
    assert "template_sha256" in entry
    assert "markdown_renderer" in entry


def test_post_coexists_with_pdf_export_entry(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """A POST /export/pdf followed by POST /export/docx must yield TWO
    entries in the manifest's exports array, both intact."""
    job_id = approved_job_ready_for_export
    r_pdf = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    assert r_pdf.status_code == 201
    r_docx = authed_client.post(_post_path(job_id))
    assert r_docx.status_code == 201

    manifest = json.loads(
        (job_folder(job_id) / "document_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    formats = sorted(e["format"] for e in manifest["exports"])
    assert formats == ["docx", "pdf"]


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
    r = authed_client.post(_post_path(approved_job))
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] in {
        "manifest_required", "brief_required",
    }


def test_post_returns_400_on_markdown_drift(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
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


def test_get_returns_docx_bytes_after_post(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    authed_client.post(_post_path(job_id))
    r = authed_client.get(_get_path(job_id))
    assert r.status_code == 200
    assert r.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )
    assert r.content.startswith(b"PK\x03\x04")
    assert "etag" in r.headers


def test_get_returns_404_when_no_docx_yet(
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
# Read-only retrieval
# ---------------------------------------------------------------------------


def test_docx_retrieval_does_not_touch_manifest_mtime(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """``GET /exports/docx`` must be strictly read-only (mirrors PDF)."""
    job_id = approved_job_ready_for_export
    r = authed_client.post(_post_path(job_id))
    assert r.status_code == 201

    manifest_path = job_folder(job_id) / "document_manifest.json"
    mtime_before = os.stat(manifest_path).st_mtime_ns
    bytes_before = manifest_path.read_bytes()

    time.sleep(0.01)

    r = authed_client.get(_get_path(job_id))
    assert r.status_code == 200
    etag = r.headers["etag"]
    r304 = authed_client.get(
        _get_path(job_id), headers={"If-None-Match": etag},
    )
    assert r304.status_code == 304

    mtime_after = os.stat(manifest_path).st_mtime_ns
    bytes_after = manifest_path.read_bytes()
    assert mtime_after == mtime_before
    assert bytes_after == bytes_before
