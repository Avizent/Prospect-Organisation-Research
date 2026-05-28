"""Tests for the Markdown → HTML converter (Step 36).

The converter lives in ``backend.exporters.markdown_to_html`` and is
deliberately isolated from the WeasyPrint side: it is a pure function
``markdown_to_html(text) -> str`` plus two pinned provenance
constants. These tests pin the contract.
"""

from __future__ import annotations

import re

import pytest


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def test_provenance_constants_are_present_and_shaped() -> None:
    from backend.exporters.markdown_to_html import (
        MARKDOWN_RENDERER_NAME,
        MARKDOWN_RENDERER_VERSION,
    )

    assert MARKDOWN_RENDERER_NAME == "markdown"
    assert isinstance(MARKDOWN_RENDERER_VERSION, str)
    assert _SEMVER_RE.match(MARKDOWN_RENDERER_VERSION), (
        f"MARKDOWN_RENDERER_VERSION must be semver-shaped; "
        f"got {MARKDOWN_RENDERER_VERSION!r}"
    )


def test_markdown_to_html_is_deterministic() -> None:
    """Two calls with the same input produce byte-equal output.
    This is the contract the PDF renderer leans on."""
    from backend.exporters.markdown_to_html import markdown_to_html

    src = "# Title\n\nParagraph with *emphasis* and a [link text](anchor).\n"
    a = markdown_to_html(src)
    b = markdown_to_html(src)
    assert a == b


def test_markdown_to_html_renders_headings() -> None:
    from backend.exporters.markdown_to_html import markdown_to_html

    out = markdown_to_html("# Top\n## Mid\n### Low\n")
    assert "<h1>Top</h1>" in out
    assert "<h2>Mid</h2>" in out
    assert "<h3>Low</h3>" in out


def test_markdown_to_html_renders_tables() -> None:
    """``tables`` extension is pinned on; pipe tables must convert."""
    from backend.exporters.markdown_to_html import markdown_to_html

    src = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = markdown_to_html(src)
    assert "<table>" in out
    assert "<th>A</th>" in out
    assert "<td>1</td>" in out


def test_markdown_to_html_renders_fenced_code() -> None:
    """``fenced_code`` extension is pinned on; triple-backtick blocks
    must convert to ``<pre><code>``."""
    from backend.exporters.markdown_to_html import markdown_to_html

    src = "```\nx = 1\n```\n"
    out = markdown_to_html(src)
    assert "<pre>" in out
    assert "<code>" in out
    assert "x = 1" in out


def test_markdown_to_html_returns_fragment_not_full_document() -> None:
    """The converter MUST NOT wrap the output in ``<html>`` / ``<body>``.
    The PDF template provides the wrapping; double-wrapping would
    produce invalid HTML that WeasyPrint may or may not tolerate."""
    from backend.exporters.markdown_to_html import markdown_to_html

    out = markdown_to_html("# Hello\n")
    assert "<html" not in out
    assert "<body" not in out
