"""Closed-taxonomy issue tests for ``validate_export_integrity``.

One test per issue code in the closed taxonomy, plus stable-sort and
clean-state assertions. Two amendments to the original Step 37 plan
are pinned here:

  * amendment 1 — ``source_markdown_drift`` is **error** severity, not
    warning.
  * amendment 2 — ``unsupported_export_version`` exists at warning
    severity.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.exporters.lifecycle import (
    ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH,
    ISSUE_MANIFEST_ENTRY_MISSING_FIELD,
    ISSUE_MANIFEST_SHA_MISMATCH,
    ISSUE_MARKDOWN_MISSING,
    ISSUE_MISSING_EXPORT_FILE,
    ISSUE_ORPHAN_EXPORT_FILE,
    ISSUE_SOURCE_ENTRY_DRIFT,
    ISSUE_SOURCE_MARKDOWN_DRIFT,
    ISSUE_UNKNOWN_EXPORT_FORMAT,
    ISSUE_UNSUPPORTED_EXPORT_VERSION,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    reconcile_export_manifest,
    validate_export_integrity,
)
from backend.jobs.storage import (
    export_path_for,
    job_folder,
    write_document_manifest,
    write_export,
    write_prospect_brief_markdown,
)


_MD_TEXT = "# Acme Ltd\n\nBrief body.\n"
_MD_BYTES = _MD_TEXT.encode("utf-8")
_MD_SHA = hashlib.sha256(_MD_BYTES).hexdigest()

_PDF_BYTES = b"%PDF-1.7 fake fixture bytes\n"
_PDF_SHA = hashlib.sha256(_PDF_BYTES).hexdigest()


def _full_entry(**overrides: Any) -> dict[str, Any]:
    """Return a fully-formed export entry. Override individual fields
    by keyword to seed specific failure modes."""
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


def _seed_clean_job(
    *,
    md_text: str = _MD_TEXT,
    md_sha: str | None = None,
    write_pdf: bool = True,
    entry_overrides: dict[str, Any] | None = None,
    raw_entry: dict[str, Any] | None = None,
    omit_entry: bool = False,
    extra_manifest: dict[str, Any] | None = None,
) -> str:
    """Seed a fresh job folder with brief + manifest + (optional) PDF.

    ``entry_overrides`` merges onto the default full entry; ``raw_entry``
    bypasses the default and writes the dict verbatim — needed for the
    "missing required field" test that asserts on absence.
    """
    job_id = str(uuid.uuid4())
    write_prospect_brief_markdown(job_id, md_text)
    sha = md_sha if md_sha is not None else hashlib.sha256(
        md_text.encode("utf-8"),
    ).hexdigest()

    if omit_entry:
        exports_list: list[dict[str, Any]] = []
    elif raw_entry is not None:
        exports_list = [raw_entry]
    else:
        exports_list = [_full_entry(**(entry_overrides or {}))]

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": sha,
        "markdown_byte_length": len(md_text.encode("utf-8")),
        "sections": [],
        "artefacts": {},
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": None,
        "warnings": [],
        "exports": exports_list,
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    write_document_manifest(job_id, manifest)

    if write_pdf:
        write_export(job_id, "pdf", _PDF_BYTES)

    return job_id


# ---------------------------------------------------------------------------
# Clean state — no issues
# ---------------------------------------------------------------------------


def test_clean_state_has_no_issues() -> None:
    job_id = _seed_clean_job()
    issues = validate_export_integrity(job_id)
    assert issues == []


def test_clean_state_with_no_exports_has_no_issues() -> None:
    job_id = _seed_clean_job(omit_entry=True, write_pdf=False)
    issues = validate_export_integrity(job_id)
    assert issues == []


# ---------------------------------------------------------------------------
# One test per issue code
# ---------------------------------------------------------------------------


def test_missing_export_file_issue() -> None:
    job_id = _seed_clean_job(write_pdf=False)
    codes = [i.code for i in validate_export_integrity(job_id)]
    assert ISSUE_MISSING_EXPORT_FILE in codes


def test_missing_export_file_severity() -> None:
    job_id = _seed_clean_job(write_pdf=False)
    issue = next(
        i for i in validate_export_integrity(job_id)
        if i.code == ISSUE_MISSING_EXPORT_FILE
    )
    assert issue.severity == SEVERITY_ERROR


def test_orphan_export_file_issue() -> None:
    """File on disk but the manifest has no matching entry."""
    job_id = _seed_clean_job(omit_entry=True, write_pdf=True)
    issues = validate_export_integrity(job_id)
    codes = [i.code for i in issues]
    assert ISSUE_ORPHAN_EXPORT_FILE in codes
    orphan = next(i for i in issues if i.code == ISSUE_ORPHAN_EXPORT_FILE)
    assert orphan.severity == SEVERITY_WARNING


def test_manifest_sha_mismatch_issue() -> None:
    job_id = _seed_clean_job(entry_overrides={"sha256": "deadbeef" * 8})
    codes = [i.code for i in validate_export_integrity(job_id)]
    assert ISSUE_MANIFEST_SHA_MISMATCH in codes


def test_manifest_sha_mismatch_severity() -> None:
    job_id = _seed_clean_job(entry_overrides={"sha256": "deadbeef" * 8})
    issue = next(
        i for i in validate_export_integrity(job_id)
        if i.code == ISSUE_MANIFEST_SHA_MISMATCH
    )
    assert issue.severity == SEVERITY_ERROR


def test_manifest_byte_length_mismatch_issue() -> None:
    job_id = _seed_clean_job(entry_overrides={"byte_length": 99999})
    codes = [i.code for i in validate_export_integrity(job_id)]
    assert ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH in codes


def test_source_markdown_drift_is_error_severity() -> None:
    """Amendment 1: ``source_markdown_drift`` is error-severity."""
    job_id = _seed_clean_job()
    # Now tamper with prospect_brief.md so its sha no longer matches
    # the manifest's recorded markdown_sha256.
    folder = job_folder(job_id)
    (folder / "prospect_brief.md").write_text(
        _MD_TEXT + "edit\n", encoding="utf-8",
    )
    issues = validate_export_integrity(job_id)
    matching = [i for i in issues if i.code == ISSUE_SOURCE_MARKDOWN_DRIFT]
    assert matching, (
        f"expected {ISSUE_SOURCE_MARKDOWN_DRIFT!r} after Markdown edit; "
        f"got codes {[i.code for i in issues]}"
    )
    assert matching[0].severity == SEVERITY_ERROR


def test_source_entry_drift_issue() -> None:
    """An entry was rendered from an earlier Markdown version."""
    job_id = _seed_clean_job(
        entry_overrides={"source_markdown_sha256": "f" * 64},
    )
    issues = validate_export_integrity(job_id)
    matching = [i for i in issues if i.code == ISSUE_SOURCE_ENTRY_DRIFT]
    assert matching
    assert matching[0].severity == SEVERITY_WARNING


def test_markdown_missing_issue() -> None:
    job_id = _seed_clean_job()
    (job_folder(job_id) / "prospect_brief.md").unlink()
    codes = [i.code for i in validate_export_integrity(job_id)]
    assert ISSUE_MARKDOWN_MISSING in codes


def test_unknown_export_format_issue() -> None:
    # Step 38 promoted "docx" to a known format; use a token that is
    # genuinely outside the closed taxonomy so the negative test stays
    # meaningful.
    job_id = _seed_clean_job(
        entry_overrides={"format": "xlsx"}, write_pdf=False,
    )
    issues = validate_export_integrity(job_id)
    codes = [i.code for i in issues]
    assert ISSUE_UNKNOWN_EXPORT_FORMAT in codes
    matching = next(i for i in issues if i.code == ISSUE_UNKNOWN_EXPORT_FORMAT)
    assert matching.severity == SEVERITY_WARNING


def test_manifest_entry_missing_field_issue() -> None:
    entry = _full_entry()
    del entry["sha256"]
    job_id = _seed_clean_job(raw_entry=entry, write_pdf=True)
    codes = [i.code for i in validate_export_integrity(job_id)]
    assert ISSUE_MANIFEST_ENTRY_MISSING_FIELD in codes


def test_unsupported_export_version_issue() -> None:
    """Amendment 2: a version string outside the validator's known set
    surfaces as ``unsupported_export_version`` at warning severity."""
    job_id = _seed_clean_job(
        entry_overrides={"exporter_version": "999.999.999"},
    )
    issues = validate_export_integrity(job_id)
    matching = [
        i for i in issues if i.code == ISSUE_UNSUPPORTED_EXPORT_VERSION
    ]
    assert matching, (
        "expected unsupported_export_version on an unknown version; "
        f"got codes {[i.code for i in issues]}"
    )
    assert matching[0].severity == SEVERITY_WARNING


# ---------------------------------------------------------------------------
# Stable sort
# ---------------------------------------------------------------------------


def test_issue_order_is_stable_across_runs() -> None:
    """Two independent validator runs over the same on-disk state must
    return byte-identical issue lists."""
    job_id = _seed_clean_job(write_pdf=False)
    a = validate_export_integrity(job_id)
    b = validate_export_integrity(job_id)
    assert [(i.code, i.severity, i.format, i.filename) for i in a] == [
        (i.code, i.severity, i.format, i.filename) for i in b
    ]


def test_errors_sort_before_warnings() -> None:
    """Mix at least one error and one warning, then prove the error
    sorts first."""
    job_id = _seed_clean_job(
        entry_overrides={
            "exporter_version": "999.999.999",  # warning
            "sha256": "deadbeef" * 8,  # error
        },
    )
    issues = validate_export_integrity(job_id)
    severities = [i.severity for i in issues]
    # First error must come before first warning.
    first_error = severities.index(SEVERITY_ERROR)
    first_warning = next(
        (i for i, s in enumerate(severities) if s == SEVERITY_WARNING),
        None,
    )
    if first_warning is not None:
        assert first_error < first_warning


# ---------------------------------------------------------------------------
# Reconciliation is dry-run only
# ---------------------------------------------------------------------------


def test_reconcile_export_manifest_is_dry_run_only() -> None:
    job_id = _seed_clean_job(omit_entry=True, write_pdf=True)
    result = reconcile_export_manifest(job_id, dry_run=True)
    assert result["dry_run"] is True
    assert any(
        "orphan" in action.lower() for action in result["actions"]
    )


def test_reconcile_export_manifest_raises_when_write_mode_requested() -> None:
    job_id = _seed_clean_job()
    with pytest.raises(NotImplementedError):
        reconcile_export_manifest(job_id, dry_run=False)


def test_reconcile_does_not_mutate_manifest_on_disk() -> None:
    """The reconciler is documented as side-effect free in dry-run; the
    manifest text must be byte-identical before and after."""
    job_id = _seed_clean_job(omit_entry=True, write_pdf=True)
    folder = job_folder(job_id)
    before = (folder / "document_manifest.json").read_text("utf-8")
    reconcile_export_manifest(job_id, dry_run=True)
    after = (folder / "document_manifest.json").read_text("utf-8")
    assert before == after


# ---------------------------------------------------------------------------
# validate_export_integrity behavioural read-only contract
# ---------------------------------------------------------------------------


def test_validate_does_not_mutate_manifest_or_pdf() -> None:
    job_id = _seed_clean_job()
    folder = job_folder(job_id)
    manifest_before = (folder / "document_manifest.json").read_text("utf-8")
    pdf_before = export_path_for(job_id, "pdf").read_bytes()

    validate_export_integrity(job_id)

    manifest_after = (folder / "document_manifest.json").read_text("utf-8")
    pdf_after = export_path_for(job_id, "pdf").read_bytes()
    assert manifest_before == manifest_after
    assert pdf_before == pdf_after
