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


def strip_pdf_dates(pdf_bytes: bytes, *, id_seed: bytes | None = None) -> bytes:
    """Strip wall-clock metadata from a PDF so it is byte-reproducible.

    WeasyPrint stamps ``/CreationDate`` and ``/ModDate`` into the PDF
    info dictionary at render time, and also emits an XMP packet in
    ``/Root/Metadata`` carrying the same timestamps. Both move every
    re-render even when the source HTML/CSS are byte-identical. The
    third source of drift is the trailer ``/ID`` array, which PDF
    readers cache; WeasyPrint seeds it from a clock-derived UUID.

    This helper does four things — and only four:

      1. Delete ``/CreationDate`` and ``/ModDate`` from the info dict.
      2. Overwrite ``/Producer`` and ``/Creator`` with
         :data:`PRODUCER_STRING` (the pinned brand string).
      3. Delete the XMP metadata stream at ``/Root/Metadata`` if
         present — it duplicates what we just rewrote in the info
         dict and carries its own embedded timestamps.
      4. Save the PDF with pikepdf's ``deterministic_id=True`` flag so
         the trailer ``/ID`` array is derived from a SHA-256 of the
         document objects rather than from the system clock. When
         ``id_seed`` is supplied we additionally splice the first 16
         bytes of the seed into the *first* slot of the resulting
         ``/ID`` array (the "original file identifier"); the *second*
         slot stays as the content-hash value pikepdf computed, which
         keeps the second-pass write byte-stable across runs.

    The output is the re-saved PDF bytes. We pass
    ``linearize=False`` so pikepdf does not run a second writer pass
    that would re-introduce wall-clock noise via cross-reference
    timestamps.
    """
    # Local import — pikepdf is a heavy native dep we do not want to
    # pull in at package-import time. The static fence in
    # ``tests/exporters/test_no_forbidden_imports.py`` parametrises
    # the import scan, but ``pikepdf`` is not on the forbidden list.
    import pikepdf

    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        info = pdf.docinfo
        for key in ("/CreationDate", "/ModDate"):
            if key in info:
                del info[key]
        info["/Producer"] = PRODUCER_STRING
        info["/Creator"] = PRODUCER_STRING

        # The XMP packet is not part of /Info — it sits at
        # ``/Root/Metadata`` as a content stream. Drop it entirely;
        # we have no need for XMP and keeping it would re-introduce
        # the very wall-clock fields we just stripped above.
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]

        out = io.BytesIO()
        pdf.save(out, linearize=False, deterministic_id=True)
        saved = out.getvalue()

    if id_seed is None:
        return saved

    # Splice the markdown-derived seed into the first slot of the
    # trailer ``/ID`` array. pikepdf has already written a
    # content-derived value into both slots; we want the first slot
    # (the "permanent file identifier") to be derivable from the
    # source markdown so an operator can verify provenance without
    # parsing the whole PDF. The second slot stays as the content
    # hash, so two byte-identical PDFs still have byte-identical
    # /ID arrays.
    return _splice_id_seed(saved, id_seed[:16].ljust(16, b"\x00"))


def _splice_id_seed(pdf_bytes: bytes, seed: bytes) -> bytes:
    """Rewrite the first /ID slot in ``pdf_bytes`` to ``seed``.

    pikepdf's ``deterministic_id=True`` emits the trailer ID as
    ``/ID [<HHHH...><HHHH...>]`` where each ``HHHH`` is a 32-char
    hex string. We locate that pattern textually and replace the
    first hex string with ``seed.hex().upper()``. The /ID array is
    the only trailer field whose syntactic shape is fixed enough to
    splice safely by regex — the rest of the file is left untouched.
    """
    import re

    # ``/ID [<aabbcc...><ddeeff...>]`` with optional whitespace.
    pattern = re.compile(
        rb"/ID\s*\[\s*<([0-9A-Fa-f]{32})>\s*<([0-9A-Fa-f]{32})>\s*\]"
    )
    new_first = seed.hex().upper().encode("ascii")

    def _replace(m: "re.Match[bytes]") -> bytes:
        return b"/ID [<" + new_first + b"><" + m.group(2) + b">]"

    spliced, count = pattern.subn(_replace, pdf_bytes, count=1)
    if count != 1:
        # Defensive: if the layout shifts in a future pikepdf release
        # we surface the failure rather than silently emitting an
        # unseeded /ID.
        raise RuntimeError(
            "could not splice /ID seed — trailer /ID layout changed"
        )
    return spliced


__all__ = [
    "FIXED_EPOCH",
    "FIXED_ZIP_MTIME",
    "PRODUCER_STRING",
    "sha256_hex",
    "canonicalize_zip",
    "strip_pdf_dates",
]
