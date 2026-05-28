"""Step 36 — frontend exports section content scans.

Pins the structural shape of the Step 36 export controls in
``frontend/js/screens/manifest_viewer.js`` and ``frontend/js/api.js``:

  * an ``api.runPdfExport`` wrapper targets
    ``/api/jobs/{id}/export/pdf`` via POST,
  * the viewer renders the Generate and Download controls as TWO
    distinct elements (the plan calls them out by name) — a single
    "click here" affordance is explicitly disallowed, so the test
    pins both classes and asserts they are not co-located on the same
    element,
  * the Download link points at the GET retrieval path
    ``/api/jobs/{id}/exports/pdf`` (not the POST path),
  * the viewer renders provenance projections for the new manifest
    fields surfaced in Step 36 (``markdown_renderer``,
    ``regeneration_count``, ``exporter_version``,
    ``template_version``, ``renderer``).
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONTEND = _REPO_ROOT / "frontend" / "js"
_API_JS = _FRONTEND / "api.js"
_VIEWER_JS = _FRONTEND / "screens" / "manifest_viewer.js"


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# api.js wrapper
# ---------------------------------------------------------------------------


def test_api_exposes_run_pdf_export(api_src: str) -> None:
    assert "runPdfExport:" in api_src


def test_run_pdf_export_targets_export_pdf_path(api_src: str) -> None:
    m = re.search(
        r'runPdfExport:.*?/api/jobs/\$\{encodeURIComponent\(id\)\}/export/pdf',
        api_src,
        flags=re.DOTALL,
    )
    assert m, "runPdfExport must POST to /api/jobs/{id}/export/pdf"


def test_run_pdf_export_uses_post(api_src: str) -> None:
    m = re.search(
        r'runPdfExport:.*?request\("POST"', api_src, flags=re.DOTALL,
    )
    assert m, "runPdfExport must use POST"


# ---------------------------------------------------------------------------
# manifest_viewer.js — distinct Generate + Download elements
# ---------------------------------------------------------------------------


def test_viewer_has_generate_pdf_class(viewer_src: str) -> None:
    assert "btn-generate-pdf" in viewer_src


def test_viewer_has_download_pdf_class(viewer_src: str) -> None:
    assert "btn-download-pdf" in viewer_src


def test_generate_and_download_are_distinct_elements(viewer_src: str) -> None:
    """The Generate and Download controls must NOT collapse into a
    single element. We assert by checking that ``btn-generate-pdf``
    and ``btn-download-pdf`` never appear together inside the same
    ``class:`` literal."""
    pattern = re.compile(
        r'class:\s*"[^"]*btn-(?:generate|download)-pdf[^"]*"'
    )
    for match in pattern.finditer(viewer_src):
        literal = match.group(0)
        assert not (
            "btn-generate-pdf" in literal and "btn-download-pdf" in literal
        ), (
            "Generate and Download must be distinct elements; "
            f"found combined class literal: {literal!r}"
        )


def test_viewer_calls_run_pdf_export(viewer_src: str) -> None:
    assert "api.runPdfExport" in viewer_src


def test_download_link_targets_get_retrieval_path(viewer_src: str) -> None:
    """The Download anchor must point at ``/exports/pdf`` (GET), NOT
    the POST ``/export/pdf`` path."""
    m = re.search(
        r'class:\s*"btn-download-pdf".*?href:\s*`([^`]+)`',
        viewer_src,
        flags=re.DOTALL,
    )
    assert m, "btn-download-pdf must have an href"
    href = m.group(1)
    assert "/exports/pdf" in href, (
        f"Download link must use GET retrieval path; got {href!r}"
    )
    assert "/export/pdf" not in href.replace("/exports/pdf", ""), (
        f"Download link must not point at the POST path; got {href!r}"
    )


# ---------------------------------------------------------------------------
# Provenance projections from the exports[] entry
# ---------------------------------------------------------------------------


def test_viewer_surfaces_exporter_version(viewer_src: str) -> None:
    assert "exporter_version" in viewer_src


def test_viewer_surfaces_template_version(viewer_src: str) -> None:
    assert "template_version" in viewer_src


def test_viewer_surfaces_renderer_provenance(viewer_src: str) -> None:
    assert "entry.renderer" in viewer_src


def test_viewer_surfaces_markdown_renderer_provenance(viewer_src: str) -> None:
    """Amendment 2: markdown_renderer is a separate provenance axis."""
    assert "markdown_renderer" in viewer_src


def test_viewer_surfaces_regeneration_count(viewer_src: str) -> None:
    assert "regeneration_count" in viewer_src
