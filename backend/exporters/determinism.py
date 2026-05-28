"""Pure determinism helpers for the export layer (Step 35 scaffold).

These helpers are the small primitives every renderer will share to
make its output byte-equal across re-runs. They are deliberately
pure functions over ``bytes``/``str`` — no I/O, no clocks, no
third-party imports beyond the stdlib — so they can be tested in
isolation and audited at a glance.

Helpers
-------

  - ``FIXED_EPOCH``      — UTC sentinel used for every "created at"
                           timestamp inside ZIP/OOXML containers.
  - ``FIXED_ZIP_MTIME``  — ZIP filesystem epoch (1980-01-01) used as
                           every member's modified-time field.
  - ``PRODUCER_STRING``  — the fixed PDF ``/Producer`` and
                           ``/Creator`` value. Pin the brand here
                           rather than at every renderer.
  - ``canonicalize_zip`` — re-archive a ZIP buffer with sorted member
                           order, fixed mtime, and ``ZIP_DEFLATED`` so
                           DOCX/XLSX are byte-deterministic.
  - ``sha256_hex``       — sha256 of a byte buffer as a 64-char hex
                           string. Mirrors the manifest convention.

Step 35 ships and tests these helpers without consuming them — the
renderers that will use them (``pdf.py``, ``docx.py``) land in
Steps 36 and 37 respectively.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Time sentinels
# ---------------------------------------------------------------------------
#
# ``FIXED_EPOCH`` is the canonical "fixed past datetime" written into
# every OOXML ``core_properties.created``/``modified`` field. We pick
# 2020-01-01T00:00:00Z — old enough to be obviously not "now", new
# enough that a freshly-generated DOCX does not look broken to a
# human reader.
FIXED_EPOCH: datetime = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# ZIP's internal filesystem stores modified-times as DOS date/time
# fields starting at 1980-01-01. Using this as every member's mtime
# guarantees ``zipfile`` writes the same DOS-time bytes on every run.
# The 6-tuple form is ``(year, month, day, hour, minute, second)``.
FIXED_ZIP_MTIME: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# Brand strings
# ---------------------------------------------------------------------------
#
# Pinned here (rather than in each renderer) so a brand-name change
# is a single-line edit and so the determinism contract is unaffected
# by what tagline we ship. PDF's ``/Producer`` and ``/Creator``
# metadata fields default to "WeasyPrint x.y.z" — we override both
# with this fixed string before writing bytes to disk.
PRODUCER_STRING: str = "ANS Prospect Tool"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_hex(buffer: bytes) -> str:
    """Return the SHA-256 of ``buffer`` as a 64-char hex string.

    Pinned here so renderers and the storage layer compute hashes
    the same way the assembler already does in
    ``backend/assembly/markdown.py`` (``hashlib.sha256(...).hexdigest()``).
    """
    return hashlib.sha256(buffer).hexdigest()


def canonicalize_zip(zip_bytes: bytes) -> bytes:
    """Re-archive ``zip_bytes`` with sorted member order and fixed mtime.

    DOCX and XLSX are ZIP-of-XML containers. Two byte-identical
    OOXML payloads can still produce different ZIP bytes if the
    underlying writer chooses different member order, different
    compression levels, or different mtime values. This helper
    eliminates all three sources of drift:

      - member order: sorted by name (lexicographic)
      - mtime: every member gets ``FIXED_ZIP_MTIME`` (1980-01-01)
      - compression: ``ZIP_DEFLATED`` at level 6 (Python's default
        and ``zipfile``'s stable choice)

    The helper does NOT inspect or mutate member contents — XML
    canonicalisation belongs to whichever renderer is producing the
    parts. This is the outer-container step only.
    """
    in_buf = io.BytesIO(zip_bytes)
    out_buf = io.BytesIO()

    with zipfile.ZipFile(in_buf, mode="r") as src:
        # Snapshot then sort by name so the iteration order is
        # deterministic regardless of how ``src`` enumerates members.
        members = sorted(src.infolist(), key=lambda zi: zi.filename)
        with zipfile.ZipFile(
            out_buf,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as dst:
            for member in members:
                data = src.read(member.filename)
                # Build a fresh ``ZipInfo`` so we drop any mtime,
                # external_attr, or extra-field state the source
                # member carried. We re-set only the fields we need.
                new_info = zipfile.ZipInfo(
                    filename=member.filename,
                    date_time=FIXED_ZIP_MTIME,
                )
                new_info.compress_type = zipfile.ZIP_DEFLATED
                # Preserve the executable/directory bit pattern from
                # the source — OOXML containers rely on the default
                # file-mode bits, so we do not touch ``external_attr``
                # here.
                dst.writestr(new_info, data)

    return out_buf.getvalue()


def strip_pdf_dates(pdf_bytes: bytes) -> bytes:
    """Placeholder for the PDF date-stripping pass.

    The real implementation lands in Step 36 with the PDF renderer
    (it will use ``pikepdf`` to remove ``/CreationDate``, ``/ModDate``
    and rewrite ``/Producer`` and ``/Creator`` to ``PRODUCER_STRING``).

    Step 35 ships only the stub so the surface is visible — calling
    it raises ``NotImplementedError`` to make accidental wiring loud.
    """
    raise NotImplementedError(
        "strip_pdf_dates is reserved for Step 36's PDF renderer; "
        "Step 35 ships only the surface so the renderer wiring lands "
        "atomically with its tests."
    )


__all__ = [
    "FIXED_EPOCH",
    "FIXED_ZIP_MTIME",
    "PRODUCER_STRING",
    "sha256_hex",
    "canonicalize_zip",
    "strip_pdf_dates",
]
