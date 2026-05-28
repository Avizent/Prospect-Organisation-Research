"""Tests for ``GET /api/jobs/{job_id}/exports/validate`` (Step 37).

The endpoint exposes
:func:`backend.exporters.lifecycle.validate_export_integrity` over the
wire as a JSON envelope ``{"job_id": ..., "issues": [...]}``. It is
strictly read-only: no manifest writes, no state transitions, no PDF
rewrites.

Failure mapping mirrors the rest of the jobs surface:

* unknown job / non-UUID job_id        → 404 ``job_not_found``
* manifest absent                       → 404 ``manifest_not_found``
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.jobs.storage import (
    job_folder,
    write_document_manifest,
    write_export,
    write_prospect_brief_markdown,
)


_MD_TEXT = "# Acme Ltd\n\nBrief body for governance tests.\n"
_MD_BYTES = _MD_TEXT.encode("utf-8")
_MD_SHA = hashlib.sha256(_MD_BYTES).hexdigest()

_PDF_BYTES = b"%PDF-1.7 fake fixture bytes\n"
_PDF_SHA = hashlib.sha256(_PDF_BYTES).hexdigest()


def _entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": len(_PDF_BYTES),
        "sha256": _PDF_SHA,
        "source_markdown_sha256": _MD_SHA,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": EXPORTER_VERSION,
        "template_version": TEMPLATE_VERSION,
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "regeneration_count": 0,
        "warnings": [],
    }
    base.update(overrides)
    return base


def _seed_clean(
    job_id: str,
    *,
    entry_overrides: dict[str, Any] | None = None,
    write_pdf: bool = True,
    omit_entry: bool = False,
) -> None:
    write_prospect_brief_markdown(job_id, _MD_TEXT)
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
        "exports": [] if omit_entry else [_entry(**(entry_overrides or {}))],
    }
    write_document_manifest(job_id, manifest)
    if write_pdf:
        write_export(job_id, "pdf", _PDF_BYTES)


def _path(job_id: str) -> str:
    return f"/api/jobs/{job_id}/exports/validate"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_state_returns_200_with_empty_issues(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed_clean(approved_job)
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"job_id": approved_job, "issues": []}


def test_envelope_shape_matches_documented_keys(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Envelope must be exactly ``{"job_id", "issues"}`` — ``extra="forbid"``."""
    _seed_clean(approved_job)
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    assert set(r.json().keys()) == {"job_id", "issues"}


def test_issue_record_shape_matches_documented_fields(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Each issue carries code, severity, message, format, filename."""
    _seed_clean(approved_job, entry_overrides={"sha256": "deadbeef" * 8})
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200
    issues = r.json()["issues"]
    assert issues
    issue = issues[0]
    assert set(issue.keys()) == {
        "code", "severity", "message", "format", "filename",
    }


# ---------------------------------------------------------------------------
# Issue surfacing
# ---------------------------------------------------------------------------


def test_route_surfaces_manifest_sha_mismatch(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed_clean(approved_job, entry_overrides={"sha256": "deadbeef" * 8})
    r = authed_client.get(_path(approved_job))
    body = r.json()
    codes = [i["code"] for i in body["issues"]]
    assert "manifest_sha_mismatch" in codes


def test_route_surfaces_source_markdown_drift_as_error(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Amendment 1 — ``source_markdown_drift`` is error severity."""
    _seed_clean(approved_job)
    # Tamper with the on-disk Markdown.
    write_prospect_brief_markdown(approved_job, _MD_TEXT + "edit\n")
    r = authed_client.get(_path(approved_job))
    body = r.json()
    matching = [i for i in body["issues"] if i["code"] == "source_markdown_drift"]
    assert matching
    assert matching[0]["severity"] == "error"


def test_route_surfaces_unsupported_export_version_as_warning(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Amendment 2 — ``unsupported_export_version`` is warning severity."""
    _seed_clean(
        approved_job,
        entry_overrides={"exporter_version": "999.999.999"},
    )
    r = authed_client.get(_path(approved_job))
    body = r.json()
    matching = [
        i for i in body["issues"]
        if i["code"] == "unsupported_export_version"
    ]
    assert matching
    assert matching[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_route_returns_404_for_unknown_job(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(_path("00000000-0000-4000-8000-deadbeefdead"))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_route_returns_404_for_non_uuid_job_id(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(_path("not-a-uuid"))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "job_not_found"


def test_route_returns_404_when_manifest_absent(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Approved job exists but no manifest yet → ``manifest_not_found``."""
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "manifest_not_found"


def test_route_requires_authentication(
    anon_client: TestClient,
    approved_job: str,
) -> None:
    r = anon_client.get(_path(approved_job))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Behavioural read-only contract
# ---------------------------------------------------------------------------


def test_route_does_not_touch_manifest_mtime(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """``GET /exports/validate`` must not rewrite ``document_manifest.json``."""
    _seed_clean(approved_job)
    manifest_path = job_folder(approved_job) / "document_manifest.json"
    mtime_before = os.stat(manifest_path).st_mtime_ns
    bytes_before = manifest_path.read_bytes()

    time.sleep(0.01)

    for _ in range(3):
        r = authed_client.get(_path(approved_job))
        assert r.status_code == 200

    mtime_after = os.stat(manifest_path).st_mtime_ns
    bytes_after = manifest_path.read_bytes()
    assert mtime_after == mtime_before, (
        "GET /exports/validate must not rewrite document_manifest.json "
        f"(mtime moved from {mtime_before} → {mtime_after})"
    )
    assert bytes_after == bytes_before


def test_route_does_not_touch_pdf_mtime(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed_clean(approved_job)
    pdf_path = job_folder(approved_job) / "exports" / "prospect_brief.pdf"
    mtime_before = os.stat(pdf_path).st_mtime_ns
    bytes_before = pdf_path.read_bytes()

    time.sleep(0.01)

    for _ in range(3):
        authed_client.get(_path(approved_job))

    assert os.stat(pdf_path).st_mtime_ns == mtime_before
    assert pdf_path.read_bytes() == bytes_before


def test_route_does_not_mutate_state_json(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed_clean(approved_job)
    state_path = job_folder(approved_job) / "state.json"
    bytes_before = state_path.read_bytes()
    for _ in range(3):
        authed_client.get(_path(approved_job))
    assert state_path.read_bytes() == bytes_before
