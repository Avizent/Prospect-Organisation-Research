"""Markdown → HTML conversion for the PDF exporter (Step 36).

The PDF renderer consumes ``prospect_brief.md`` and produces a
deterministic PDF. The first stage of that pipeline is a pure
Markdown → HTML transformation, factored into its own tiny module so
it can be unit-tested in isolation from the WeasyPrint side and so
the renderer-provenance record on the manifest names a concrete
package + version.

Why a dedicated module
----------------------

Markdown → HTML is one of the two independent provenance axes inside
the PDF render pipeline:

  - ``markdown_renderer`` — the Python-Markdown package, version
    derived live from ``markdown.__version__``. A bump here changes
    the HTML the WeasyPrint step receives and therefore the final
    PDF bytes — separate from a template bump.
  - ``renderer``           — WeasyPrint, recorded at the
    :mod:`backend.exporters.pdf` layer.

Splitting the two lets the manifest's ``exports[]`` entry carry both
axes, so a future "why did this PDF change?" triage knows whether to
blame the Markdown parser or the HTML→PDF engine.

Determinism
-----------

Python-Markdown produces byte-equal HTML across runs for the same
input + same extension set + same options. We pin the extension list
explicitly here rather than relying on the package's default, so a
package update that changed defaults would not silently change our
output.

We deliberately do NOT enable extensions that expand syntactic
surface (e.g. ``footnotes``, ``toc``, ``meta``) — the assembler's
canonical Markdown already targets a small, well-behaved subset
(headings, lists, code blocks, fenced code, simple tables). Adding
extensions would couple the PDF surface to syntax the assembler does
not actually emit.
"""

from __future__ import annotations

import markdown as _markdown


# Pinned at the module level so the PDF renderer can read it without
# importing the third-party package itself.
MARKDOWN_RENDERER_NAME: str = "markdown"
MARKDOWN_RENDERER_VERSION: str = _markdown.__version__


# Extensions enabled for the canonical brief Markdown. ``tables``
# covers GFM-style pipe tables (the assembler uses them for the
# product-mapping and benefits rollups). ``fenced_code`` accepts
# triple-backtick code blocks — the assembler never emits code, but
# operators sometimes paste shell snippets into the briefing during
# regeneration and we want those to render as blocks not paragraphs.
_EXTENSIONS: tuple[str, ...] = (
    "tables",
    "fenced_code",
)


def markdown_to_html(markdown_text: str) -> str:
    """Convert ``markdown_text`` to HTML deterministically.

    The returned string is a fragment (no ``<html>`` / ``<body>``
    wrapper) — the PDF template wraps the fragment with brand chrome
    and the INTERNAL DRAFT watermark. Wrapping happens at the
    template layer so the wrapping HTML can change independently of
    the converter (this is the ``template_version`` axis).
    """
    return _markdown.markdown(
        markdown_text,
        extensions=list(_EXTENSIONS),
        output_format="html",
    )


__all__ = [
    "MARKDOWN_RENDERER_NAME",
    "MARKDOWN_RENDERER_VERSION",
    "markdown_to_html",
]
