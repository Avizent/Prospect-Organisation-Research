"""Step 38 amendment 1 — the pinned ``base.docx`` template is never mutated.

``backend/exporters/templates/base.docx`` is a shipped binary that
seeds every DOCX render. The renderer MUST load it through a
read-only path (``read_bytes`` → ``io.BytesIO``) so that the on-disk
file is never reopened with a writable handle. This test mechanically
enforces the invariant by hashing the file before and after a
:func:`render_docx` call.

Why an explicit invariant
-------------------------

python-docx accepts both ``Path``/``str`` and file-like objects via
:class:`docx.Document` — and the path-accepting branches happily
write back to the path under some library versions. A naive renderer
that handed ``Document`` the template path would silently mutate the
template on every render, breaking the
:data:`backend.exporters.docx.TEMPLATE_SHA256` constant the manifest
viewer renders.

The amendment is reviewed at PR time: any change to the renderer
that hashes the template once at start and again at end must produce
identical hashes — full stop.
"""

from __future__ import annotations

import hashlib
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TEMPLATE_PATH = (
    _REPO_ROOT / "backend" / "exporters" / "templates" / "base.docx"
)


def _hash_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_base_docx_template_is_never_modified() -> None:
    """Render a DOCX and confirm the template SHA-256 is unchanged.

    We hash the file on disk before any import, render once, and
    re-hash. A renderer that opens the template writably (or that
    saves back to the path) would produce a different hash on the
    second read.
    """
    from datetime import datetime, timezone

    before = _hash_file(_TEMPLATE_PATH)

    from backend.exporters.docx import render_docx

    ans_logo = (
        _REPO_ROOT / "ans_knowledge" / "brand" / "ans_logo.png"
    ).read_bytes()
    md = "# Acme Ltd\n\nBody.\n"
    render_docx(
        job_id="00000000-0000-4000-8000-000000000000",
        markdown_text=md,
        markdown_sha256=hashlib.sha256(md.encode("utf-8")).hexdigest(),
        company_name="Acme Ltd",
        ans_logo_bytes=ans_logo,
        prospect_logo_bytes=b"",
        now=datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc),
    )

    after = _hash_file(_TEMPLATE_PATH)

    assert before == after, (
        "base.docx template was mutated during render_docx — "
        "the renderer must load it via read_bytes + BytesIO and "
        "never reopen the file writably"
    )


def test_template_sha256_constant_matches_on_disk_file() -> None:
    """The module-level constant must equal the on-disk SHA-256.

    A drift here means either the constant got out of sync with the
    template file (e.g. someone re-saved the binary) or the renderer
    is computing the SHA over a different buffer than the file's
    actual contents.
    """
    from backend.exporters.docx import TEMPLATE_SHA256

    assert TEMPLATE_SHA256 == _hash_file(_TEMPLATE_PATH)
