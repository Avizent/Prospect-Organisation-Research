"""Step 43 — manual delivery-ready files panel in the manifest viewer.

Content scans only — the project has no JS test runner. These tests pin
the structural shape of the ``_renderDeliveryReadyPanel`` function in
``frontend/js/screens/manifest_viewer.js``.

Coverage:
* the delivery-ready panel section is present in the viewer source
* the heading "Delivery-ready files" is present
* the manual-attach instruction text is present
* "Download latest PDF" download link is present when a PDF entry exists
* "Download latest DOCX" download link is present when a DOCX entry exists
* "Missing PDF" and "Missing DOCX" notices appear for absent exports
* a hint pointing back to Job Status appears when exports are missing
* the panel is strictly read-only — no api.* calls, no POST literals
* no M365, Graph, Gmail, SMTP, sendMail, delivery, or backend.delivery
* the Step 41 export history panel is unchanged
* the existing manifest-viewer tests remain unaffected
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VIEWER_JS = (
    _REPO_ROOT / "frontend" / "js" / "screens" / "manifest_viewer.js"
)


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def panel_body(viewer_src: str) -> str:
    """Extract the ``_renderDeliveryReadyPanel`` function body by
    brace-matching so assertions are scoped to the new panel only,
    preventing false positives from the existing generate/download
    controls in the exports section."""
    m = re.search(
        r"function\s+_renderDeliveryReadyPanel\s*\([^)]*\)\s*\{",
        viewer_src,
    )
    assert m is not None, (
        "_renderDeliveryReadyPanel not found in manifest_viewer.js — "
        "Step 43 implementation is missing"
    )
    start = m.end() - 1  # opening {
    depth = 0
    end = None
    for i in range(start, len(viewer_src)):
        ch = viewer_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, (
        "could not find closing brace of _renderDeliveryReadyPanel"
    )
    return viewer_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. Panel structure
# ---------------------------------------------------------------------------

def test_delivery_ready_panel_section_exists(viewer_src: str) -> None:
    """The panel must be built as a ``<section>`` with the
    ``delivery-ready-panel`` class so CSS and accessibility tooling
    can target it."""
    assert "delivery-ready-panel" in viewer_src, (
        "manifest_viewer.js must create a section with "
        "class 'delivery-ready-panel'"
    )


def test_delivery_ready_heading_present(viewer_src: str) -> None:
    """The panel heading must read 'Delivery-ready files' so operators
    can locate the download section at a glance after generation."""
    assert "Delivery-ready files" in viewer_src, (
        "manifest_viewer.js must contain the heading "
        "'Delivery-ready files'"
    )


def test_manual_attach_instruction_present(viewer_src: str) -> None:
    """The instruction 'Download these files and attach them manually'
    must appear in the panel so the operator knows this is a manual
    step and no email is sent automatically."""
    assert "Download these files and attach them manually" in viewer_src, (
        "manifest_viewer.js must contain the manual-attach instruction "
        "'Download these files and attach them manually'"
    )


# ---------------------------------------------------------------------------
# 2. Download links
# ---------------------------------------------------------------------------

def test_pdf_download_link_present(viewer_src: str) -> None:
    """The panel must expose a 'Download latest PDF' link so the
    operator has a one-click path to the current PDF export."""
    assert "Download latest PDF" in viewer_src, (
        "manifest_viewer.js must contain the text 'Download latest PDF'"
    )


def test_docx_download_link_present(viewer_src: str) -> None:
    """The panel must expose a 'Download latest DOCX' link so the
    operator has a one-click path to the current DOCX export."""
    assert "Download latest DOCX" in viewer_src, (
        "manifest_viewer.js must contain the text 'Download latest DOCX'"
    )


def test_pdf_download_link_targets_exports_route(panel_body: str) -> None:
    """The PDF download link must target the exports route, not the
    POST export-trigger path."""
    assert re.search(
        r'href:\s*`/api/jobs/\$\{[^}]+\}/exports/pdf`',
        panel_body,
    ), (
        "_renderDeliveryReadyPanel must link to /api/jobs/{id}/exports/pdf "
        "— the GET retrieval path, not the POST trigger"
    )


def test_docx_download_link_targets_exports_route(panel_body: str) -> None:
    """The DOCX download link must target the exports route."""
    assert re.search(
        r'href:\s*`/api/jobs/\$\{[^}]+\}/exports/docx`',
        panel_body,
    ), (
        "_renderDeliveryReadyPanel must link to /api/jobs/{id}/exports/docx"
    )


def test_pdf_download_uses_anchor_not_button(panel_body: str) -> None:
    """Download controls must be ``<a>`` anchor elements, not ``<button>``
    elements that could be confused with POST-triggering controls."""
    assert "btn-download-pdf-delivery" in panel_body, (
        "PDF download link must carry class btn-download-pdf-delivery"
    )
    # The class must be on an anchor, not a button.
    assert re.search(
        r'el\(\s*"a"\s*,\s*\{[^}]*btn-download-pdf-delivery',
        panel_body,
        flags=re.DOTALL,
    ), (
        "PDF download element must be el(\"a\", ...) not a button"
    )


def test_docx_download_uses_anchor_not_button(panel_body: str) -> None:
    """DOCX download link must also be an anchor."""
    assert "btn-download-docx-delivery" in panel_body, (
        "DOCX download link must carry class btn-download-docx-delivery"
    )
    assert re.search(
        r'el\(\s*"a"\s*,\s*\{[^}]*btn-download-docx-delivery',
        panel_body,
        flags=re.DOTALL,
    ), (
        "DOCX download element must be el(\"a\", ...) not a button"
    )


# ---------------------------------------------------------------------------
# 3. Missing-file notices
# ---------------------------------------------------------------------------

def test_missing_pdf_notice_present(viewer_src: str) -> None:
    """When no PDF exists the panel must surface 'Missing PDF' so the
    operator understands what needs to be generated."""
    assert "Missing PDF" in viewer_src, (
        "manifest_viewer.js must display 'Missing PDF' when no PDF entry "
        "is present in the manifest"
    )


def test_missing_docx_notice_present(viewer_src: str) -> None:
    """When no DOCX exists the panel must surface 'Missing DOCX'."""
    assert "Missing DOCX" in viewer_src, (
        "manifest_viewer.js must display 'Missing DOCX' when no DOCX entry "
        "is present in the manifest"
    )


def test_missing_hint_points_to_generate_documents(viewer_src: str) -> None:
    """When exports are missing the panel must instruct the operator to
    use Generate Documents, closing the UX loop between the manifest
    viewer and the job status screen."""
    assert "Generate Documents" in viewer_src, (
        "manifest_viewer.js must mention 'Generate Documents' in the "
        "missing-files hint"
    )


def test_missing_hint_links_to_job_status(panel_body: str) -> None:
    """The missing-exports hint must include a link back to the job
    status screen so the operator can navigate there in one click."""
    assert re.search(
        r'href:\s*`#/jobs/\$\{[^}]+\}`',
        panel_body,
    ), (
        "_renderDeliveryReadyPanel must include a link to "
        "#/jobs/{jobId} when exports are missing"
    )


# ---------------------------------------------------------------------------
# 4. Read-only — no POST, no api.* calls in the panel
# ---------------------------------------------------------------------------

def test_panel_makes_no_api_calls(panel_body: str) -> None:
    """The delivery panel must not call any api.* method. It is
    purely a download-link presenter — any fetch call would mean it
    is triggering work that belongs to a different screen."""
    api_calls = re.findall(r"api\.(\w+)\(", panel_body)
    assert not api_calls, (
        f"_renderDeliveryReadyPanel must not call any api.* methods; "
        f"found: {api_calls}"
    )


def test_panel_contains_no_post_literal(panel_body: str) -> None:
    """No 'POST' string literal may appear in the panel function body.
    The existing manifest_viewer_never_calls_assemble_route test already
    enforces this for the whole file; this test scopes the assertion to
    the delivery panel specifically for clarity."""
    assert '"POST"' not in panel_body, (
        "_renderDeliveryReadyPanel must not contain a 'POST' literal"
    )
    assert "'POST'" not in panel_body, (
        "_renderDeliveryReadyPanel must not contain a 'POST' literal"
    )


def test_panel_is_called_in_render(viewer_src: str) -> None:
    """``_renderDeliveryReadyPanel`` must be called from the ``render``
    function so it actually appears in the DOM."""
    assert "_renderDeliveryReadyPanel(" in viewer_src, (
        "_renderDeliveryReadyPanel must be called from render()"
    )


# ---------------------------------------------------------------------------
# 5. No forbidden delivery-system references
# ---------------------------------------------------------------------------

_FORBIDDEN_DELIVERY_TERMS = (
    "M365",
    "m365",
    "Graph",
    "gmail",
    "Gmail",
    "smtp",
    "SMTP",
    "sendMail",
    "send_mail",
    "backend.delivery",
    "delivery_tracking",
    "background_job",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_DELIVERY_TERMS)
def test_no_delivery_system_in_panel(panel_body: str, needle: str) -> None:
    """The delivery panel must not reference any external delivery
    system. Step 43 is manual-download only — M365, Gmail, SMTP, and
    Graph integration are deferred to a later step."""
    assert needle not in panel_body, (
        f"_renderDeliveryReadyPanel must not reference {needle!r}; "
        "Step 43 is manual-attachment only, no automated delivery"
    )


# ---------------------------------------------------------------------------
# 6. Historical export panel unchanged
# ---------------------------------------------------------------------------

def test_export_history_panel_still_present(viewer_src: str) -> None:
    """Step 41's export history panel must remain intact after Step 43
    adds the delivery-ready panel."""
    assert "manifest-export-history-panel" in viewer_src, (
        "Step 41 export history panel must not be removed or renamed "
        "by Step 43"
    )


def test_render_exports_function_still_present(viewer_src: str) -> None:
    """The ``_renderExports`` function that wraps per-format panels and
    the history panel must remain present."""
    assert "_renderExports(" in viewer_src, (
        "_renderExports must remain present — Step 43 must not remove "
        "the exports section"
    )


def test_export_history_render_function_still_present(
    viewer_src: str,
) -> None:
    """``_renderExportHistory`` and ``_renderExportHistoryForFormat``
    from Step 41 must remain present."""
    assert "_renderExportHistory(" in viewer_src
    assert "_renderExportHistoryForFormat(" in viewer_src
