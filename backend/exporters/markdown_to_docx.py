"""Markdown → DOCX structural builder (Step 38).

The DOCX renderer consumes ``prospect_brief.md`` and produces a
deterministic ``.docx`` file. The first stage of that pipeline is a
pure Markdown → DOCX structural transformation, factored into its own
small module so it can be unit-tested in isolation from the
python-docx-side concerns (template loading, header/watermark) and so
the renderer-provenance record on the manifest names a concrete
``markdown_renderer`` package + version.

Why HTML-as-intermediate
------------------------

Python-Markdown is already the pinned Markdown engine for the PDF
renderer (see :mod:`backend.exporters.markdown_to_html`). Using it
again here — but routed via XHTML output into a deterministic
ElementTree walker — keeps the ``markdown_renderer.name`` field
identical across PDF and DOCX. A future "why did this DOCX change?"
triage looks at the same axis as a PDF triage.

We deliberately do NOT pipe through pandoc or htmldocx:

  * pandoc is a heavy system dependency we'd have to characterise and
    pin per-host; it would also re-introduce a third determinism
    boundary.
  * htmldocx adds another non-frozen library between us and the on-disk
    XML; the structural mapping we need is small enough to handle in
    ~150 lines of pure code.

What we map
-----------

The assembler's canonical Markdown targets a small, well-behaved
subset of GFM. The builder maps:

  * ``# … ######``       → :class:`docx.text.paragraph.Paragraph` with
                           a ``Heading 1 … Heading 6`` style.
  * Paragraphs           → plain paragraphs with the default style.
  * Bullet lists         → ``List Bullet`` style paragraphs.
  * Ordered lists        → ``List Number`` style paragraphs.
  * Fenced code blocks   → paragraphs with monospace runs (we do not
                           ship a custom style — the run's font name
                           is set inline so the docx is portable).
  * Pipe tables          → :meth:`docx.document.Document.add_table`
                           with a single header row and ``Table Grid``
                           style.
  * Inline ``<strong>``  → bold run.
  * Inline ``<em>``      → italic run.
  * Inline ``<code>``    → monospace run.
  * Inline ``<br/>``     → soft line break inside the current run.

Anything else (images, footnotes, raw HTML beyond the above tags) is
emitted verbatim as plain text — the assembler does not produce them
and the operator-paste path for code already lives under the
``<pre><code>`` branch.

Determinism
-----------

Python-Markdown produces byte-equal XHTML across runs for the same
input + same extension set + same options. The walker iterates the
ElementTree in document order, so the resulting docx paragraph order
is a deterministic function of the input. python-docx itself does not
introduce non-determinism beyond the rsid markers and timestamps the
template / scrub step already handles (see
:func:`backend.exporters.determinism.scrub_docx_metadata`).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable

import markdown as _markdown

from backend.exporters.markdown_to_html import (
    MARKDOWN_RENDERER_NAME,
    MARKDOWN_RENDERER_VERSION,
)


# Extension list mirrors :mod:`backend.exporters.markdown_to_html` so
# the PDF and DOCX renderers see byte-equal HTML for the same input.
_EXTENSIONS: tuple[str, ...] = (
    "tables",
    "fenced_code",
)


# Tag → docx style name. Heading levels map 1:1. The body tags below
# are handled by ``_apply_block`` directly.
_HEADING_STYLES: dict[str, str] = {
    "h1": "Heading 1",
    "h2": "Heading 2",
    "h3": "Heading 3",
    "h4": "Heading 4",
    "h5": "Heading 5",
    "h6": "Heading 6",
}


def _markdown_to_xhtml(markdown_text: str) -> str:
    """Convert ``markdown_text`` to a single-rooted XHTML fragment.

    Python-Markdown's ``xhtml`` output ensures every element is
    well-formed XML, so the ElementTree walker can parse the result
    without HTML-specific corrections. We wrap the fragment in a
    single ``<root>`` element so the parser sees a well-formed
    document even when the Markdown produces multiple sibling blocks.
    """
    body = _markdown.markdown(
        markdown_text,
        extensions=list(_EXTENSIONS),
        output_format="xhtml",
    )
    return f"<root>{body}</root>"


def render_markdown_into(document, markdown_text: str) -> None:  # type: ignore[no-untyped-def]
    """Append the rendered Markdown to ``document`` in place.

    ``document`` is a :class:`docx.document.Document` (we accept it
    untyped so this module does not depend on python-docx for its
    public signature; the python-docx import happens inside the
    renderer's main entry point).

    The function walks the XHTML produced by Python-Markdown and
    issues one or more ``add_paragraph`` / ``add_table`` calls per
    block. Inline runs are applied via the helper :func:`_apply_inline`.
    """
    tree = ET.fromstring(_markdown_to_xhtml(markdown_text))
    for block in list(tree):
        _apply_block(document, block)


def _apply_block(document, block: ET.Element) -> None:  # type: ignore[no-untyped-def]
    """Dispatch a single XHTML block element onto python-docx calls."""
    tag = block.tag.lower()

    if tag in _HEADING_STYLES:
        paragraph = document.add_paragraph(style=_HEADING_STYLES[tag])
        _apply_inline(paragraph, block)
        return

    if tag == "p":
        paragraph = document.add_paragraph()
        _apply_inline(paragraph, block)
        return

    if tag == "ul":
        for li in block.findall("li"):
            paragraph = document.add_paragraph(style="List Bullet")
            _apply_inline(paragraph, li)
        return

    if tag == "ol":
        for li in block.findall("li"):
            paragraph = document.add_paragraph(style="List Number")
            _apply_inline(paragraph, li)
        return

    if tag == "pre":
        # Python-Markdown wraps fenced code as ``<pre><code>…</code></pre>``.
        code = block.find("code")
        text = "" if code is None else (code.text or "")
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        run.font.name = "DejaVu Sans Mono"
        return

    if tag == "blockquote":
        # Map blockquotes to a plain paragraph for now — the canonical
        # brief Markdown does not emit them; we want the fallback to
        # render rather than crash if an operator paste introduces one.
        paragraph = document.add_paragraph()
        for child in block:
            _apply_inline(paragraph, child)
        return

    if tag == "table":
        _apply_table(document, block)
        return

    # Unknown tag — emit a paragraph of its plain text so nothing is
    # silently dropped. The assembler never produces unknown tags.
    fallback = document.add_paragraph()
    fallback.add_run("".join(block.itertext()))


def _apply_table(document, block: ET.Element) -> None:  # type: ignore[no-untyped-def]
    """Render a pipe-table ``<table>`` block via python-docx."""
    thead = block.find("thead")
    tbody = block.find("tbody")
    header_cells: list[ET.Element] = []
    if thead is not None:
        header_row = thead.find("tr")
        if header_row is not None:
            header_cells = list(header_row.findall("th"))
    body_rows: list[list[ET.Element]] = []
    if tbody is not None:
        for tr in tbody.findall("tr"):
            body_rows.append(list(tr.findall("td")))

    column_count = max(
        [len(header_cells)] + [len(r) for r in body_rows] or [0]
    )
    if column_count == 0:
        return

    table = document.add_table(
        rows=1 + len(body_rows), cols=column_count,
    )
    table.style = "Table Grid"

    # Header row.
    for col_index in range(column_count):
        cell = table.rows[0].cells[col_index]
        cell_paragraph = cell.paragraphs[0]
        if col_index < len(header_cells):
            _apply_inline(cell_paragraph, header_cells[col_index], bold=True)
        else:
            cell_paragraph.add_run("")

    # Body rows.
    for row_index, row_cells in enumerate(body_rows, start=1):
        for col_index in range(column_count):
            cell = table.rows[row_index].cells[col_index]
            cell_paragraph = cell.paragraphs[0]
            if col_index < len(row_cells):
                _apply_inline(cell_paragraph, row_cells[col_index])
            else:
                cell_paragraph.add_run("")


def _apply_inline(
    paragraph,
    element: ET.Element,
    *,
    bold: bool = False,
    italic: bool = False,
    monospace: bool = False,
) -> None:  # type: ignore[no-untyped-def]
    """Walk inline children of ``element`` and append runs to ``paragraph``.

    The walker carries three formatting flags down the tree so a
    ``<strong><em>…</em></strong>`` nesting becomes a single bold+italic
    run rather than two separate runs.
    """
    if element.text:
        _emit_run(paragraph, element.text, bold, italic, monospace)
    for child in element:
        tag = child.tag.lower()
        if tag in ("strong", "b"):
            _apply_inline(paragraph, child, bold=True, italic=italic, monospace=monospace)
        elif tag in ("em", "i"):
            _apply_inline(paragraph, child, bold=bold, italic=True, monospace=monospace)
        elif tag == "code":
            _apply_inline(paragraph, child, bold=bold, italic=italic, monospace=True)
        elif tag == "br":
            run = paragraph.add_run()
            run.add_break()
        else:
            # Unknown inline tag — recurse with the same formatting so
            # the tag is invisible to the output.
            _apply_inline(
                paragraph, child, bold=bold, italic=italic, monospace=monospace,
            )
        if child.tail:
            _emit_run(paragraph, child.tail, bold, italic, monospace)


def _emit_run(
    paragraph,
    text: str,
    bold: bool,
    italic: bool,
    monospace: bool,
) -> None:  # type: ignore[no-untyped-def]
    """Append a single styled run to ``paragraph``."""
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if monospace:
        run.font.name = "DejaVu Sans Mono"


def markdown_renderer_provenance() -> dict[str, str]:
    """Return the ``markdown_renderer`` provenance dict for DOCX entries."""
    return {
        "name": MARKDOWN_RENDERER_NAME,
        "version": MARKDOWN_RENDERER_VERSION,
    }


__all__ = [
    "MARKDOWN_RENDERER_NAME",
    "MARKDOWN_RENDERER_VERSION",
    "render_markdown_into",
    "markdown_renderer_provenance",
]
