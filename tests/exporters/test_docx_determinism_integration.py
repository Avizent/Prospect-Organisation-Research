"""Step 38 end-to-end byte-equal determinism (DOCX).

Mirrors :mod:`tests.exporters.test_pdf_determinism_integration` —
render → scrub → result, twice, with the same inputs, must produce
identical bytes *and* identical sha256. Asserted twice with two
different markdown bodies so a future change that drops the scrubbing
step (re-introducing rsids or wall-clock metadata) surfaces here.
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


def test_render_docx_is_byte_equal_across_two_renders_short_body() -> None:
    from backend.exporters.docx import render_docx

    md = "# Acme\n\nShort body.\n"
    a = render_docx(**_kwargs(md))
    b = render_docx(**_kwargs(md))
    assert a.bytes == b.bytes
    assert a.sha256 == b.sha256


def test_render_docx_is_byte_equal_across_two_renders_long_body() -> None:
    from backend.exporters.docx import render_docx

    md = "# Acme\n\n" + ("Long paragraph with content. " * 200) + "\n"
    a = render_docx(**_kwargs(md))
    b = render_docx(**_kwargs(md))
    assert a.bytes == b.bytes
    assert a.sha256 == b.sha256


def test_render_docx_differs_when_markdown_differs() -> None:
    from backend.exporters.docx import render_docx

    a = render_docx(**_kwargs("# Acme\n\nBody A.\n"))
    b = render_docx(**_kwargs("# Acme\n\nBody B (different).\n"))
    assert a.bytes != b.bytes
    assert a.sha256 != b.sha256
