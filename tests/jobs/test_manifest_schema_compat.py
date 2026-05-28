"""Step 35 — storage helpers remain schema-agnostic across the v1→v2 bump.

The Step 34 storage helpers (``write_document_manifest`` /
``read_document_manifest``) are deliberately schema-agnostic: they
serialise/deserialise a ``dict`` without inspecting any field. That
contract is what lets us bump ``schema_version`` 1 → 2 additively
without touching storage code, but it is worth pinning under a
dedicated test so a future change does not silently couple storage
to a particular schema version.

(The companion route-passthrough test lives in
``tests/jobs_routes/test_manifest_schema_compat_route.py`` because
the route fixtures live there.)
"""

from __future__ import annotations

import uuid
from pathlib import Path

from backend.jobs.storage import (
    read_document_manifest,
    write_document_manifest,
)


# A hand-rolled v1 manifest — exactly the shape the Step 29 assembler
# wrote prior to Step 35. No ``exports`` field at all.
_V1_MANIFEST: dict = {
    "schema_version": 1,
    "job_id": "00000000-0000-4000-8000-000000000000",
    "company_name": "Acme Ltd",
    "company_url": "https://acme.example.com/",
    "generated_at": "2026-05-28T00:00:00Z",
    "markdown_filename": "prospect_brief.md",
    "markdown_sha256": "a" * 64,
    "markdown_byte_length": 1234,
    "sections": [],
    "artefacts": {},
    "outputs": [
        {"key": "markdown", "filename": "prospect_brief.md"},
        {"key": "manifest", "filename": "document_manifest.json"},
    ],
    "critic_verdict": None,
    "warnings": [],
}


def _new_job_id() -> str:
    return str(uuid.uuid4())


def test_storage_round_trips_v1_manifest_unchanged(
    isolated_jobs_root: Path,
) -> None:
    """A v1 manifest written then re-read must equal the original.

    The storage layer is schema-agnostic — it never defaults missing
    fields, never asserts the shape, never adds ``exports: []`` on
    behalf of the caller."""
    job_id = _new_job_id()
    payload = {**_V1_MANIFEST, "job_id": job_id}
    write_document_manifest(job_id, payload)
    rebuilt = read_document_manifest(job_id)
    assert rebuilt == payload
    # And specifically: the schema-version reader-compat promise.
    assert "exports" not in rebuilt
    assert rebuilt["schema_version"] == 1


def test_storage_round_trips_v2_manifest_with_empty_exports(
    isolated_jobs_root: Path,
) -> None:
    """A v2 manifest (with ``exports: []``) must round-trip too."""
    job_id = _new_job_id()
    payload = {
        **_V1_MANIFEST,
        "job_id": job_id,
        "schema_version": 2,
        "exports": [],
    }
    write_document_manifest(job_id, payload)
    rebuilt = read_document_manifest(job_id)
    assert rebuilt == payload
    assert rebuilt["exports"] == []
    assert rebuilt["schema_version"] == 2
