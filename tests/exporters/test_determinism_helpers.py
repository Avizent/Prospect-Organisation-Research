"""Pure-function tests for the Step 35 determinism helpers.

These helpers live in ``backend.exporters.determinism`` and are the
shared primitives every renderer will use to keep its output
byte-equal across re-runs. They are deliberately pure (no I/O, no
clocks, no third-party deps) so the contract is testable in
isolation here, before any renderer exists.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_fixed_epoch_is_utc_2020_01_01() -> None:
    from backend.exporters.determinism import FIXED_EPOCH

    assert isinstance(FIXED_EPOCH, datetime)
    assert FIXED_EPOCH == datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert FIXED_EPOCH.tzinfo is timezone.utc


def test_fixed_zip_mtime_is_1980_01_01_six_tuple() -> None:
    from backend.exporters.determinism import FIXED_ZIP_MTIME

    # ZIP filesystem epoch starts at 1980-01-01. Anything earlier
    # would underflow the DOS-time field.
    assert FIXED_ZIP_MTIME == (1980, 1, 1, 0, 0, 0)


def test_producer_string_is_pinned_and_brand_agnostic_to_version() -> None:
    from backend.exporters.determinism import PRODUCER_STRING

    assert PRODUCER_STRING == "ANS Prospect Tool"
    # The producer string must NOT carry a renderer version, otherwise
    # a silent dependency bump would change the byte output.
    assert "weasyprint" not in PRODUCER_STRING.lower()
    assert "python-docx" not in PRODUCER_STRING.lower()


# ---------------------------------------------------------------------------
# sha256_hex
# ---------------------------------------------------------------------------


def test_sha256_hex_matches_stdlib_for_empty_input() -> None:
    from backend.exporters.determinism import sha256_hex

    assert sha256_hex(b"") == hashlib.sha256(b"").hexdigest()


def test_sha256_hex_matches_stdlib_for_arbitrary_bytes() -> None:
    from backend.exporters.determinism import sha256_hex

    payload = b"the canonical markdown body, with newlines\n"
    assert sha256_hex(payload) == hashlib.sha256(payload).hexdigest()


def test_sha256_hex_returns_64_character_lowercase_hex() -> None:
    from backend.exporters.determinism import sha256_hex

    out = sha256_hex(b"x")
    assert len(out) == 64
    assert out == out.lower()
    assert all(c in "0123456789abcdef" for c in out)


# ---------------------------------------------------------------------------
# canonicalize_zip
# ---------------------------------------------------------------------------


def _make_zip(members: list[tuple[str, bytes]]) -> bytes:
    """Helper: build a ZIP from ``(name, content)`` pairs in order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)
    return buf.getvalue()


def test_canonicalize_zip_is_deterministic_for_identical_input() -> None:
    """Two canonicalisation passes over the same input must produce
    byte-identical output. This is the determinism contract every
    renderer leans on."""
    from backend.exporters.determinism import canonicalize_zip

    src = _make_zip([("a.txt", b"alpha"), ("b.txt", b"beta")])
    out1 = canonicalize_zip(src)
    out2 = canonicalize_zip(src)
    assert out1 == out2


def test_canonicalize_zip_sorts_members_by_name() -> None:
    """Member order in the input must not affect the output. We build
    the same set of (name, content) pairs in two orders and assert
    the canonicalised bytes are identical."""
    from backend.exporters.determinism import canonicalize_zip

    a_first = _make_zip([("a.txt", b"alpha"), ("z.txt", b"zeta")])
    z_first = _make_zip([("z.txt", b"zeta"), ("a.txt", b"alpha")])
    assert canonicalize_zip(a_first) == canonicalize_zip(z_first)


def test_canonicalize_zip_pins_member_mtimes_to_fixed_epoch() -> None:
    """Every member's ``date_time`` must be 1980-01-01 after the pass."""
    from backend.exporters.determinism import canonicalize_zip, FIXED_ZIP_MTIME

    src = _make_zip([("only.txt", b"payload")])
    out = canonicalize_zip(src)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        for info in zf.infolist():
            assert info.date_time == FIXED_ZIP_MTIME, (
                f"member {info.filename!r} carries non-canonical mtime "
                f"{info.date_time!r}"
            )


def test_canonicalize_zip_preserves_member_payloads() -> None:
    """Canonicalisation MUST NOT mutate inner bytes — only the
    container metadata changes."""
    from backend.exporters.determinism import canonicalize_zip

    src = _make_zip([
        ("doc.xml", b"<root><child/></root>"),
        ("rels.xml", b"<Relationships/>"),
    ])
    out = canonicalize_zip(src)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        assert zf.read("doc.xml") == b"<root><child/></root>"
        assert zf.read("rels.xml") == b"<Relationships/>"


def test_canonicalize_zip_uses_deflate_compression() -> None:
    from backend.exporters.determinism import canonicalize_zip

    src = _make_zip([("x.txt", b"y" * 4096)])
    out = canonicalize_zip(src)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        for info in zf.infolist():
            assert info.compress_type == zipfile.ZIP_DEFLATED


# ---------------------------------------------------------------------------
# strip_pdf_dates — landed in Step 36 alongside the PDF renderer.
# Smoke-test it here against a tiny WeasyPrint PDF so the helper's
# contract (no /CreationDate, no /ModDate, fixed /Producer, no /Metadata
# stream) is enforced even if the renderer file is deleted.
# ---------------------------------------------------------------------------


def _tiny_pdf_bytes() -> bytes:
    """Build the smallest reasonable PDF for the determinism smoke test.

    We use WeasyPrint to produce one because constructing a valid PDF
    by hand is awkward, and the helper only operates on real PDFs.
    """
    import weasyprint  # noqa: WPS433 — heavy native dep, lazy import.
    return weasyprint.HTML(string="<html><body>hi</body></html>").write_pdf()


def test_strip_pdf_dates_removes_creation_and_mod_date() -> None:
    import pikepdf

    from backend.exporters.determinism import strip_pdf_dates

    raw = _tiny_pdf_bytes()
    stripped = strip_pdf_dates(raw, id_seed=b"\x01" * 16)
    with pikepdf.open(io.BytesIO(stripped)) as pdf:
        info = pdf.docinfo
        assert "/CreationDate" not in info
        assert "/ModDate" not in info


def test_strip_pdf_dates_pins_producer_and_creator() -> None:
    import pikepdf

    from backend.exporters.determinism import PRODUCER_STRING, strip_pdf_dates

    stripped = strip_pdf_dates(_tiny_pdf_bytes(), id_seed=b"\x02" * 16)
    with pikepdf.open(io.BytesIO(stripped)) as pdf:
        assert str(pdf.docinfo["/Producer"]) == PRODUCER_STRING
        assert str(pdf.docinfo["/Creator"]) == PRODUCER_STRING


def test_strip_pdf_dates_drops_xmp_metadata_stream() -> None:
    import pikepdf

    from backend.exporters.determinism import strip_pdf_dates

    stripped = strip_pdf_dates(_tiny_pdf_bytes(), id_seed=b"\x03" * 16)
    with pikepdf.open(io.BytesIO(stripped)) as pdf:
        assert "/Metadata" not in pdf.Root


def test_strip_pdf_dates_is_deterministic_across_runs() -> None:
    """Two calls with the same input + same ``id_seed`` produce
    byte-equal output. This is the renderer's central guarantee."""
    from backend.exporters.determinism import strip_pdf_dates

    raw = _tiny_pdf_bytes()
    seed = b"\xaa" * 16
    out_a = strip_pdf_dates(raw, id_seed=seed)
    out_b = strip_pdf_dates(raw, id_seed=seed)
    assert out_a == out_b
