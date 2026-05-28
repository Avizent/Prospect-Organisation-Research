"""Tests for :func:`backend.exporters.determinism.scrub_docx_metadata`.

The scrubber is the docx analogue of :func:`strip_pdf_dates`. We pin:

  * ``dcterms:created`` / ``dcterms:modified`` are replaced with the
    pinned ISO sentinel.
  * ``Application`` / ``AppVersion`` in ``docProps/app.xml`` carry the
    pinned brand string + version.
  * ``<w:rsids>`` block and ``w:rsid*`` attributes are stripped.
  * ZIP entry order is sorted lexicographically.
  * Per-entry mtime equals ``FIXED_ZIP_MTIME`` (1980-01-01).
  * Two scrub calls over the same input produce byte-equal output
    (idempotence + determinism).
"""

from __future__ import annotations

import io
import zipfile

import pytest

docx = pytest.importorskip("docx")


def _build_docx_with_dirty_metadata() -> bytes:
    """Save a python-docx document to bytes — it will carry rsids,
    a fresh ``cp:revision``, and a wall-clock ``dcterms:created``."""
    document = docx.Document()
    document.add_paragraph("hello")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# core.xml scrub
# ---------------------------------------------------------------------------


def test_scrub_pins_dcterms_created_and_modified() -> None:
    from backend.exporters.determinism import scrub_docx_metadata

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        core = zf.read("docProps/core.xml").decode("utf-8")
    assert "2020-01-01T00:00:00Z" in core


def test_scrub_pins_dc_creator_to_producer_string() -> None:
    from backend.exporters.determinism import (
        PRODUCER_STRING,
        scrub_docx_metadata,
    )

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        core = zf.read("docProps/core.xml").decode("utf-8")
    assert f"<dc:creator>{PRODUCER_STRING}</dc:creator>" in core


# ---------------------------------------------------------------------------
# app.xml scrub
# ---------------------------------------------------------------------------


def test_scrub_pins_application_and_appversion() -> None:
    from backend.exporters.determinism import (
        PRODUCER_STRING,
        scrub_docx_metadata,
    )

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        # docProps/app.xml is optional in a minimal python-docx output;
        # only assert when it is present.
        if "docProps/app.xml" in zf.namelist():
            app = zf.read("docProps/app.xml").decode("utf-8")
            assert f"<Application>{PRODUCER_STRING}</Application>" in app
            assert "<AppVersion>1.0000</AppVersion>" in app


# ---------------------------------------------------------------------------
# rsid scrub
# ---------------------------------------------------------------------------


def test_scrub_strips_rsids_from_settings_xml() -> None:
    from backend.exporters.determinism import scrub_docx_metadata

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        settings = zf.read("word/settings.xml").decode("utf-8")
    assert "<w:rsids" not in settings
    assert "w:rsidRoot=" not in settings


def test_scrub_strips_rsid_attributes_from_document_xml() -> None:
    from backend.exporters.determinism import scrub_docx_metadata

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        doc = zf.read("word/document.xml").decode("utf-8")
    # No surviving ``w:rsid*="..."`` attributes anywhere.
    assert 'w:rsidR="' not in doc
    assert 'w:rsidRDefault="' not in doc


# ---------------------------------------------------------------------------
# ZIP layout
# ---------------------------------------------------------------------------


def test_scrub_sorts_zip_member_order_lexicographically() -> None:
    from backend.exporters.determinism import scrub_docx_metadata

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        names = zf.namelist()
    assert names == sorted(names)


def test_scrub_pins_fixed_zip_mtime() -> None:
    from backend.exporters.determinism import (
        FIXED_ZIP_MTIME,
        scrub_docx_metadata,
    )

    raw = _build_docx_with_dirty_metadata()
    out = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        for info in zf.infolist():
            assert info.date_time == FIXED_ZIP_MTIME


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_scrub_is_idempotent() -> None:
    """Scrubbing twice produces the same bytes as scrubbing once."""
    from backend.exporters.determinism import scrub_docx_metadata

    raw = _build_docx_with_dirty_metadata()
    once = scrub_docx_metadata(raw, markdown_sha256="a" * 64)
    twice = scrub_docx_metadata(once, markdown_sha256="a" * 64)
    assert once == twice
