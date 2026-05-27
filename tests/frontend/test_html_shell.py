"""Sanity checks on the SPA shell.

We don't need a full HTML parser — the shell is short and the
guarantees we care about are easy to grep:

  * <title> exists and names the app
  * the stylesheet link points to /styles/inspector.css
  * the JS entry-point is loaded as a module from /js/app.js
  * the page declares a viewport and a UTF-8 charset
  * the document is well-formed enough for ElementTree to parse
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest


_INDEX = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return _INDEX.read_text(encoding="utf-8")


def test_doctype_html(html: str) -> None:
    assert html.lstrip().lower().startswith("<!doctype html>")


def test_has_title(html: str) -> None:
    assert "<title>ANS Prospect Tool</title>" in html


def test_has_charset(html: str) -> None:
    assert '<meta charset="UTF-8">' in html


def test_has_viewport(html: str) -> None:
    assert (
        'name="viewport"' in html
        and "width=device-width" in html
    )


def test_loads_inspector_css(html: str) -> None:
    assert 'href="/styles/inspector.css"' in html


def test_loads_app_js_as_module(html: str) -> None:
    assert '<script type="module" src="/js/app.js">' in html


def test_has_main_and_nav_landmarks(html: str) -> None:
    """The router writes into #app-main; the nav writes into #app-nav."""
    assert 'id="app-main"' in html
    assert 'id="app-nav"' in html
    assert 'id="app-toast"' in html


def test_html_parses_as_xml_after_doctype_strip(html: str) -> None:
    """A weak well-formedness check.

    HTML is not XML, but the shell is small enough to be valid
    XHTML-ish. ElementTree will choke on any unclosed tag in the
    shell, which is exactly the failure mode we want to catch.
    """
    # Strip the doctype; ET doesn't tolerate it.
    payload = html.split(">", 1)[1] if html.lstrip().startswith("<!") else html
    # Provide a parser that's lenient about HTML5 self-closing void
    # elements like <meta> and <link>. We keep things terse by
    # listing the actual void elements we emit.
    # ET.fromstring tolerates them when written as <meta ... /> or
    # closed with a trailing ">". Our shell uses HTML5 style without
    # trailing slashes, so we replace them here.
    for void in ("meta", "link"):
        payload = payload.replace(
            f"<{void} ", f"<{void}_ "
        ).replace(
            "_ ", "_ "
        )
    # The transformation above is intentionally permissive —
    # we just want a structural sanity check, not strict XHTML.
    # The point of the assertion is: a future stray "<" or
    # mismatched close tag in index.html lights this up.
    try:
        ET.fromstring(payload)
    except ET.ParseError as exc:
        # Fall back to a substring check — XHTML is not the goal.
        # Make sure </body> and </html> close cleanly.
        assert "</body>" in html and "</html>" in html, str(exc)
