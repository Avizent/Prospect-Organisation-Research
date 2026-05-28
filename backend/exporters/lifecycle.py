"""Export lifecycle governance — strictly read-only projections (Step 37).

This module turns the on-disk facts about an export (the manifest's
``exports[]`` entry, the actual file under
``~/.ans-tool/jobs/{job_id}/exports/``, and the live
``prospect_brief.md``) into two *computed* views the rest of the app
can act on:

  * **Lifecycle projections** — three orthogonal staleness axes per
    entry, plus a manifest-level rollup. Used by
    ``GET /api/jobs/{id}/manifest`` (response-time enrichment, not
    persisted) and by the export GET handler (stale headers).
  * **Integrity issues** — a closed taxonomy of governance problems,
    each carrying a stable ``code`` + severity. Used by
    ``GET /api/jobs/{id}/exports/validate``.

Hard rules
----------

* Nothing in this module mutates state. No file writes, no manifest
  upserts, no ``state.json`` transitions. A static fence in
  ``tests/exporters/test_lifecycle_is_readonly.py`` enforces the rule
  by AST-scanning the source for writer references.
* No HTTP, no network, no Anthropic, no Keychain. The package-wide
  fence in ``tests/exporters/test_no_forbidden_imports.py`` covers
  this, but call it out explicitly here too.
* The only storage helpers this module touches are the read-only ones
  (``read_document_manifest``, ``read_export_bytes``,
  ``read_prospect_brief_markdown``, ``export_path_for``,
  ``job_folder``).

Staleness axes
--------------

A given ``exports[]`` entry can be stale along three independent axes:

  1. ``source_markdown_drift`` — ``document_manifest.markdown_sha256``
     no longer matches ``prospect_brief.md`` on disk. This is the
     **error-severity** condition (amendment 1) because the
     manifest's notion of "current Markdown" has diverged from the
     actual file, and every downstream artefact (including the next
     export) is suspect. Surfaced as a prominent manifest-level
     banner.
  2. ``source_entry_drift`` — the export was rendered from an earlier
     Markdown version (``entry.source_markdown_sha256`` !=
     ``manifest.markdown_sha256``). Warning severity — the file is
     still downloadable, but the operator should regenerate before
     sharing.
  3. ``file_drift`` — the export's recorded ``sha256`` no longer
     matches the bytes on disk. Either someone edited the file out of
     band, or the manifest entry was hand-edited.

Issue taxonomy (closed set)
---------------------------

Severity levels: ``error`` (must regenerate), ``warning``
(operator-visible drift), ``info`` (advisory).

  * ``missing_export_file`` (error)               — manifest entry has
                                                    no on-disk file.
  * ``orphan_export_file`` (warning)              — on-disk file with
                                                    no manifest entry.
  * ``manifest_sha_mismatch`` (error)             — file bytes don't
                                                    hash to the
                                                    recorded sha256.
  * ``manifest_byte_length_mismatch`` (error)     — file length differs
                                                    from recorded
                                                    byte_length.
  * ``source_markdown_drift`` (**error** — amendment 1)
                                                    manifest.markdown_sha256
                                                    no longer matches
                                                    prospect_brief.md.
  * ``source_entry_drift`` (warning)              — entry was rendered
                                                    from an earlier
                                                    Markdown version.
  * ``markdown_missing`` (error)                  — prospect_brief.md
                                                    absent.
  * ``unknown_export_format`` (warning)           — entry.format outside
                                                    the supported set.
  * ``manifest_entry_missing_required_field`` (error) — required
                                                    field absent on
                                                    entry.
  * ``unsupported_export_version`` (**warning** — amendment 2) —
                                                    entry.exporter_version
                                                    not recognised by
                                                    the current
                                                    validator. Lets
                                                    older artefacts
                                                    keep flowing while
                                                    flagging the
                                                    version gap.

Reconciliation
--------------

:func:`reconcile_export_manifest` is **signature-only / dry-run-only**.
It reports the actions a write-mode helper would take, but never
takes them. A future write-mode reconciler will land in its own step
behind explicit operator confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.exporters import EXPORTER_VERSION
from backend.exporters.determinism import sha256_hex
from backend.jobs.storage import (
    JobNotFound,
    export_path_for,
    job_folder,
    read_document_manifest,
    read_export_bytes,
    read_prospect_brief_markdown,
)


# ---------------------------------------------------------------------------
# Issue taxonomy — closed set
# ---------------------------------------------------------------------------
#
# Codes are pinned constants so callers can branch on them without
# string-typo'ing. Severity is part of the taxonomy: a future code
# change is a deliberate audit surface, not a free-text addition.

ISSUE_MISSING_EXPORT_FILE: str = "missing_export_file"
ISSUE_ORPHAN_EXPORT_FILE: str = "orphan_export_file"
ISSUE_MANIFEST_SHA_MISMATCH: str = "manifest_sha_mismatch"
ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH: str = "manifest_byte_length_mismatch"
ISSUE_SOURCE_MARKDOWN_DRIFT: str = "source_markdown_drift"
ISSUE_SOURCE_ENTRY_DRIFT: str = "source_entry_drift"
ISSUE_MARKDOWN_MISSING: str = "markdown_missing"
ISSUE_UNKNOWN_EXPORT_FORMAT: str = "unknown_export_format"
ISSUE_MANIFEST_ENTRY_MISSING_FIELD: str = "manifest_entry_missing_required_field"
ISSUE_UNSUPPORTED_EXPORT_VERSION: str = "unsupported_export_version"


# Severity values are a closed set so the frontend can map them
# deterministically to CSS classes.
SEVERITY_ERROR: str = "error"
SEVERITY_WARNING: str = "warning"
SEVERITY_INFO: str = "info"


# Amendment 1: ``source_markdown_drift`` is error-severity.
# Amendment 2: ``unsupported_export_version`` is warning-severity.
_ISSUE_SEVERITY: dict[str, str] = {
    ISSUE_MISSING_EXPORT_FILE: SEVERITY_ERROR,
    ISSUE_ORPHAN_EXPORT_FILE: SEVERITY_WARNING,
    ISSUE_MANIFEST_SHA_MISMATCH: SEVERITY_ERROR,
    ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH: SEVERITY_ERROR,
    ISSUE_SOURCE_MARKDOWN_DRIFT: SEVERITY_ERROR,
    ISSUE_SOURCE_ENTRY_DRIFT: SEVERITY_WARNING,
    ISSUE_MARKDOWN_MISSING: SEVERITY_ERROR,
    ISSUE_UNKNOWN_EXPORT_FORMAT: SEVERITY_WARNING,
    ISSUE_MANIFEST_ENTRY_MISSING_FIELD: SEVERITY_ERROR,
    ISSUE_UNSUPPORTED_EXPORT_VERSION: SEVERITY_WARNING,
}


# Closed sets of "known" tokens. The lifecycle module owns the
# validator's notion of "recognised"; the storage layer owns the
# notion of "supported by the renderer". They overlap today but
# stay deliberately separate so a stale schema does not silently
# accept new formats.
_KNOWN_EXPORT_FORMATS: frozenset[str] = frozenset({"pdf"})
_KNOWN_EXPORTER_VERSIONS: frozenset[str] = frozenset({EXPORTER_VERSION})
_REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "format",
    "filename",
    "byte_length",
    "sha256",
    "source_markdown_sha256",
    "exporter_version",
    "template_version",
)


@dataclass(frozen=True)
class IntegrityIssue:
    """A single governance condition discovered by the validator.

    ``code`` is one of the ``ISSUE_*`` constants above. ``severity`` is
    looked up via :data:`_ISSUE_SEVERITY` at construction time; the
    field stays on the dataclass so callers do not need to import the
    severity map. ``format`` and ``filename`` scope the issue to a
    specific export entry where applicable (manifest-level issues
    leave them at the empty string).
    """

    code: str
    severity: str
    message: str
    format: str = ""
    filename: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "format": self.format,
            "filename": self.filename,
        }


def _issue(
    code: str,
    message: str,
    *,
    format: str = "",
    filename: str = "",
) -> IntegrityIssue:
    return IntegrityIssue(
        code=code,
        severity=_ISSUE_SEVERITY[code],
        message=message,
        format=format,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Per-entry / manifest lifecycle projections
# ---------------------------------------------------------------------------


def compute_export_lifecycle(
    entry: dict[str, Any],
    *,
    manifest_markdown_sha256: str | None,
    on_disk_markdown_sha256: str | None,
) -> dict[str, Any]:
    """Compute the per-entry stale projection.

    Returns a small dict with ``stale`` (bool) and ``reasons``
    (list[str]). The reasons list contains zero or more of:

      * ``source_entry_drift``
      * ``source_markdown_drift`` (mirrored from the manifest level so
        the entry alone is enough to decide UX)

    No file I/O happens here — the caller is expected to have already
    read whatever it needs from disk.
    """
    reasons: list[str] = []

    entry_sha = entry.get("source_markdown_sha256")
    if (
        entry_sha is not None
        and manifest_markdown_sha256 is not None
        and entry_sha != manifest_markdown_sha256
    ):
        reasons.append(ISSUE_SOURCE_ENTRY_DRIFT)

    if (
        manifest_markdown_sha256 is not None
        and on_disk_markdown_sha256 is not None
        and manifest_markdown_sha256 != on_disk_markdown_sha256
    ):
        reasons.append(ISSUE_SOURCE_MARKDOWN_DRIFT)

    return {
        "stale": bool(reasons),
        "reasons": reasons,
    }


def compute_manifest_lifecycle(
    manifest: dict[str, Any],
    *,
    on_disk_markdown_sha256: str | None,
) -> dict[str, Any]:
    """Compute the manifest-level lifecycle rollup.

    Returns a small dict:

      * ``source_markdown_drift`` (bool) — amendment 1 surfaces this
        as a top-level prominent banner.
      * ``on_disk_markdown_sha256`` (str | None) — projected back so
        the frontend can display "Manifest SHA-256" vs "Source SHA-256"
        side-by-side without recomputing.
      * ``any_stale_export`` (bool) — at least one ``exports[]`` entry
        is stale on any axis.
    """
    manifest_sha = manifest.get("markdown_sha256")
    drift = bool(
        manifest_sha is not None
        and on_disk_markdown_sha256 is not None
        and manifest_sha != on_disk_markdown_sha256
    )

    any_stale = False
    exports = manifest.get("exports")
    if isinstance(exports, list):
        for entry in exports:
            if not isinstance(entry, dict):
                continue
            projection = compute_export_lifecycle(
                entry,
                manifest_markdown_sha256=manifest_sha
                if isinstance(manifest_sha, str) else None,
                on_disk_markdown_sha256=on_disk_markdown_sha256,
            )
            if projection["stale"]:
                any_stale = True
                break

    return {
        "source_markdown_drift": drift,
        "on_disk_markdown_sha256": on_disk_markdown_sha256,
        "any_stale_export": any_stale,
    }


# ---------------------------------------------------------------------------
# Detect / validate — touch the filesystem (read-only)
# ---------------------------------------------------------------------------


def _read_on_disk_markdown_sha(job_id: str) -> str | None:
    """Return ``sha256_hex`` of the on-disk Markdown, or ``None`` if absent."""
    try:
        text = read_prospect_brief_markdown(job_id)
    except JobNotFound:
        return None
    return sha256_hex(text.encode("utf-8"))


def detect_stale_exports(job_id: str) -> dict[str, Any]:
    """Compute the manifest + per-entry lifecycle projection for ``job_id``.

    Reads the manifest and the on-disk Markdown. Returns a dict:

      * ``manifest`` — the result of :func:`compute_manifest_lifecycle`.
      * ``entries`` — list of ``{format, lifecycle}`` records, one per
        ``exports[]`` entry, in manifest order.

    Raises :class:`JobNotFound` if the manifest is absent — the GET
    routes map that to 404. A missing Markdown does NOT raise; it
    surfaces as ``on_disk_markdown_sha256 = None``, which the
    projection logic handles deterministically.
    """
    manifest = read_document_manifest(job_id)
    on_disk_sha = _read_on_disk_markdown_sha(job_id)

    manifest_view = compute_manifest_lifecycle(
        manifest, on_disk_markdown_sha256=on_disk_sha,
    )

    manifest_sha = manifest.get("markdown_sha256")
    manifest_sha_str = manifest_sha if isinstance(manifest_sha, str) else None

    entries: list[dict[str, Any]] = []
    exports = manifest.get("exports")
    if isinstance(exports, list):
        for entry in exports:
            if not isinstance(entry, dict):
                continue
            fmt = entry.get("format")
            entries.append({
                "format": fmt if isinstance(fmt, str) else "",
                "lifecycle": compute_export_lifecycle(
                    entry,
                    manifest_markdown_sha256=manifest_sha_str,
                    on_disk_markdown_sha256=on_disk_sha,
                ),
            })

    return {
        "manifest": manifest_view,
        "entries": entries,
    }


def _validate_entry_required_fields(
    entry: dict[str, Any],
) -> list[IntegrityIssue]:
    """Check every required entry field is present."""
    issues: list[IntegrityIssue] = []
    fmt_raw = entry.get("format")
    fmt = fmt_raw if isinstance(fmt_raw, str) else ""
    filename_raw = entry.get("filename")
    filename = filename_raw if isinstance(filename_raw, str) else ""
    for field in _REQUIRED_ENTRY_FIELDS:
        if field not in entry or entry[field] in (None, ""):
            issues.append(_issue(
                ISSUE_MANIFEST_ENTRY_MISSING_FIELD,
                f"entry is missing required field {field!r}",
                format=fmt,
                filename=filename,
            ))
    return issues


def validate_export_integrity(job_id: str) -> list[IntegrityIssue]:
    """Walk every export and produce the closed-taxonomy issue list.

    Read-only: no manifest writes, no state mutations, no file
    rewrites. The returned list is sorted with a stable key so two
    callers can diff issue lists across runs.

    Raises :class:`JobNotFound` if the manifest is absent (the route
    maps that to 404).
    """
    manifest = read_document_manifest(job_id)
    on_disk_sha = _read_on_disk_markdown_sha(job_id)

    issues: list[IntegrityIssue] = []

    # --- Manifest-level: markdown source presence + drift ------------
    if on_disk_sha is None:
        issues.append(_issue(
            ISSUE_MARKDOWN_MISSING,
            "prospect_brief.md is not on disk for this job",
        ))

    manifest_sha = manifest.get("markdown_sha256")
    manifest_sha_str = manifest_sha if isinstance(manifest_sha, str) else None
    if (
        manifest_sha_str is not None
        and on_disk_sha is not None
        and manifest_sha_str != on_disk_sha
    ):
        issues.append(_issue(
            ISSUE_SOURCE_MARKDOWN_DRIFT,
            "manifest.markdown_sha256 no longer matches prospect_brief.md",
        ))

    # --- Per-entry checks --------------------------------------------
    exports = manifest.get("exports")
    entries: list[dict[str, Any]] = []
    if isinstance(exports, list):
        for entry in exports:
            if isinstance(entry, dict):
                entries.append(entry)

    formats_seen: set[str] = set()
    for entry in entries:
        fmt_raw = entry.get("format")
        fmt = fmt_raw if isinstance(fmt_raw, str) else ""
        filename_raw = entry.get("filename")
        filename = filename_raw if isinstance(filename_raw, str) else ""
        formats_seen.add(fmt)

        # Required fields first — many downstream checks only make
        # sense once we know the entry's structural shape is intact.
        issues.extend(_validate_entry_required_fields(entry))

        # Unknown format — record but do not skip subsequent checks
        # that don't need a registered storage path.
        if fmt and fmt not in _KNOWN_EXPORT_FORMATS:
            issues.append(_issue(
                ISSUE_UNKNOWN_EXPORT_FORMAT,
                f"entry.format {fmt!r} is not in the supported set",
                format=fmt,
                filename=filename,
            ))

        # Unsupported exporter version (amendment 2).
        version_raw = entry.get("exporter_version")
        if (
            isinstance(version_raw, str)
            and version_raw
            and version_raw not in _KNOWN_EXPORTER_VERSIONS
        ):
            issues.append(_issue(
                ISSUE_UNSUPPORTED_EXPORT_VERSION,
                (
                    f"entry.exporter_version {version_raw!r} is not "
                    "recognised by the current validator"
                ),
                format=fmt,
                filename=filename,
            ))

        # On-disk file checks — only meaningful for known formats.
        if fmt in _KNOWN_EXPORT_FORMATS:
            try:
                payload = read_export_bytes(job_id, fmt)
            except JobNotFound:
                issues.append(_issue(
                    ISSUE_MISSING_EXPORT_FILE,
                    (
                        f"manifest has an entry for {fmt!r} but the "
                        "file is not on disk"
                    ),
                    format=fmt,
                    filename=filename,
                ))
                continue

            recorded_sha = entry.get("sha256")
            if isinstance(recorded_sha, str) and recorded_sha:
                actual_sha = sha256_hex(payload)
                if actual_sha != recorded_sha:
                    issues.append(_issue(
                        ISSUE_MANIFEST_SHA_MISMATCH,
                        (
                            "file bytes do not hash to the manifest's "
                            "recorded sha256"
                        ),
                        format=fmt,
                        filename=filename,
                    ))

            recorded_len = entry.get("byte_length")
            if isinstance(recorded_len, int) and recorded_len >= 0:
                if len(payload) != recorded_len:
                    issues.append(_issue(
                        ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH,
                        (
                            f"file is {len(payload)} bytes but manifest "
                            f"records {recorded_len}"
                        ),
                        format=fmt,
                        filename=filename,
                    ))

            entry_md_sha = entry.get("source_markdown_sha256")
            if (
                isinstance(entry_md_sha, str)
                and manifest_sha_str is not None
                and entry_md_sha != manifest_sha_str
            ):
                issues.append(_issue(
                    ISSUE_SOURCE_ENTRY_DRIFT,
                    (
                        "export was rendered from an earlier Markdown "
                        "version"
                    ),
                    format=fmt,
                    filename=filename,
                ))

    # --- Orphan files: known-format files present on disk but with no
    #    matching manifest entry. We only look at the closed set of
    #    known formats — random files in ``exports/`` are out of scope
    #    for this taxonomy.
    exports_folder = job_folder(job_id) / "exports"
    if exports_folder.is_dir():
        for fmt in _KNOWN_EXPORT_FORMATS:
            if fmt in formats_seen:
                continue
            path = export_path_for(job_id, fmt)
            if path.exists():
                issues.append(_issue(
                    ISSUE_ORPHAN_EXPORT_FILE,
                    (
                        f"file {path.name!r} is on disk but the "
                        "manifest has no matching entry"
                    ),
                    format=fmt,
                    filename=path.name,
                ))

    # Stable sort so the response order does not depend on filesystem
    # enumeration order. Severity-first, then code, then format.
    _severity_rank: dict[str, int] = {
        SEVERITY_ERROR: 0,
        SEVERITY_WARNING: 1,
        SEVERITY_INFO: 2,
    }
    issues.sort(
        key=lambda i: (
            _severity_rank.get(i.severity, 99),
            i.code,
            i.format,
            i.filename,
            i.message,
        )
    )
    return issues


# ---------------------------------------------------------------------------
# Reconciliation — dry-run only signature
# ---------------------------------------------------------------------------


def reconcile_export_manifest(
    job_id: str, *, dry_run: bool = True,
) -> dict[str, Any]:
    """Report the actions a write-mode reconciler would take.

    Step 37 ships ``dry_run`` only. Passing ``dry_run=False`` raises
    :class:`NotImplementedError` so a future caller cannot accidentally
    trigger a write-mode reconciliation via this signature.

    Returns a dict ``{"dry_run": True, "actions": [str, ...]}`` listing
    the actions that would be taken (e.g. "would remove orphan entry
    for format=pdf"). Read-only — never mutates the manifest or any
    file on disk.
    """
    if not dry_run:
        raise NotImplementedError(
            "write-mode reconciliation is not implemented in Step 37"
        )

    actions: list[str] = []
    for issue in validate_export_integrity(job_id):
        if issue.code == ISSUE_ORPHAN_EXPORT_FILE:
            actions.append(
                f"would adopt or remove orphan file {issue.filename!r} "
                f"(format={issue.format!r})"
            )
        elif issue.code == ISSUE_MISSING_EXPORT_FILE:
            actions.append(
                f"would remove dangling manifest entry for "
                f"format={issue.format!r}"
            )
        elif issue.code in (
            ISSUE_MANIFEST_SHA_MISMATCH,
            ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH,
        ):
            actions.append(
                f"would refresh manifest entry hashes for "
                f"format={issue.format!r}"
            )
        # Other issue codes (drift, unknown format, unsupported
        # version, missing field) require operator decisions and do
        # not have a defined reconciliation action.

    return {"dry_run": True, "actions": actions}


__all__ = [
    "IntegrityIssue",
    "ISSUE_MISSING_EXPORT_FILE",
    "ISSUE_ORPHAN_EXPORT_FILE",
    "ISSUE_MANIFEST_SHA_MISMATCH",
    "ISSUE_MANIFEST_BYTE_LENGTH_MISMATCH",
    "ISSUE_SOURCE_MARKDOWN_DRIFT",
    "ISSUE_SOURCE_ENTRY_DRIFT",
    "ISSUE_MARKDOWN_MISSING",
    "ISSUE_UNKNOWN_EXPORT_FORMAT",
    "ISSUE_MANIFEST_ENTRY_MISSING_FIELD",
    "ISSUE_UNSUPPORTED_EXPORT_VERSION",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SEVERITY_INFO",
    "compute_export_lifecycle",
    "compute_manifest_lifecycle",
    "detect_stale_exports",
    "validate_export_integrity",
    "reconcile_export_manifest",
]
