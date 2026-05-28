"""Deterministic DOCX renderer (Step 38).

Turns the canonical ``prospect_brief.md`` into a byte-reproducible
``.docx`` file. The renderer is a pure function:

  - Inputs: Markdown text + the markdown SHA-256 the caller already
    knows + a company name + the two logo byte blobs + an optional
    fixed-time sentinel (only used inside the template, never
    embedded in the docx metadata).
  - Output: an :class:`backend.exporters.base.ExportResult` with the
    final docx bytes, their SHA-256, and any warnings.

No I/O happens here other than the read-only load of the pinned
``base.docx`` template — the caller (the route layer) writes the
bytes to disk atomically. Keeping the renderer otherwise I/O-free
makes determinism testable in pure unit tests and keeps the storage
layout decoupled from the rendering pipeline.

Pipeline
--------

  1. Validate brand assets (mirrors PDF — ANS logo bytes are
     mandatory; prospect logo bytes are optional with a warning).
  2. Load ``backend/exporters/templates/base.docx`` into memory and
     hand a :class:`io.BytesIO` to python-docx so the on-disk
     template is NEVER opened with a writable handle (this is the
     Step-38 amendment-1 invariant).
  3. Apply the brand-chrome header watermark + body brand block.
  4. Walk the Markdown via :mod:`backend.exporters.markdown_to_docx`
     and append paragraphs/tables onto the document in order.
  5. Save the document to an in-memory buffer via
     :meth:`docx.document.Document.save`.
  6. Pass the buffer through
     :func:`backend.exporters.determinism.scrub_docx_metadata` to
     strip clock-derived metadata, pin Application/AppVersion, drop
     ``w:rsid*`` attributes and the ``<w:rsids>`` block, and re-zip
     with sorted member order + ``FIXED_ZIP_MTIME``.

Determinism contract
--------------------

Two runs of :func:`render_docx` with identical inputs MUST produce
byte-equal output. Sources of drift the renderer rules out:

  - ``dcterms:created`` / ``dcterms:modified`` (scrubbed)
  - ``cp:revision`` / ``dc:creator`` / ``cp:lastModifiedBy`` (scrubbed)
  - ``Application`` / ``AppVersion`` / telemetry tags in app.xml
  - ``w:rsid*`` revision-save IDs across document/settings/styles/
    header/footer parts
  - ZIP entry order + per-entry mtime
  - Image-relationship rIds (deterministic given fixed add-order)

Template invariant (amendment 1)
--------------------------------

``backend/exporters/templates/base.docx`` is a shipped, pinned
binary. The renderer MUST NOT write back to it. We enforce this
mechanically by reading the file once into a ``bytes`` buffer at
each render call and handing a :class:`io.BytesIO` to python-docx.
A unit test in ``tests/exporters/test_base_docx_template_invariant``
hashes the template before and after a render to prove it.

Provenance (amendment 2)
------------------------

The DOCX export entry on the manifest carries a fourth provenance
field beyond the PDF entry's three (``exporter_version``,
``template_version``, ``renderer.{name,version}``):

  - ``template_sha256`` — SHA-256 of the loaded ``base.docx`` bytes.
    A change to the template file is therefore visible on the next
    render even when no other provenance axis has moved.

This is exposed at the module level as :data:`TEMPLATE_SHA256` and
via :func:`template_sha256` for the route layer to embed in the
``exports[]`` entry.

Failure surface
---------------

``ExportError`` is raised with one of the five reasons documented
in :mod:`backend.exporters.base`:

  - ``missing_brand_asset``    — ANS logo bytes are empty.
  - ``missing_logo``           — reserved (the renderer degrades
                                 gracefully if the prospect logo is
                                 missing).
  - ``template_render_failed`` — python-docx raised while applying
                                 the brand-chrome header or body
                                 block.
  - ``renderer_failed``        — python-docx raised during the final
                                 save, or the scrubber raised, or
                                 the markdown walker raised.
  - ``markdown_drift``         — reserved for the route layer.
"""

from __future__ import annotations

import io
import pathlib
from dataclasses import dataclass
from datetime import datetime

from backend.exporters import EXPORTER_VERSION, TEMPLATE_VERSION
from backend.exporters.base import ExportError, ExportResult
from backend.exporters.determinism import scrub_docx_metadata, sha256_hex
from backend.exporters.markdown_to_docx import (
    MARKDOWN_RENDERER_NAME,
    MARKDOWN_RENDERER_VERSION,
    render_markdown_into,
)


# ---------------------------------------------------------------------------
# Renderer provenance — read live so a python-docx upgrade surfaces in the
# manifest's ``exports[].renderer.version`` field on the next render.
# ---------------------------------------------------------------------------

RENDERER_NAME: str = "python-docx"


def _renderer_version() -> str:
    """Return the python-docx package version string.

    Imported lazily so this module's import-time cost stays light;
    the static fence in ``tests/exporters/test_no_forbidden_imports``
    parametrises the scan but ``docx`` (the import name of
    python-docx) is not on the forbidden list.
    """
    import docx as _docx  # noqa: WPS433 — lazy import
    return _docx.__version__


# ---------------------------------------------------------------------------
# Template loader — pinned to this package's templates/ directory so a
# future repo-relative path change does not silently resolve a different
# file.
# ---------------------------------------------------------------------------

_TEMPLATES_DIR: pathlib.Path = (
    pathlib.Path(__file__).resolve().parent / "templates"
)
_BASE_TEMPLATE_PATH: pathlib.Path = _TEMPLATES_DIR / "base.docx"


def _read_template_bytes() -> bytes:
    """Return the on-disk bytes of ``base.docx`` (read-only).

    Reading via ``read_bytes`` guarantees we never hold a writable
    handle to the template file — the bytes are copied into memory
    in one shot and the file is closed before we ever touch
    python-docx. This is the mechanical enforcement of the
    "template never mutated" invariant.
    """
    return _BASE_TEMPLATE_PATH.read_bytes()


# Module-level SHA-256 of the pinned template. Computed once at import
# so the route layer can read the value cheaply, and so a CI-time
# drift in the template surface (someone re-saving the file by
# accident) shows up at import-time too.
TEMPLATE_SHA256: str = sha256_hex(_read_template_bytes())


# ---------------------------------------------------------------------------
# Public render entry-point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RenderInputs:
    """Internal collapse of the public render kwargs — lets the
    pipeline pass a single value object between helpers instead of
    seven positional kwargs."""

    job_id: str
    markdown_text: str
    markdown_sha256: str
    company_name: str
    ans_logo_bytes: bytes
    prospect_logo_bytes: bytes
    now: datetime


def render_docx(
    *,
    job_id: str,
    markdown_text: str,
    markdown_sha256: str,
    company_name: str,
    ans_logo_bytes: bytes,
    prospect_logo_bytes: bytes,
    now: datetime,
) -> ExportResult:
    """Render ``markdown_text`` into a deterministic DOCX.

    See module docstring for the full pipeline and determinism
    contract. The function is I/O-free apart from a read-only load
    of the pinned ``base.docx`` template; the caller writes the
    returned bytes to disk.
    """
    inputs = _RenderInputs(
        job_id=job_id,
        markdown_text=markdown_text,
        markdown_sha256=markdown_sha256,
        company_name=company_name,
        ans_logo_bytes=ans_logo_bytes,
        prospect_logo_bytes=prospect_logo_bytes,
        now=now,
    )

    warnings: list[str] = []

    # 1. Validate brand assets — mirrors PDF.
    if not inputs.ans_logo_bytes:
        raise ExportError(
            "missing_brand_asset",
            detail="ANS logo bytes are empty; cannot render without brand",
        )
    if not inputs.prospect_logo_bytes:
        warnings.append(
            "prospect logo missing — rendered without co-brand"
        )

    # 2. Load the template via BytesIO. ``_read_template_bytes`` returns
    # a fresh copy on every call, so concurrent renders cannot share a
    # mutable buffer and the on-disk file is never opened writably.
    import docx as _docx  # noqa: WPS433 — lazy import (heavy)
    try:
        document = _docx.Document(io.BytesIO(_read_template_bytes()))
    except Exception as exc:  # noqa: BLE001 — python-docx raises many shapes
        raise ExportError(
            "template_render_failed",
            detail=f"python-docx (template load): {exc}",
        ) from exc

    # 3. Brand chrome — page header watermark, body brand block.
    try:
        _apply_brand_chrome(
            document,
            company_name=inputs.company_name,
            ans_logo_bytes=inputs.ans_logo_bytes,
            prospect_logo_bytes=inputs.prospect_logo_bytes,
        )
    except Exception as exc:  # noqa: BLE001 — python-docx raises many shapes
        raise ExportError(
            "template_render_failed",
            detail=f"python-docx (brand chrome): {exc}",
        ) from exc

    # 4. Walk the Markdown into the document.
    try:
        render_markdown_into(document, inputs.markdown_text)
    except Exception as exc:  # noqa: BLE001
        raise ExportError(
            "renderer_failed",
            detail=f"markdown_to_docx: {exc}",
        ) from exc

    # 5. Footer paragraph — mirrors the PDF template's footer line.
    footer = document.add_paragraph()
    footer.add_run("INTERNAL DRAFT — not for external distribution.")

    # 6. Serialise to bytes.
    try:
        buf = io.BytesIO()
        document.save(buf)
        raw_docx = buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise ExportError(
            "renderer_failed",
            detail=f"python-docx (save): {exc}",
        ) from exc

    # 7. Scrub clock-derived metadata + canonicalise the ZIP.
    try:
        final_docx = scrub_docx_metadata(
            raw_docx, markdown_sha256=inputs.markdown_sha256,
        )
    except Exception as exc:  # noqa: BLE001
        raise ExportError(
            "renderer_failed",
            detail=f"scrub_docx_metadata: {exc}",
        ) from exc

    return ExportResult(
        format="docx",
        bytes=final_docx,
        sha256=sha256_hex(final_docx),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _apply_brand_chrome(
    document,  # type: ignore[no-untyped-def]
    *,
    company_name: str,
    ans_logo_bytes: bytes,
    prospect_logo_bytes: bytes,
) -> None:
    """Apply the page-header watermark and body brand block.

    The page header carries the INTERNAL DRAFT watermark so the
    watermark renders on every page (CLAUDE.md hard rule #7 — "All
    generated documents watermarked as INTERNAL DRAFT"). The body
    brand block sits at the top of the document and carries:

      * the ANS logo (always present — validated upstream),
      * the prospect logo (when supplied),
      * the company name as a Heading 1,
      * the "Prospect Brief" subtitle.

    Image relationships are added in a fixed order so the resulting
    ``rId`` sequence is deterministic across runs.
    """
    # Page-header watermark — repeats on every page.
    section = document.sections[0]
    header = section.header
    if header.paragraphs:
        header_paragraph = header.paragraphs[0]
    else:
        header_paragraph = header.add_paragraph()
    # Wipe any inherited text then add the watermark run.
    for run in list(header_paragraph.runs):
        run.text = ""
    watermark_run = header_paragraph.add_run("INTERNAL DRAFT")
    watermark_run.bold = True

    # Body brand block — ANS logo, optional prospect logo, title.
    ans_paragraph = document.add_paragraph()
    ans_paragraph.add_run().add_picture(io.BytesIO(ans_logo_bytes))
    if prospect_logo_bytes:
        prospect_paragraph = document.add_paragraph()
        prospect_paragraph.add_run().add_picture(
            io.BytesIO(prospect_logo_bytes),
        )
    title_paragraph = document.add_paragraph(style="Heading 1")
    title_paragraph.add_run(company_name)
    subtitle_paragraph = document.add_paragraph()
    subtitle_paragraph.add_run("Prospect Brief")


# ---------------------------------------------------------------------------
# Provenance projection
# ---------------------------------------------------------------------------

def renderer_provenance() -> dict[str, str]:
    """Return the ``renderer`` provenance dict for the manifest entry."""
    return {"name": RENDERER_NAME, "version": _renderer_version()}


def markdown_renderer_provenance() -> dict[str, str]:
    """Return the ``markdown_renderer`` provenance dict for the manifest."""
    return {
        "name": MARKDOWN_RENDERER_NAME,
        "version": MARKDOWN_RENDERER_VERSION,
    }


def template_sha256() -> str:
    """Return the SHA-256 of the pinned ``base.docx`` template.

    Exposed as a function (rather than the module-level constant
    only) so the route layer's import-site reads the same string the
    determinism tests assert against, and so a future "re-read each
    request" change can land without touching every caller.
    """
    return TEMPLATE_SHA256


__all__ = [
    "EXPORTER_VERSION",
    "TEMPLATE_VERSION",
    "RENDERER_NAME",
    "TEMPLATE_SHA256",
    "render_docx",
    "renderer_provenance",
    "markdown_renderer_provenance",
    "template_sha256",
]
