"""Step 41 — GET /api/jobs/{job_id}/exports/{format}/{export_id}.

Historical export retrieval: immutable, read-only, content-addressed.

Coverage:

  * happy path (v1 and superseded historical versions)
  * ETag = export_id
  * X-Export-Version, X-Export-Id, X-Export-Latest, X-Export-Superseded
  * Cache-Control: private, max-age=31536000, immutable
  * Content-Disposition carries versioned filename
  * Conditional GET (304 with same headers)
  * Stale-header semantics against the historical entry
  * Immutability: GET does not touch manifest, state.json, or alias mtime
  * All error paths (404 and 500 shapes)
  * Authentication guard
  * Latest-via-historical returns same bytes as latest GET
  * Three-generation lineage
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
    write_export,
    write_export_archive,
    write_prospect_brief_markdown,
)


# ---------------------------------------------------------------------------
# Shared markdown + manifest helpers
# ---------------------------------------------------------------------------

_MARKDOWN_V1 = (
    "# Acme Ltd\n\nFirst version of the brief.\n\n"
    "## Snapshot\n\nHeadcount 1000-5000.\n"
)
_MARKDOWN_V2 = _MARKDOWN_V1 + "\nAdded paragraph for v2.\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed_manifest(job_id: str, markdown: str) -> None:
    write_prospect_brief_markdown(job_id, markdown)
    manifest = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-29T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": _sha(markdown),
        "markdown_byte_length": len(markdown.encode("utf-8")),
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


def _update_markdown(job_id: str, new_md: str) -> None:
    """Replace brief + refresh manifest's recorded sha."""
    write_prospect_brief_markdown(job_id, new_md)
    mp = job_folder(job_id) / "document_manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["markdown_sha256"] = _sha(new_md)
    m["markdown_byte_length"] = len(new_md.encode("utf-8"))
    write_document_manifest(job_id, m)


@pytest.fixture()
def ready_job(approved_job: str) -> Iterator[str]:
    """Approved job with a v2 manifest + markdown on disk."""
    _seed_manifest(approved_job, _MARKDOWN_V1)
    yield approved_job


def _post(client: TestClient, job_id: str, fmt: str) -> dict:
    r = client.post(f"/api/jobs/{job_id}/export/{fmt}")
    assert r.status_code in (200, 201), r.text
    return r.json()


def _historical_url(job_id: str, fmt: str, export_id: str) -> str:
    return f"/api/jobs/{job_id}/exports/{fmt}/{export_id}"


# ---------------------------------------------------------------------------
# POST response shape reused here to seed lineage
# ---------------------------------------------------------------------------


def test_get_historical_returns_201_bytes_for_latest(
    authed_client: TestClient,
    ready_job: str,
) -> None:
    """After a single POST the historical endpoint for that export_id
    returns the same bytes as the latest GET."""
    r1 = _post(authed_client, ready_job, "pdf")
    export_id = r1["export_id"]

    r_hist = authed_client.get(_historical_url(ready_job, "pdf", export_id))
    assert r_hist.status_code == 200
    assert r_hist.headers["Content-Type"] == "application/pdf"
    assert r_hist.content.startswith(b"%PDF-")

    r_latest = authed_client.get(f"/api/jobs/{ready_job}/exports/pdf")
    assert r_hist.content == r_latest.content


def test_get_historical_returns_correct_bytes_for_superseded_version(
    authed_client: TestClient,
    ready_job: str,
) -> None:
    """v1 bytes survive unchanged after a v2 render replaces the alias."""
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    v1_bytes = authed_client.get(
        _historical_url(ready_job, "pdf", v1_id)
    ).content

    _update_markdown(ready_job, _MARKDOWN_V2)
    r2 = _post(authed_client, ready_job, "pdf")
    v2_id = r2["export_id"]
    assert v2_id != v1_id  # render produced different bytes

    r_v1 = authed_client.get(_historical_url(ready_job, "pdf", v1_id))
    assert r_v1.status_code == 200
    assert r_v1.content == v1_bytes

    r_v2 = authed_client.get(_historical_url(ready_job, "pdf", v2_id))
    assert r_v2.status_code == 200
    assert r_v2.content != v1_bytes


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------


def test_etag_equals_export_id(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["ETag"] == f'"{eid}"'


def test_x_export_id_header_set(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["X-Export-Id"] == eid


def test_x_export_version_header_set(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["X-Export-Version"] == "1"


def test_x_export_latest_true_for_latest(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["X-Export-Latest"] == "true"


def test_x_export_latest_false_for_superseded(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    _update_markdown(ready_job, _MARKDOWN_V2)
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", v1_id))
    assert r.headers["X-Export-Latest"] == "false"


def test_x_export_superseded_true_for_v1_after_v2_exists(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    _update_markdown(ready_job, _MARKDOWN_V2)
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", v1_id))
    assert r.headers["X-Export-Superseded"] == "true"


def test_x_export_superseded_false_for_latest(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["X-Export-Superseded"] == "false"


def test_cache_control_header_immutable(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    cc = r.headers["Cache-Control"]
    assert "private" in cc
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_content_disposition_carries_versioned_filename(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    cd = r.headers["Content-Disposition"]
    assert "prospect_brief.v1.pdf" in cd


def test_stale_reasons_contains_superseded_token_for_old_version(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    _update_markdown(ready_job, _MARKDOWN_V2)
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", v1_id))
    assert r.headers["X-Export-Stale"] == "true"
    assert "superseded" in r.headers["X-Export-Stale-Reasons"]


def test_stale_not_set_for_current_version(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.headers["X-Export-Stale"] == "false"
    assert r.headers["X-Export-Stale-Reasons"] == ""


def test_source_markdown_sha_matches_entry_not_current_manifest(
    authed_client: TestClient, ready_job: str,
) -> None:
    """The source markdown sha in the header reflects the entry's
    own source_markdown_sha256, not the current manifest's sha256."""
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    v1_source_sha = r1["source_markdown_sha256"]

    # Mutate the markdown so the manifest's sha differs.
    _update_markdown(ready_job, _MARKDOWN_V2)

    r = authed_client.get(_historical_url(ready_job, "pdf", v1_id))
    # Historical header should still point at the v1 source sha.
    assert r.headers["X-Export-Source-Markdown-SHA256"] == v1_source_sha


# ---------------------------------------------------------------------------
# Conditional GET
# ---------------------------------------------------------------------------


def test_if_none_match_returns_304_with_same_headers(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r200 = authed_client.get(_historical_url(ready_job, "pdf", eid))
    etag = r200.headers["ETag"]

    r304 = authed_client.get(
        _historical_url(ready_job, "pdf", eid),
        headers={"If-None-Match": etag},
    )
    assert r304.status_code == 304
    assert r304.headers["ETag"] == etag
    assert r304.headers["X-Export-Version"] == r200.headers["X-Export-Version"]
    assert r304.headers["X-Export-Id"] == r200.headers["X-Export-Id"]
    assert r304.headers["X-Export-Latest"] == r200.headers["X-Export-Latest"]
    assert r304.headers["Cache-Control"] == r200.headers["Cache-Control"]


def test_if_none_match_with_different_etag_returns_200(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    r = authed_client.get(
        _historical_url(ready_job, "pdf", eid),
        headers={"If-None-Match": '"' + "0" * 64 + '"'},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Immutability guarantees — mtime checks
# ---------------------------------------------------------------------------


def test_historical_get_does_not_touch_manifest_mtime(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    mp = job_folder(ready_job) / "document_manifest.json"
    mtime_before = mp.stat().st_mtime
    # Brief sleep so mtime would visibly change if written.
    time.sleep(0.05)
    r1_body = authed_client.get(
        f"/api/jobs/{ready_job}/exports/pdf"
    )
    eid = hashlib.sha256(r1_body.content).hexdigest()
    authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert mp.stat().st_mtime == mtime_before, (
        "historical GET must not touch document_manifest.json"
    )


def test_historical_get_does_not_write_archive_file(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    archive_dir = job_folder(ready_job) / "exports" / "archive"
    files_before = set(archive_dir.iterdir()) if archive_dir.exists() else set()
    time.sleep(0.05)
    authed_client.get(_historical_url(ready_job, "pdf", eid))
    files_after = set(archive_dir.iterdir()) if archive_dir.exists() else set()
    assert files_after == files_before, (
        "historical GET must not write any archive file"
    )


def test_historical_get_does_not_mutate_state_json_mtime(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    sp = job_folder(ready_job) / "state.json"
    mtime_before = sp.stat().st_mtime
    time.sleep(0.05)
    r1_body = authed_client.get(f"/api/jobs/{ready_job}/exports/pdf")
    eid = hashlib.sha256(r1_body.content).hexdigest()
    authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert sp.stat().st_mtime == mtime_before, (
        "historical GET must not touch state.json"
    )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_returns_404_for_unknown_job(authed_client: TestClient) -> None:
    r = authed_client.get(
        f"/api/jobs/00000000-0000-4000-8000-deadbeefdead/exports/pdf/{'f' * 64}"
    )
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_returns_404_for_non_uuid_job(authed_client: TestClient) -> None:
    r = authed_client.get(
        f"/api/jobs/not-a-uuid/exports/pdf/{'f' * 64}"
    )
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_returns_404_for_unknown_format(
    authed_client: TestClient, ready_job: str,
) -> None:
    r = authed_client.get(
        _historical_url(ready_job, "xlsx", "f" * 64)
    )
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "unknown_format"


def test_returns_404_for_malformed_export_id_too_short(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", "f" * 10))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "invalid_export_id"


def test_returns_404_for_malformed_export_id_uppercase(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", "F" * 64))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "invalid_export_id"


def test_returns_404_for_malformed_export_id_non_hex(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", "g" * 64))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "invalid_export_id"


def test_returns_404_for_unknown_export_id(
    authed_client: TestClient, ready_job: str,
) -> None:
    _post(authed_client, ready_job, "pdf")
    r = authed_client.get(_historical_url(ready_job, "pdf", "e" * 64))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "export_not_found"


def test_returns_404_for_orphan_archive_file(
    authed_client: TestClient, ready_job: str,
) -> None:
    """An archive file placed by hand with no matching manifest entry
    must return 404 — the manifest is the only catalogue."""
    # Place an orphan archive file.
    archive_dir = job_folder(ready_job) / "exports" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    orphan_id = "a" * 64
    (archive_dir / f"prospect_brief.v1.pdf").write_bytes(b"%PDF-1.7\norphan")
    r = authed_client.get(_historical_url(ready_job, "pdf", orphan_id))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "export_not_found"


def test_returns_500_for_missing_archive_when_entry_present(
    authed_client: TestClient, ready_job: str,
) -> None:
    """Manifest entry present but archive file gone → 500 not 404."""
    r1 = _post(authed_client, ready_job, "pdf")
    eid = r1["export_id"]
    # Delete the archive file.
    archive_dir = job_folder(ready_job) / "exports" / "archive"
    for f in archive_dir.iterdir():
        if f.suffix == ".pdf":
            f.unlink()
    r = authed_client.get(_historical_url(ready_job, "pdf", eid))
    assert r.status_code == 500
    assert r.json()["detail"]["reason"] == "export_archive_missing"


def test_returns_401_when_anonymous(
    anon_client: TestClient, ready_job: str,
) -> None:
    """Unauthenticated access must be rejected."""
    r = anon_client.get(_historical_url(ready_job, "pdf", "f" * 64))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# DOCX format
# ---------------------------------------------------------------------------


def test_historical_docx_returns_bytes(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "docx")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "docx", eid))
    assert r.status_code == 200
    assert "application/vnd.openxmlformats" in r.headers["Content-Type"]
    assert r.content[:4] == b"PK\x03\x04"  # ZIP/DOCX magic bytes


def test_historical_docx_x_export_latest_true(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "docx")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "docx", eid))
    assert r.headers["X-Export-Latest"] == "true"


def test_content_disposition_versioned_docx(
    authed_client: TestClient, ready_job: str,
) -> None:
    r1 = _post(authed_client, ready_job, "docx")
    eid = r1["export_id"]
    r = authed_client.get(_historical_url(ready_job, "docx", eid))
    assert "prospect_brief.v1.docx" in r.headers["Content-Disposition"]


# ---------------------------------------------------------------------------
# Three-generation lineage
# ---------------------------------------------------------------------------


def test_three_generation_lineage_each_version_retrievable(
    authed_client: TestClient, ready_job: str,
) -> None:
    """POST × 3 with different markdown each time. Historical endpoint
    must serve the correct bytes for every version."""
    r1 = _post(authed_client, ready_job, "pdf")
    v1_id = r1["export_id"]
    v1_bytes = authed_client.get(
        f"/api/jobs/{ready_job}/exports/pdf"
    ).content

    _update_markdown(ready_job, _MARKDOWN_V2)
    r2 = _post(authed_client, ready_job, "pdf")
    v2_id = r2["export_id"]
    v2_bytes = authed_client.get(
        f"/api/jobs/{ready_job}/exports/pdf"
    ).content

    md_v3 = _MARKDOWN_V2 + "\nThird paragraph.\n"
    _update_markdown(ready_job, md_v3)
    r3 = _post(authed_client, ready_job, "pdf")
    v3_id = r3["export_id"]
    v3_bytes = authed_client.get(
        f"/api/jobs/{ready_job}/exports/pdf"
    ).content

    # All three export_ids are distinct.
    assert len({v1_id, v2_id, v3_id}) == 3

    # Historical endpoint returns correct bytes for every version.
    assert (
        authed_client.get(_historical_url(ready_job, "pdf", v1_id)).content
        == v1_bytes
    )
    assert (
        authed_client.get(_historical_url(ready_job, "pdf", v2_id)).content
        == v2_bytes
    )
    assert (
        authed_client.get(_historical_url(ready_job, "pdf", v3_id)).content
        == v3_bytes
    )

    # Latest flag only on v3.
    assert authed_client.get(
        _historical_url(ready_job, "pdf", v3_id)
    ).headers["X-Export-Latest"] == "true"
    assert authed_client.get(
        _historical_url(ready_job, "pdf", v1_id)
    ).headers["X-Export-Latest"] == "false"

    # Superseded flag on v1 and v2 but not v3.
    assert authed_client.get(
        _historical_url(ready_job, "pdf", v1_id)
    ).headers["X-Export-Superseded"] == "true"
    assert authed_client.get(
        _historical_url(ready_job, "pdf", v3_id)
    ).headers["X-Export-Superseded"] == "false"
