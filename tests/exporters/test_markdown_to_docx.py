"""Tests for :mod:`backend.exporters.markdown_to_docx` (Step 38).

Coverage:

  * Headings ``# … ######`` produce ``Heading 1 … Heading 6`` styled
    paragraphs.
  * Bullet lists produce ``List Bullet`` styled paragraphs.
  * Ordered lists produce ``List Number`` styled paragraphs.
  * Fenced code blocks produce paragraphs with monospace runs.
  * Pipe tables produce a ``Table Grid`` table with a header row.
  * Inline ``**bold**`` / ``*italic*`` / ``` `code` ``` become
    bold / italic / monospace runs.
  * The renderer provenance is the Python-Markdown package + version.
"""

from __future__ import annotations

import pytest

docx = pytest.importorskip("docx")


def _new_document():
    return docx.Document()


def _styles(document) -> list[str]:
    return [p.style.name for p in document.paragraphs]


def _texts(document) -> list[str]:
    return [p.text for p in document.paragraphs]


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level,style", [
    (1, "Heading 1"),
    (2, "Heading 2"),
    (3, "Heading 3"),
    (4, "Heading 4"),
    (5, "Heading 5"),
    (6, "Heading 6"),
])
def test_heading_levels_map_to_heading_styles(level: int, style: str) -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, f"{'#' * level} Acme")
    assert style in _styles(doc)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_bullet_list_uses_list_bullet_style() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "- a\n- b\n- c\n")
    styles = _styles(doc)
    assert styles.count("List Bullet") == 3


def test_ordered_list_uses_list_number_style() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "1. a\n2. b\n3. c\n")
    styles = _styles(doc)
    assert styles.count("List Number") == 3


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


def test_fenced_code_block_uses_monospace_run() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "```\nhello\n```\n")
    # The code paragraph carries a run with the DejaVu monospace face.
    found = False
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if run.font.name == "DejaVu Sans Mono" and "hello" in run.text:
                found = True
    assert found, "expected a DejaVu Sans Mono run carrying the code text"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_pipe_table_creates_table_grid_table() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    md = (
        "| Col A | Col B |\n"
        "| --- | --- |\n"
        "| a1 | b1 |\n"
        "| a2 | b2 |\n"
    )
    render_markdown_into(doc, md)
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert table.style.name == "Table Grid"
    assert len(table.rows) == 3  # header + 2 body
    assert table.rows[0].cells[0].text == "Col A"
    assert table.rows[2].cells[1].text == "b2"


# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------


def test_bold_inline_produces_bold_run() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "**loud**")
    runs = [r for p in doc.paragraphs for r in p.runs if r.text]
    assert any(r.bold and r.text == "loud" for r in runs)


def test_italic_inline_produces_italic_run() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "*soft*")
    runs = [r for p in doc.paragraphs for r in p.runs if r.text]
    assert any(r.italic and r.text == "soft" for r in runs)


def test_inline_code_produces_monospace_run() -> None:
    from backend.exporters.markdown_to_docx import render_markdown_into

    doc = _new_document()
    render_markdown_into(doc, "use `cmd` here")
    runs = [r for p in doc.paragraphs for r in p.runs if r.text]
    assert any(
        r.font.name == "DejaVu Sans Mono" and r.text == "cmd"
        for r in runs
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_markdown_renderer_provenance_dict() -> None:
    from backend.exporters.markdown_to_docx import (
        MARKDOWN_RENDERER_NAME,
        MARKDOWN_RENDERER_VERSION,
        markdown_renderer_provenance,
    )

    prov = markdown_renderer_provenance()
    assert prov["name"] == MARKDOWN_RENDERER_NAME == "markdown"
    assert prov["version"] == MARKDOWN_RENDERER_VERSION
