"""Tests for :func:`backend.exporters.docx.render_docx` (Step 38).

Coverage:

  * Byte-equal determinism across two calls with identical inputs.
  * ``ExportResult.format == "docx"``.
  * INTERNAL DRAFT watermark appears in the document header part.
  * Missing brand asset is fatal (``ExportError("missing_brand_asset")``);
    missing prospect logo degrades to a warning.
  * The renderer never spawns a subprocess.
  * Amendment 1 invariant — see also
    :mod:`tests.exporters.test_base_docx_template_invariant`.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANS_LOGO_PATH = _REPO_ROOT / "ans_knowledge" / "brand" / "ans_logo.png"


def _ans_logo_bytes() -> bytes:
    return _ANS_LOGO_PATH.read_bytes()


def _sample_inputs() -> dict:
    md = "# Acme Ltd\n\nBrief body.\n\n## Section\n\nMore content.\n"
    return {
        "job_id": "00000000-0000-4000-8000-000000000000",
        "markdown_text": md,
        "markdown_sha256": hashlib.sha256(md.encode("utf-8")).hexdigest(),
        "company_name": "Acme Ltd",
        "ans_logo_bytes": _ans_logo_bytes(),
        "prospect_logo_bytes": b"",
        "now": datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# Byte-equal determinism
# ---------------------------------------------------------------------------


def test_render_docx_is_byte_equal_across_two_runs() -> None:
    from backend.exporters.docx import render_docx

    kwargs = _sample_inputs()
    a = render_docx(**kwargs)
    b = render_docx(**kwargs)
    assert a.bytes == b.bytes
    assert a.sha256 == b.sha256
    assert a.sha256 == hashlib.sha256(a.bytes).hexdigest()


def test_render_docx_returns_export_result_with_docx_format() -> None:
    from backend.exporters.docx import render_docx

    out = render_docx(**_sample_inputs())
    assert out.format == "docx"


def test_render_docx_emits_valid_zip() -> None:
    """The output must parse as a ZIP — DOCX is fundamentally a ZIP."""
    from backend.exporters.docx import render_docx

    out = render_docx(**_sample_inputs())
    with zipfile.ZipFile(io.BytesIO(out.bytes)) as zf:
        names = zf.namelist()
        assert "[Content_Types].xml" in names
        assert "word/document.xml" in names


# ---------------------------------------------------------------------------
# Watermark surface
# ---------------------------------------------------------------------------


def test_render_docx_carries_internal_draft_watermark() -> None:
    """The INTERNAL DRAFT watermark MUST land in a header part so it
    repeats on every page (CLAUDE.md hard rule #7)."""
    from backend.exporters.docx import render_docx

    out = render_docx(**_sample_inputs())
    with zipfile.ZipFile(io.BytesIO(out.bytes)) as zf:
        header_parts = [n for n in zf.namelist() if "header" in n.lower()]
        assert header_parts, (
            "expected at least one word/header*.xml part carrying "
            "the INTERNAL DRAFT watermark"
        )
        joined = b"".join(zf.read(n) for n in header_parts)
        assert b"INTERNAL DRAFT" in joined


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_render_docx_raises_missing_brand_asset_for_empty_ans_logo() -> None:
    from backend.exporters.base import ExportError
    from backend.exporters.docx import render_docx

    kwargs = _sample_inputs()
    kwargs["ans_logo_bytes"] = b""
    with pytest.raises(ExportError) as ei:
        render_docx(**kwargs)
    assert ei.value.reason == "missing_brand_asset"


def test_render_docx_warns_but_succeeds_when_prospect_logo_missing() -> None:
    from backend.exporters.docx import render_docx

    kwargs = _sample_inputs()
    kwargs["prospect_logo_bytes"] = b""
    result = render_docx(**kwargs)
    assert result.format == "docx"
    assert any("prospect logo" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# No subprocess
# ---------------------------------------------------------------------------


def test_render_docx_does_not_spawn_subprocess(monkeypatch) -> None:
    import subprocess

    from backend.exporters.docx import render_docx

    def _no_run(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("renderer must not call subprocess.run")

    def _no_popen(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("renderer must not call subprocess.Popen")

    monkeypatch.setattr(subprocess, "run", _no_run)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)

    render_docx(**_sample_inputs())


# ---------------------------------------------------------------------------
# Provenance projections
# ---------------------------------------------------------------------------


def test_renderer_provenance_returns_python_docx_name() -> None:
    from backend.exporters.docx import RENDERER_NAME, renderer_provenance

    assert RENDERER_NAME == "python-docx"
    prov = renderer_provenance()
    assert prov["name"] == "python-docx"
    assert prov["version"]


def test_markdown_renderer_provenance_matches_pdf_axis() -> None:
    """The DOCX and PDF renderers share the same Markdown engine, so
    the provenance dicts must agree."""
    from backend.exporters.docx import (
        markdown_renderer_provenance as docx_md_prov,
    )
    from backend.exporters.pdf import (
        markdown_renderer_provenance as pdf_md_prov,
    )

    assert docx_md_prov() == pdf_md_prov()


def test_template_sha256_is_pinned_and_64_hex() -> None:
    from backend.exporters.docx import TEMPLATE_SHA256, template_sha256

    assert isinstance(TEMPLATE_SHA256, str)
    assert len(TEMPLATE_SHA256) == 64
    int(TEMPLATE_SHA256, 16)  # parseable as hex
    assert template_sha256() == TEMPLATE_SHA256
