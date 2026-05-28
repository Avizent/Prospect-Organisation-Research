"""Tests for the Step 29 assembly storage helpers.

Step 29 adds two new write-only helpers in :mod:`backend.jobs.storage`:

* :func:`write_prospect_brief_markdown` — atomically write
  ``prospect_brief.md`` to the job folder.
* :func:`write_document_manifest` — atomically write
  ``document_manifest.json`` to the job folder.

Neither helper has a read counterpart: both outputs are terminal
artefacts produced by :mod:`backend.assembly.markdown` and consumed
externally (the user reads the Markdown; the manifest is a provenance
record for future tooling).

The tests here mirror ``tests/jobs/test_storage_writers.py``:

* Round-trip the file (write → read raw bytes / JSON) to prove the
  expected on-disk shape.
* Pin the filename so the helper cannot collide with any Stage 1/2
  artefact.
* Prove the atomic-write postcondition (no leftover ``.tmp``).
* Prove the folder is created defensively if absent.
* Pin the JSON-formatting policy (UTF-8, ``ensure_ascii=False``) so
  non-ASCII company names are not silently mojibaked at write time.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from backend.jobs.storage import (
    JobNotFound,
    read_prospect_brief_markdown,
    write_document_manifest,
    write_prospect_brief_markdown,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# write_prospect_brief_markdown
# ---------------------------------------------------------------------------

def test_write_prospect_brief_markdown_persists_text_at_expected_path(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    text = "# Heading\n\nBody.\n"
    path = write_prospect_brief_markdown(job_id, text)
    assert path == isolated_jobs_root / job_id / "prospect_brief.md"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == text


def test_write_prospect_brief_markdown_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    """Mirrors the other writers' defence-in-depth idiom."""
    job_id = _new_job_id()
    path = write_prospect_brief_markdown(job_id, "hello\n")
    assert path.exists()
    assert path.parent == isolated_jobs_root / job_id


def test_write_prospect_brief_markdown_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_prospect_brief_markdown(job_id, "hello\n")
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_prospect_brief_markdown_is_idempotent_replacement(
    isolated_jobs_root: Path,
) -> None:
    """A second write replaces the file atomically; no .tmp residue."""
    job_id = _new_job_id()
    write_prospect_brief_markdown(job_id, "first\n")
    write_prospect_brief_markdown(job_id, "second\n")
    path = isolated_jobs_root / job_id / "prospect_brief.md"
    assert path.read_text(encoding="utf-8") == "second\n"
    assert list(path.parent.glob("*.tmp")) == []


def test_write_prospect_brief_markdown_preserves_unicode(
    isolated_jobs_root: Path,
) -> None:
    """A non-ASCII glyph must round-trip without escaping."""
    job_id = _new_job_id()
    text = "# Société Générale — résumé\n"
    write_prospect_brief_markdown(job_id, text)
    on_disk = (isolated_jobs_root / job_id / "prospect_brief.md").read_text(
        encoding="utf-8"
    )
    assert on_disk == text
    # The raw bytes must contain the UTF-8 encoding of the glyph, not a
    # backslash-escape: the markdown is a *display* artefact.
    raw_bytes = (
        isolated_jobs_root / job_id / "prospect_brief.md"
    ).read_bytes()
    assert "é".encode("utf-8") in raw_bytes


# ---------------------------------------------------------------------------
# write_document_manifest
# ---------------------------------------------------------------------------

def test_write_document_manifest_round_trips_to_json(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "warnings": [],
        "sections": [
            {"key": "header", "rendered": True, "reason": None},
        ],
    }
    path = write_document_manifest(job_id, manifest)
    assert path == isolated_jobs_root / job_id / "document_manifest.json"
    assert path.exists()
    rebuilt = json.loads(path.read_text(encoding="utf-8"))
    assert rebuilt == manifest


def test_write_document_manifest_creates_folder_if_absent(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_document_manifest(job_id, {"schema_version": 1})
    assert (
        isolated_jobs_root / job_id / "document_manifest.json"
    ).exists()


def test_write_document_manifest_leaves_no_tmp_file_behind(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_document_manifest(job_id, {"schema_version": 1})
    folder = isolated_jobs_root / job_id
    assert list(folder.glob("*.tmp")) == []


def test_write_document_manifest_uses_unicode_safe_encoding(
    isolated_jobs_root: Path,
) -> None:
    """``ensure_ascii=False`` policy: non-ASCII not backslash-escaped."""
    job_id = _new_job_id()
    write_document_manifest(
        job_id, {"company_name": "Société Générale"}
    )
    raw = (
        isolated_jobs_root / job_id / "document_manifest.json"
    ).read_text(encoding="utf-8")
    assert "Société" in raw
    assert "\\u" not in raw


# ---------------------------------------------------------------------------
# read_prospect_brief_markdown (Step 31)
# ---------------------------------------------------------------------------

def test_read_prospect_brief_markdown_round_trips(
    isolated_jobs_root: Path,
) -> None:
    """Round-trip: write → read returns the same UTF-8 text verbatim."""
    job_id = _new_job_id()
    text = "# Heading\n\nBody paragraph.\n"
    write_prospect_brief_markdown(job_id, text)
    assert read_prospect_brief_markdown(job_id) == text


def test_read_prospect_brief_markdown_preserves_unicode(
    isolated_jobs_root: Path,
) -> None:
    """Non-ASCII glyphs survive the round-trip without escaping."""
    job_id = _new_job_id()
    text = "# Société Générale — résumé\n"
    write_prospect_brief_markdown(job_id, text)
    assert read_prospect_brief_markdown(job_id) == text


def test_read_prospect_brief_markdown_missing_file_raises_job_not_found(
    isolated_jobs_root: Path,
) -> None:
    """Mirrors the other read helpers' contract: missing → JobNotFound."""
    job_id = _new_job_id()
    with pytest.raises(JobNotFound):
        read_prospect_brief_markdown(job_id)


def test_read_prospect_brief_markdown_rejects_non_uuid_job_id(
    isolated_jobs_root: Path,
) -> None:
    """``_validate_job_id`` raises ValueError on non-UUID ids — same
    contract as the other ``read_*`` helpers, which the route layer
    relies on to map to HTTP 404."""
    with pytest.raises(ValueError):
        read_prospect_brief_markdown("not-a-uuid")


def test_write_document_manifest_uses_two_space_indent(
    isolated_jobs_root: Path,
) -> None:
    """Mirrors the rest of the module's JSON-formatting policy."""
    job_id = _new_job_id()
    write_document_manifest(
        job_id, {"nested": {"inner": 1}}
    )
    raw = (
        isolated_jobs_root / job_id / "document_manifest.json"
    ).read_text(encoding="utf-8")
    assert '  "nested"' in raw  # two-space indent at depth 1
    assert '    "inner"' in raw  # four-space indent at depth 2
