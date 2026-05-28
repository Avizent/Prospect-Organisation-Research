"""Step 37 — ``GET /api/jobs/{id}/manifest`` carries a ``lifecycle`` sibling.

The Step 34 route returned ``{"manifest": {...}}``. Step 37 enriches
the envelope with a ``lifecycle`` field carrying a computed projection
of staleness signals (source-markdown drift, per-entry staleness).
The projection is read-only and not persisted.
"""

from __future__ import annotations

import hashlib
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


_MD_TEXT = "# Acme Ltd\n\nManifest lifecycle fixture.\n"
_MD_BYTES = _MD_TEXT.encode("utf-8")
_MD_SHA = hashlib.sha256(_MD_BYTES).hexdigest()

_PDF_BYTES = b"%PDF-1.7 stub\n"
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


def _seed(
    job_id: str,
    *,
    entry_overrides: dict[str, Any] | None = None,
    omit_entry: bool = False,
    write_pdf: bool = True,
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
    return f"/api/jobs/{job_id}/manifest"


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


def test_envelope_carries_lifecycle_sibling(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"manifest", "lifecycle"}


def test_lifecycle_carries_manifest_and_entries(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    lifecycle = r.json()["lifecycle"]
    assert "manifest" in lifecycle
    assert "entries" in lifecycle
    assert isinstance(lifecycle["entries"], list)


def test_lifecycle_manifest_carries_documented_keys(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    manifest_lc = r.json()["lifecycle"]["manifest"]
    assert "source_markdown_drift" in manifest_lc
    assert "on_disk_markdown_sha256" in manifest_lc
    assert "any_stale_export" in manifest_lc


# ---------------------------------------------------------------------------
# Clean state
# ---------------------------------------------------------------------------


def test_clean_state_reports_no_drift_and_no_stale_exports(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    manifest_lc = r.json()["lifecycle"]["manifest"]
    assert manifest_lc["source_markdown_drift"] is False
    assert manifest_lc["any_stale_export"] is False
    assert manifest_lc["on_disk_markdown_sha256"] == _MD_SHA


def test_clean_state_entries_list_has_one_record_per_export(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    r = authed_client.get(_path(approved_job))
    entries = r.json()["lifecycle"]["entries"]
    assert len(entries) == 1
    assert entries[0]["format"] == "pdf"
    assert entries[0]["lifecycle"]["stale"] is False
    assert entries[0]["lifecycle"]["reasons"] == []


# ---------------------------------------------------------------------------
# Source Markdown drift surfaces at manifest level
# ---------------------------------------------------------------------------


def test_source_markdown_drift_flagged_after_edit(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """Amendment 1 — drift surfaces as the prominent manifest-level signal."""
    _seed(approved_job)
    write_prospect_brief_markdown(approved_job, _MD_TEXT + "edit\n")

    r = authed_client.get(_path(approved_job))
    manifest_lc = r.json()["lifecycle"]["manifest"]
    assert manifest_lc["source_markdown_drift"] is True


def test_entry_stale_propagates_to_any_stale_export(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """When the entry has a stale source SHA, ``any_stale_export`` is True."""
    _seed(approved_job, entry_overrides={"source_markdown_sha256": "f" * 64})
    r = authed_client.get(_path(approved_job))
    manifest_lc = r.json()["lifecycle"]["manifest"]
    assert manifest_lc["any_stale_export"] is True


def test_entry_lifecycle_includes_source_entry_drift_reason(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job, entry_overrides={"source_markdown_sha256": "f" * 64})
    r = authed_client.get(_path(approved_job))
    entries = r.json()["lifecycle"]["entries"]
    assert entries
    assert entries[0]["lifecycle"]["stale"] is True
    assert "source_entry_drift" in entries[0]["lifecycle"]["reasons"]


# ---------------------------------------------------------------------------
# Manifest with no exports[] — still a valid response
# ---------------------------------------------------------------------------


def test_empty_exports_yields_empty_entries_list(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job, omit_entry=True, write_pdf=False)
    r = authed_client.get(_path(approved_job))
    lifecycle = r.json()["lifecycle"]
    assert lifecycle["entries"] == []
    assert lifecycle["manifest"]["any_stale_export"] is False


# ---------------------------------------------------------------------------
# Behavioural read-only contract
# ---------------------------------------------------------------------------


def test_manifest_route_does_not_touch_manifest_mtime(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    """The enriched route is still read-only — three GETs leave the
    manifest file byte-identical and mtime-stable.
    """
    _seed(approved_job)
    manifest_path = job_folder(approved_job) / "document_manifest.json"
    mtime_before = os.stat(manifest_path).st_mtime_ns
    bytes_before = manifest_path.read_bytes()

    time.sleep(0.01)

    for _ in range(3):
        assert authed_client.get(_path(approved_job)).status_code == 200

    assert os.stat(manifest_path).st_mtime_ns == mtime_before
    assert manifest_path.read_bytes() == bytes_before


def test_manifest_route_does_not_touch_prospect_brief_mtime(
    authed_client: TestClient,
    approved_job: str,
) -> None:
    _seed(approved_job)
    md_path = job_folder(approved_job) / "prospect_brief.md"
    mtime_before = os.stat(md_path).st_mtime_ns
    bytes_before = md_path.read_bytes()

    time.sleep(0.01)

    for _ in range(3):
        authed_client.get(_path(approved_job))

    assert os.stat(md_path).st_mtime_ns == mtime_before
    assert md_path.read_bytes() == bytes_before
