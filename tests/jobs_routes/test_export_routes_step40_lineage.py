"""Step 40 — append-only lineage invariants at the HTTP boundary.

The Step 36/38 route tests cover the bulk of the export surface; this
module pins the new Step 40 contracts that didn't exist before:

  * the POST response carries ``export_id``, ``version``, ``supersedes``,
    ``manifest_sha256_at_export`` and ``regenerated``;
  * the POST and GET routes emit the ``X-Export-Version`` header;
  * a regeneration with new bytes writes a per-version archive copy
    under ``exports/archive/prospect_brief.v{N}.{fmt}`` and leaves the
    prior archive copy intact (immutability);
  * the manifest's ``exports[]`` is append-only — both entries are
    present after two real regenerations, the older entry's fields are
    byte-identical to its earlier state, and ``supersedes`` points at
    the prior latest ``export_id``;
  * the manifest is materialised as canonical v3 after the first
    upsert and carries the ``latest_export_id`` pointer for the
    rendered format;
  * ``read_export_archive_bytes`` returns the historical bytes for
    superseded versions.

These invariants are the Step 40 design surface — drift in any of them
should fail this module first.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.jobs.storage import (
    job_folder,
    read_export_archive_bytes,
    write_document_manifest,
    write_prospect_brief_markdown,
)


_MARKDOWN_V1 = (
    "# Acme Ltd\n\nFirst version of the brief body.\n\n"
    "## Snapshot\n\nHeadcount band 1000-5000.\n"
)

_MARKDOWN_V2 = _MARKDOWN_V1 + "\nA second paragraph to force new bytes.\n"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def approved_job_ready_for_export(approved_job: str) -> Iterator[str]:
    """APPROVED job with v2 manifest + prospect_brief.md on disk."""
    job_id = approved_job
    write_prospect_brief_markdown(job_id, _MARKDOWN_V1)
    manifest = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": _sha256_text(_MARKDOWN_V1),
        "markdown_byte_length": len(_MARKDOWN_V1.encode("utf-8")),
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


def _refresh_markdown(job_id: str, new_md: str) -> None:
    """Replace the brief Markdown and update the manifest's recorded sha.

    The POST route enforces ``markdown_drift`` precondition, so any
    test that wants a real regeneration must keep the recorded sha in
    sync with the on-disk bytes.
    """
    write_prospect_brief_markdown(job_id, new_md)
    manifest_path = job_folder(job_id) / "document_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["markdown_sha256"] = _sha256_text(new_md)
    manifest["markdown_byte_length"] = len(new_md.encode("utf-8"))
    write_document_manifest(job_id, manifest)


# ---------------------------------------------------------------------------
# POST response shape
# ---------------------------------------------------------------------------

def test_post_pdf_returns_step40_lineage_fields(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{approved_job_ready_for_export}/export/pdf",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # All five Step 40 fields are present and well-shaped on v1.
    assert body["export_id"] == body["sha256"]
    assert body["version"] == 1
    assert body["supersedes"] is None
    assert isinstance(body["manifest_sha256_at_export"], str)
    assert len(body["manifest_sha256_at_export"]) == 64
    assert body["regenerated"] is True


def test_post_docx_returns_step40_lineage_fields(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    r = authed_client.post(
        f"/api/jobs/{approved_job_ready_for_export}/export/docx",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["export_id"] == body["sha256"]
    assert body["version"] == 1
    assert body["supersedes"] is None
    assert len(body["manifest_sha256_at_export"]) == 64
    assert body["regenerated"] is True


# ---------------------------------------------------------------------------
# X-Export-Version header
# ---------------------------------------------------------------------------

def test_post_emits_x_export_version_header(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    assert r1.headers["X-Export-Version"] == "1"
    _refresh_markdown(job_id, _MARKDOWN_V2)
    r2 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    assert r2.headers["X-Export-Version"] == "2"


def test_get_emits_x_export_version_header_on_200(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    r = authed_client.get(f"/api/jobs/{job_id}/exports/pdf")
    assert r.status_code == 200
    assert r.headers["X-Export-Version"] == "1"


def test_get_emits_x_export_version_header_on_304(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    r1 = authed_client.get(f"/api/jobs/{job_id}/exports/pdf")
    etag = r1.headers["ETag"]
    r2 = authed_client.get(
        f"/api/jobs/{job_id}/exports/pdf",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.headers["X-Export-Version"] == "1"


# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------

def test_post_writes_per_version_archive_copy(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    archive_v1 = (
        job_folder(job_id) / "exports" / "archive" / "prospect_brief.v1.pdf"
    )
    assert archive_v1.exists()
    assert archive_v1.read_bytes().startswith(b"%PDF-")
    # Bytes match the response sha256.
    assert (
        hashlib.sha256(archive_v1.read_bytes()).hexdigest()
        == r1.json()["sha256"]
    )


def test_archive_v1_is_preserved_after_regeneration(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """Immutability — a later render must NOT overwrite the v1 archive."""
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    archive_v1 = (
        job_folder(job_id) / "exports" / "archive" / "prospect_brief.v1.pdf"
    )
    v1_bytes = archive_v1.read_bytes()
    v1_sha = r1.json()["sha256"]

    _refresh_markdown(job_id, _MARKDOWN_V2)
    r2 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    assert r2.json()["version"] == 2

    # v1 archive is byte-identical to what we captured before r2.
    assert archive_v1.read_bytes() == v1_bytes
    # v2 archive landed alongside it.
    archive_v2 = (
        job_folder(job_id) / "exports" / "archive" / "prospect_brief.v2.pdf"
    )
    assert archive_v2.exists()
    assert archive_v2.read_bytes() != v1_bytes
    # And the public helper can fetch the historical bytes.
    assert read_export_archive_bytes(job_id, "pdf", 1) == v1_bytes
    assert (
        hashlib.sha256(read_export_archive_bytes(job_id, "pdf", 1)).hexdigest()
        == v1_sha
    )


# ---------------------------------------------------------------------------
# Manifest immutability + materialisation
# ---------------------------------------------------------------------------

def test_manifest_exports_is_append_only_on_regeneration(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    job_id = approved_job_ready_for_export
    r1 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    manifest_path = job_folder(job_id) / "document_manifest.json"
    manifest_after_r1 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest_after_r1["exports"]) == 1
    v1_entry_snapshot = dict(manifest_after_r1["exports"][0])
    assert manifest_after_r1["latest_export_id"]["pdf"] == r1.json()["sha256"]
    # Materialised to v3 on the first upsert.
    assert manifest_after_r1["schema_version"] == 3

    _refresh_markdown(job_id, _MARKDOWN_V2)
    r2 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")

    manifest_after_r2 = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest_after_r2["exports"]) == 2
    # The v1 entry is byte-identical to its pre-r2 snapshot — strict
    # immutability under Step 40.
    assert manifest_after_r2["exports"][0] == v1_entry_snapshot
    # The v2 entry's supersedes pointer is the v1 export_id.
    assert manifest_after_r2["exports"][1]["version"] == 2
    assert (
        manifest_after_r2["exports"][1]["supersedes"]
        == v1_entry_snapshot["export_id"]
    )
    # The lineage pointer advanced.
    assert manifest_after_r2["latest_export_id"]["pdf"] == r2.json()["sha256"]


def test_idempotent_repost_does_not_append_entry(
    authed_client: TestClient,
    approved_job_ready_for_export: str,
) -> None:
    """A second POST with no markdown change must not append a new entry."""
    job_id = approved_job_ready_for_export
    authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    manifest_path = job_folder(job_id) / "document_manifest.json"
    before = manifest_path.read_text(encoding="utf-8")

    r2 = authed_client.post(f"/api/jobs/{job_id}/export/pdf")
    assert r2.status_code == 200
    assert r2.json()["regenerated"] is False
    assert r2.json()["version"] == 1

    # Manifest unchanged byte-for-byte.
    after = manifest_path.read_text(encoding="utf-8")
    assert before == after
