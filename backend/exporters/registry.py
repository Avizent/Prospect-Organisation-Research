"""Renderer dispatch registry (Step 39).

A single source of truth that maps a format key (``"pdf"`` / ``"docx"``)
to everything the route layer needs to dispatch on:

  * the pure renderer function (``render_pdf`` / ``render_docx``),
  * the renderer-provenance projector,
  * the markdown-renderer-provenance projector,
  * the optional template-SHA-256 projector (``None`` for PDF — the
    PDF template lives in HTML/CSS source files whose drift is already
    covered by ``TEMPLATE_VERSION``; DOCX carries a SHA-256 of its
    pinned ``base.docx`` binary as the Step 38 amendment 2 axis),
  * the on-disk filename for the export,
  * the HTTP ``Content-Type`` to stream the bytes back with.

Why a frozen dataclass and not an ABC / plugin protocol
-------------------------------------------------------

The renderer surface has TWO entries and is governed by a closed
taxonomy (``backend.exporters.lifecycle._KNOWN_EXPORT_FORMATS``).
Inheritance + a registration hook would be more machinery than the
problem warrants and would silently invite a fifth format without the
explicit step-gated review the project's hard rules require. The
dataclass is the contract: anyone adding a format must touch this
file AND the lifecycle/storage constants AND the routes, and the
invariant test in ``tests/jobs_routes/test_export_routes_internals.py``
will fail until all four sets agree.

Why not import this from the route module's `__init__`
------------------------------------------------------

The registry sits inside ``backend.exporters`` because the
renderer-specific knowledge (provenance projectors, template hashes,
media types) is exporter-package knowledge, not route-layer knowledge.
The route module imports the registry the same way it already imports
the renderers — that is the deliberate Step-36 fence relaxation
already pinned by ``tests/jobs_routes/test_export_routes_static_fence``.

Determinism / purity
--------------------

This module performs NO I/O at import time other than the renderer
modules' own module-level work (which is already covered by their
own tests — e.g. the DOCX module reads ``base.docx`` once at import
to compute ``TEMPLATE_SHA256``). The ``RendererSpec`` values are
captured as callable references, not as eagerly-evaluated dicts, so
the registry can be imported in test contexts without touching
optional renderer state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from backend.exporters.base import ExportResult
from backend.exporters.docx import (
    markdown_renderer_provenance as _docx_markdown_renderer_provenance,
    render_docx,
    renderer_provenance as _docx_renderer_provenance,
    template_sha256 as _docx_template_sha256,
)
from backend.exporters.pdf import (
    markdown_renderer_provenance as _pdf_markdown_renderer_provenance,
    render_pdf,
    renderer_provenance as _pdf_renderer_provenance,
)


# ---------------------------------------------------------------------------
# RendererSpec — the per-format dispatch record.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RendererSpec:
    """All renderer-specific knowledge the route layer dispatches on.

    The ``render`` callable is the pure renderer entry-point; it takes
    the same kwargs as ``render_pdf`` / ``render_docx`` and returns an
    :class:`backend.exporters.base.ExportResult`.

    ``template_sha256`` is ``None`` for renderers that have no on-disk
    binary template — currently the PDF renderer, whose templates are
    plain-text HTML/CSS source files. DOCX returns a callable so the
    SHA-256 of ``base.docx`` is read live (the module-level constant
    is computed at import time, so this is cheap).
    """

    render: Callable[..., ExportResult]
    renderer_provenance: Callable[[], dict[str, str]]
    markdown_renderer_provenance: Callable[[], dict[str, str]]
    template_sha256: Optional[Callable[[], str]]
    filename: str
    media_type: str


# ---------------------------------------------------------------------------
# EXPORT_RENDERERS — the closed registry.
# ---------------------------------------------------------------------------
#
# Keys MUST equal:
#   * ``backend.exporters.lifecycle._KNOWN_EXPORT_FORMATS``
#   * ``backend.jobs.storage._EXPORT_FORMATS``
#   * the set of route formats declared in ``backend.jobs.export_routes``
#
# The invariant test
# ``tests/jobs_routes/test_export_routes_internals.py::test_registry_formats_match_route_formats``
# pins all four sets to the same frozenset literal.

EXPORT_RENDERERS: Mapping[str, RendererSpec] = {
    "pdf": RendererSpec(
        render=render_pdf,
        renderer_provenance=_pdf_renderer_provenance,
        markdown_renderer_provenance=_pdf_markdown_renderer_provenance,
        template_sha256=None,
        filename="prospect_brief.pdf",
        media_type="application/pdf",
    ),
    "docx": RendererSpec(
        render=render_docx,
        renderer_provenance=_docx_renderer_provenance,
        markdown_renderer_provenance=_docx_markdown_renderer_provenance,
        template_sha256=_docx_template_sha256,
        filename="prospect_brief.docx",
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    ),
}


__all__ = [
    "RendererSpec",
    "EXPORT_RENDERERS",
]
