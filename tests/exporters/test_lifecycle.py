"""Pure-function matrix for the export lifecycle projections (Step 37).

Covers :func:`compute_export_lifecycle` and
:func:`compute_manifest_lifecycle` — both are pure dict-in / dict-out
helpers with no I/O. The on-disk validator and the issue taxonomy live
in :mod:`tests.exporters.test_integrity`.
"""

from __future__ import annotations

from backend.exporters.lifecycle import (
    ISSUE_SOURCE_ENTRY_DRIFT,
    ISSUE_SOURCE_MARKDOWN_DRIFT,
    compute_export_lifecycle,
    compute_manifest_lifecycle,
)


_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


def _entry(sha: str) -> dict:
    return {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "source_markdown_sha256": sha,
    }


# ---------------------------------------------------------------------------
# compute_export_lifecycle — per-entry projection
# ---------------------------------------------------------------------------


def test_entry_in_sync_is_not_stale() -> None:
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=_SHA_A,
        on_disk_markdown_sha256=_SHA_A,
    )
    assert result == {"stale": False, "reasons": []}


def test_entry_source_drift_is_stale_warning() -> None:
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=_SHA_B,
        on_disk_markdown_sha256=_SHA_B,
    )
    assert result["stale"] is True
    assert ISSUE_SOURCE_ENTRY_DRIFT in result["reasons"]


def test_entry_markdown_drift_is_stale_on_entry_projection() -> None:
    """When ``manifest.markdown_sha256`` no longer matches the on-disk
    Markdown, the per-entry projection mirrors that drift so callers
    that only inspect the entry projection still see the stale flag.
    """
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=_SHA_A,
        on_disk_markdown_sha256=_SHA_B,
    )
    assert result["stale"] is True
    assert ISSUE_SOURCE_MARKDOWN_DRIFT in result["reasons"]


def test_entry_both_drift_axes_combine() -> None:
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=_SHA_B,
        on_disk_markdown_sha256=_SHA_C,
    )
    assert result["stale"] is True
    assert ISSUE_SOURCE_ENTRY_DRIFT in result["reasons"]
    assert ISSUE_SOURCE_MARKDOWN_DRIFT in result["reasons"]


def test_entry_handles_missing_on_disk_sha() -> None:
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=_SHA_A,
        on_disk_markdown_sha256=None,
    )
    # No drift can be computed against a missing source — entry is
    # in sync with the manifest, so projection returns not-stale.
    assert result["stale"] is False


def test_entry_handles_missing_manifest_sha() -> None:
    result = compute_export_lifecycle(
        _entry(_SHA_A),
        manifest_markdown_sha256=None,
        on_disk_markdown_sha256=_SHA_A,
    )
    assert result["stale"] is False


# ---------------------------------------------------------------------------
# compute_manifest_lifecycle — manifest-level rollup
# ---------------------------------------------------------------------------


def test_manifest_in_sync_has_no_drift() -> None:
    manifest = {
        "markdown_sha256": _SHA_A,
        "exports": [_entry(_SHA_A)],
    }
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=_SHA_A,
    )
    assert result["source_markdown_drift"] is False
    assert result["any_stale_export"] is False
    assert result["on_disk_markdown_sha256"] == _SHA_A


def test_manifest_source_markdown_drift_flagged() -> None:
    manifest = {
        "markdown_sha256": _SHA_A,
        "exports": [],
    }
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=_SHA_B,
    )
    assert result["source_markdown_drift"] is True


def test_manifest_any_stale_export_propagates() -> None:
    manifest = {
        "markdown_sha256": _SHA_A,
        "exports": [_entry(_SHA_B)],  # entry-source drift
    }
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=_SHA_A,
    )
    assert result["source_markdown_drift"] is False
    assert result["any_stale_export"] is True


def test_manifest_missing_on_disk_sha_does_not_flag_drift() -> None:
    """If the Markdown file is absent we cannot prove drift —
    ``source_markdown_drift`` stays False. The validator's
    ``markdown_missing`` issue is the surface for the absence
    case, not the lifecycle projection.
    """
    manifest = {
        "markdown_sha256": _SHA_A,
        "exports": [_entry(_SHA_A)],
    }
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=None,
    )
    assert result["source_markdown_drift"] is False
    assert result["on_disk_markdown_sha256"] is None


def test_manifest_with_no_exports_array_is_handled() -> None:
    manifest = {"markdown_sha256": _SHA_A}
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=_SHA_A,
    )
    assert result["any_stale_export"] is False


def test_manifest_with_non_list_exports_is_handled() -> None:
    manifest = {"markdown_sha256": _SHA_A, "exports": "not-a-list"}
    result = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=_SHA_A,
    )
    assert result["any_stale_export"] is False
