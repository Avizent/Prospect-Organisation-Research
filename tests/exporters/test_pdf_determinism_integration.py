"""Step 36 end-to-end byte-equal determinism.

The unit-level renderer test (``test_pdf_renderer.py``) already pins
byte-equality across two calls into :func:`render_pdf`. This file
adds a slightly broader integration assertion: render → strip →
result, twice, with the same inputs, must produce identical bytes
*and* identical sha256. We assert it twice with two different
markdown bodies so a future change that accidentally drops the
``id_seed`` (re-introducing clock-derived ``/ID``) would surface here
as a difference between the two seeds AND a mismatch within a single
seed across runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANS_LOGO_PATH = _REPO_ROOT / "ans_knowledge" / "brand" / "ans_logo.png"


def _kwargs(markdown_text: str) -> dict:
    return {
        "job_id": "00000000-0000-4000-8000-000000000000",
        "markdown_text": markdown_text,
        "markdown_sha256": hashlib.sha256(
            markdown_text.encode("utf-8")
        ).hexdigest(),
        "company_name": "Acme Ltd",
        "ans_logo_bytes": _ANS_LOGO_PATH.read_bytes(),
        "prospect_logo_bytes": b"",
        "now": datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc),
    }


def test_render_pdf_is_byte_equal_across_two_renders_short_body() -> None:
    from backend.exporters.pdf import render_pdf

    md = "# Acme\n\nShort body.\n"
    a = render_pdf(**_kwargs(md))
    b = render_pdf(**_kwargs(md))
    assert a.bytes == b.bytes
    assert a.sha256 == b.sha256


def test_render_pdf_is_byte_equal_across_two_renders_long_body() -> None:
    from backend.exporters.pdf import render_pdf

    md = "# Acme\n\n" + ("Long paragraph with content. " * 200) + "\n"
    a = render_pdf(**_kwargs(md))
    b = render_pdf(**_kwargs(md))
    assert a.bytes == b.bytes
    assert a.sha256 == b.sha256


def test_render_pdf_differs_when_markdown_differs() -> None:
    """Sanity check the determinism is content-driven, not vacuous:
    two different markdown bodies must produce different bytes."""
    from backend.exporters.pdf import render_pdf

    a = render_pdf(**_kwargs("# Acme\n\nBody A.\n"))
    b = render_pdf(**_kwargs("# Acme\n\nBody B (different).\n"))
    assert a.bytes != b.bytes
    assert a.sha256 != b.sha256
