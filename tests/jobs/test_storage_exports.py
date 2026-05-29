"""Tests for the Step 36 / 40 export storage helpers.

The helpers under :mod:`backend.jobs.storage`:

  - :func:`write_export` / :func:`read_export_bytes` /
    :func:`export_path_for` — file-level I/O for the stable-alias
    ``exports/{filename}`` artefacts.
  - :func:`upsert_export_entry` — manifest-level append-only lineage
    that maintains the v3 ``exports[]`` invariants (Step 40):

      * entries are append-only and immutable; replace-in-place is
        gone, each render appends a new lineage entry;
      * ``version`` is monotonic per (job_id, format), starting at 1;
      * ``supersedes`` points at the prior latest ``export_id`` (or
        ``None`` on v1);
      * ``manifest_sha256_at_export`` captures the input manifest's
        SHA-256;
      * ``regeneration_count = version - 1`` (derived, persisted for
        Step 36/38 wire-compat);
      * idempotency: a re-render whose ``sha256`` matches the current
        ``latest_export_id[fmt]`` is a no-op (``was_appended=False``);
      * v1/v2 manifests are lazily projected to v3 on read and
        materialised on the next write.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from backend.jobs.storage import (
    JobNotFound,
    export_path_for,
    find_export_entry,
    read_document_manifest,
    read_export_bytes,
    upsert_export_entry,
    write_document_manifest,
    write_export,
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


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


def _v2_manifest(job_id: str) -> dict:
    return {
        **_V1_MANIFEST,
        "job_id": job_id,
        "schema_version": 2,
        "exports": [],
    }


# ---------------------------------------------------------------------------
# write_export / read_export_bytes
# ---------------------------------------------------------------------------


def test_write_export_writes_under_exports_subfolder(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    path = write_export(job_id, "pdf", b"%PDF-1.7\n")
    assert path == export_path_for(job_id, "pdf")
    assert path.parent.name == "exports"
    assert path.name == "prospect_brief.pdf"


def test_write_export_is_atomic(isolated_jobs_root: Path) -> None:
    """The helper writes via a ``.tmp`` sibling + ``os.replace``;
    no ``.tmp`` file should remain after a successful call."""
    job_id = _new_job_id()
    write_export(job_id, "pdf", b"%PDF-1.7\nfoo")
    folder = export_path_for(job_id, "pdf").parent
    leftovers = [p for p in folder.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_write_export_overwrites_existing_file(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_export(job_id, "pdf", b"first")
    write_export(job_id, "pdf", b"second")
    assert read_export_bytes(job_id, "pdf") == b"second"


def test_read_export_bytes_round_trips(isolated_jobs_root: Path) -> None:
    job_id = _new_job_id()
    payload = b"%PDF-1.7\nhello\n%%EOF\n"
    write_export(job_id, "pdf", payload)
    assert read_export_bytes(job_id, "pdf") == payload


def test_read_export_bytes_raises_when_absent(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    with pytest.raises(JobNotFound):
        read_export_bytes(job_id, "pdf")


def test_write_export_rejects_unknown_format(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    with pytest.raises(ValueError):
        # Step 38 added "docx" to the known set; pick a token that
        # remains unknown so the negative test stays meaningful.
        write_export(job_id, "xlsx", b"x")


# ---------------------------------------------------------------------------
# upsert_export_entry
# ---------------------------------------------------------------------------


def test_upsert_inserts_entry_on_first_call(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_document_manifest(job_id, _v2_manifest(job_id))
    entry = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": "f" * 64,
        "source_markdown_sha256": "a" * 64,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "warnings": [],
    }
    final, was_appended = upsert_export_entry(job_id, entry)
    assert was_appended is True
    assert final["regeneration_count"] == 0
    assert final["version"] == 1
    assert final["export_id"] == "f" * 64
    assert final["supersedes"] is None
    # manifest_sha256_at_export captures the v2 manifest's input bytes;
    # it must be a 64-char hex digest (or empty if the manifest could
    # not be read, which is not the case here).
    assert isinstance(final["manifest_sha256_at_export"], str)
    assert len(final["manifest_sha256_at_export"]) == 64

    manifest = read_document_manifest(job_id)
    assert manifest["schema_version"] == 3
    assert len(manifest["exports"]) == 1
    assert manifest["exports"][0]["format"] == "pdf"
    assert manifest["exports"][0]["sha256"] == "f" * 64
    assert manifest["exports"][0]["version"] == 1
    assert manifest["latest_export_id"]["pdf"] == "f" * 64


def test_upsert_appends_new_entry_on_regeneration_with_new_bytes(
    isolated_jobs_root: Path,
) -> None:
    """Step 40 — ``exports[]`` is append-only. A second render with
    different bytes appends a NEW entry; it does not replace.
    ``regeneration_count`` is derived as ``version - 1``, so the new
    entry reports 1 while the prior entry stays untouched at 0.
    """
    job_id = _new_job_id()
    write_document_manifest(job_id, _v2_manifest(job_id))
    base = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": "f" * 64,
        "source_markdown_sha256": "a" * 64,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "warnings": [],
    }
    first, first_appended = upsert_export_entry(job_id, base)
    assert first_appended is True
    second_input = dict(base)
    second_input["sha256"] = "e" * 64
    second, second_appended = upsert_export_entry(job_id, second_input)
    assert second_appended is True
    assert second["regeneration_count"] == 1
    assert second["version"] == 2
    assert second["export_id"] == "e" * 64
    assert second["supersedes"] == "f" * 64

    # Append-only: BOTH entries are on disk; the older entry is left
    # strictly unchanged.
    manifest = read_document_manifest(job_id)
    assert len(manifest["exports"]) == 2
    assert manifest["exports"][0]["sha256"] == "f" * 64
    assert manifest["exports"][0]["version"] == 1
    assert manifest["exports"][0]["supersedes"] is None
    assert manifest["exports"][0]["regeneration_count"] == 0
    assert manifest["exports"][1]["sha256"] == "e" * 64
    assert manifest["exports"][1]["version"] == 2
    assert manifest["exports"][1]["supersedes"] == "f" * 64
    assert manifest["exports"][1]["regeneration_count"] == 1
    assert manifest["latest_export_id"]["pdf"] == "e" * 64


def test_upsert_is_idempotent_when_sha_matches_latest(
    isolated_jobs_root: Path,
) -> None:
    """Step 40 idempotency — re-rendering identical bytes is a no-op.

    The same entry (same ``sha256``) returned twice must not produce
    a second ``exports[]`` entry; ``was_appended`` is ``False`` and
    the existing entry is returned verbatim.
    """
    job_id = _new_job_id()
    write_document_manifest(job_id, _v2_manifest(job_id))
    entry = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": "f" * 64,
        "source_markdown_sha256": "a" * 64,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "warnings": [],
    }
    first, first_appended = upsert_export_entry(job_id, entry)
    second, second_appended = upsert_export_entry(job_id, dict(entry))
    assert first_appended is True
    assert second_appended is False
    assert second["export_id"] == first["export_id"]
    assert second["version"] == 1

    manifest = read_document_manifest(job_id)
    assert len(manifest["exports"]) == 1


def test_upsert_migrates_legacy_manifest_to_v3(
    isolated_jobs_root: Path,
) -> None:
    """A pre-Step-35 manifest on disk has ``schema_version=1`` and no
    ``exports`` key. Step 40 lazily projects to v3 on read and
    materialises v3 on the first upsert.
    """
    job_id = _new_job_id()
    write_document_manifest(job_id, {**_V1_MANIFEST, "job_id": job_id})
    entry = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": "0" * 64,
        "source_markdown_sha256": "a" * 64,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "warnings": [],
    }
    upsert_export_entry(job_id, entry)

    manifest = read_document_manifest(job_id)
    assert manifest["schema_version"] == 3
    assert "exports" in manifest
    assert len(manifest["exports"]) == 1
    assert manifest["latest_export_id"]["pdf"] == "0" * 64


def test_upsert_preserves_all_non_exports_manifest_fields(
    isolated_jobs_root: Path,
) -> None:
    """The upsert helper must never lose pre-existing manifest fields."""
    job_id = _new_job_id()
    original = _v2_manifest(job_id)
    original["company_name"] = "Acme Different Ltd"
    original["markdown_sha256"] = "b" * 64
    original["sections"] = [{"key": "intro", "byte_length": 42}]
    original["warnings"] = ["a warning"]
    write_document_manifest(job_id, original)

    entry = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": "0" * 64,
        "source_markdown_sha256": "b" * 64,
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "regeneration_count": 0,
        "warnings": [],
    }
    upsert_export_entry(job_id, entry)

    final = read_document_manifest(job_id)
    assert final["company_name"] == "Acme Different Ltd"
    assert final["markdown_sha256"] == "b" * 64
    assert final["sections"] == [{"key": "intro", "byte_length": 42}]
    assert final["warnings"] == ["a warning"]


def test_upsert_raises_when_manifest_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    entry = {"format": "pdf"}
    with pytest.raises(JobNotFound):
        upsert_export_entry(job_id, entry)


def test_upsert_rejects_entry_without_format(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_document_manifest(job_id, _v2_manifest(job_id))
    with pytest.raises(ValueError):
        upsert_export_entry(job_id, {"filename": "x.pdf"})


# ---------------------------------------------------------------------------
# find_export_entry (Step 41)
# ---------------------------------------------------------------------------


def _seed_with_entry(job_id: str, sha: str) -> dict:
    """Write a v2 manifest, upsert one PDF entry, return final entry."""
    write_document_manifest(job_id, _v2_manifest(job_id))
    entry = {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 1024,
        "sha256": sha,
        "source_markdown_sha256": "a" * 64,
        "generated_at": "2026-05-29T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "warnings": [],
    }
    final, _ = upsert_export_entry(job_id, entry)
    return final


def test_find_export_entry_returns_entry_when_present(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    final = _seed_with_entry(job_id, "f" * 64)
    result = find_export_entry(job_id, "pdf", "f" * 64)
    assert result is not None
    assert result["export_id"] == "f" * 64
    assert result["version"] == 1
    assert result["format"] == "pdf"


def test_find_export_entry_returns_none_when_no_match(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_with_entry(job_id, "f" * 64)
    # Different sha → no match.
    assert find_export_entry(job_id, "pdf", "e" * 64) is None


def test_find_export_entry_returns_none_when_manifest_missing(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    # No manifest written — must return None, not raise.
    assert find_export_entry(job_id, "pdf", "f" * 64) is None


def test_find_export_entry_rejects_malformed_export_id(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    _seed_with_entry(job_id, "f" * 64)
    # Too short.
    assert find_export_entry(job_id, "pdf", "f" * 10) is None
    # Too long.
    assert find_export_entry(job_id, "pdf", "f" * 65) is None
    # Non-hex.
    assert find_export_entry(job_id, "pdf", "g" * 64) is None
    # Uppercase (not accepted even though it is a valid hex string in
    # some conventions — we require lowercase to match the stored value).
    assert find_export_entry(job_id, "pdf", "F" * 64) is None
    # Not a string at all.
    assert find_export_entry(job_id, "pdf", None) is None  # type: ignore[arg-type]


def test_find_export_entry_raises_on_unknown_format(
    isolated_jobs_root: Path,
) -> None:
    job_id = _new_job_id()
    write_document_manifest(job_id, _v2_manifest(job_id))
    with pytest.raises(ValueError):
        find_export_entry(job_id, "xlsx", "f" * 64)


def test_find_export_entry_projects_legacy_v1_manifest(
    isolated_jobs_root: Path,
) -> None:
    """A legacy v1 manifest (no ``exports`` key) is projected to v3
    in memory without an on-disk write; the function returns None
    since the projected array is empty, not an exception."""
    job_id = _new_job_id()
    write_document_manifest(job_id, {**_V1_MANIFEST, "job_id": job_id})
    # v1 manifest has no exports — should return None, no on-disk mutation.
    assert find_export_entry(job_id, "pdf", "f" * 64) is None
    # Disk still has v1 (no upsert happened).
    on_disk = read_document_manifest(job_id)
    assert on_disk.get("schema_version") == 1
    assert "exports" not in on_disk
