"""Step 50 — briefing review screen: visual design and three-column layout.

Content scans only. Pins the structural shape of the Step 50 additions
to ``frontend/js/screens/briefing.js`` and the new CSS in ``design.css``.

Coverage (31 assertions matching spec):
1.  br-page wrapper present
2.  "STAGE 3: BRIEFING REVIEW" eyebrow present
3.  "Review Corporate Briefing Document" title present
4.  page subtitle text present
5.  "Approve & Export Package" CTA present
6.  br-layout three-column wrapper present
7.  br-sidebar present
8.  "Briefing Sections" heading present
9.  br-section-nav present
10. br-section-nav-item present
11. br-section-nav-item--active present
12. br-editor-card present
13. "EDITABLE MODE" badge present
14. br-editor-textarea present
15. "Save Draft Changes" present
16. "Refine Section with AI" present (regenerate behaviour supported)
17. br-panels present
18. "Section Checklist" present
19. "Identified Gaps" present
20. "Verified Citations" present
21. "Not available for this section." fallback present
22. api.approveJob behaviour preserved
23. api.patchBriefing behaviour preserved
24. api.requestRegeneration behaviour preserved
25. no fake citation strings introduced
26. no Gemini branding
27. no M365/Gmail/SMTP/sendMail/backend.delivery references
28. no innerHTML
29. no WebSocket
30. no EventSource
31. no setInterval
+ CSS class declarations in design.css
"""

from __future__ import annotations

import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BRIEFING_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "briefing.js"
_DESIGN_CSS  = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def src() -> str:
    return _BRIEFING_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_src() -> str:
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Page wrapper
# ---------------------------------------------------------------------------

def test_br_page_class_present(src: str) -> None:
    """The screen must wrap content in a div.br-page."""
    assert "br-page" in src, (
        "briefing.js must use the 'br-page' wrapper class"
    )


# ---------------------------------------------------------------------------
# 2. Page header
# ---------------------------------------------------------------------------

def test_stage3_eyebrow_present(src: str) -> None:
    """The eyebrow must read 'STAGE 3: BRIEFING REVIEW'."""
    assert "STAGE 3: BRIEFING REVIEW" in src, (
        "briefing.js must include the eyebrow 'STAGE 3: BRIEFING REVIEW'"
    )


def test_review_corporate_briefing_document_title(src: str) -> None:
    """The page title must read 'Review Corporate Briefing Document'."""
    assert "Review Corporate Briefing Document" in src, (
        "briefing.js must include the title 'Review Corporate Briefing Document'"
    )


def test_page_subtitle_present(src: str) -> None:
    """The subtitle must describe the operator's task."""
    assert "Edit generated sections, inspect gaps and sources" in src, (
        "briefing.js must include the page subtitle about editing and approving"
    )


def test_approve_export_package_cta_present(src: str) -> None:
    """The primary CTA must read 'Approve & Export Package'."""
    assert "Approve & Export Package" in src, (
        "briefing.js must use 'Approve & Export Package' as the approve CTA"
    )


# ---------------------------------------------------------------------------
# 3. Layout
# ---------------------------------------------------------------------------

def test_br_layout_class_present(src: str) -> None:
    """The three-column wrapper must use class 'br-layout'."""
    assert "br-layout" in src, (
        "briefing.js must use 'br-layout' for the three-column layout"
    )


# ---------------------------------------------------------------------------
# 4. Sidebar
# ---------------------------------------------------------------------------

def test_br_sidebar_class_present(src: str) -> None:
    """The sidebar must use class 'br-sidebar'."""
    assert "br-sidebar" in src, (
        "briefing.js must include a sidebar with class 'br-sidebar'"
    )


def test_briefing_sections_heading_present(src: str) -> None:
    """The sidebar must be headed 'Briefing Sections'."""
    assert "Briefing Sections" in src, (
        "briefing.js must display 'Briefing Sections' as the sidebar heading"
    )


def test_br_section_nav_class_present(src: str) -> None:
    """The sidebar nav list must use class 'br-section-nav'."""
    assert "br-section-nav" in src, (
        "briefing.js must use 'br-section-nav' for the section navigation"
    )


def test_br_section_nav_item_class_present(src: str) -> None:
    """Each nav link must carry the 'br-section-nav-item' class."""
    assert "br-section-nav-item" in src, (
        "briefing.js must use 'br-section-nav-item' for navigation links"
    )


def test_br_section_nav_item_active_class_present(src: str) -> None:
    """The active section must carry 'br-section-nav-item--active'."""
    assert "br-section-nav-item--active" in src, (
        "briefing.js must mark the active section with "
        "'br-section-nav-item--active'"
    )


# ---------------------------------------------------------------------------
# 5. Editor cards
# ---------------------------------------------------------------------------

def test_br_editor_card_class_present(src: str) -> None:
    """Each section editor must use class 'br-editor-card'."""
    assert "br-editor-card" in src, (
        "briefing.js must use 'br-editor-card' for section editor cards"
    )


def test_editable_mode_badge_present(src: str) -> None:
    """Each editor card must carry an 'EDITABLE MODE' badge."""
    assert "EDITABLE MODE" in src, (
        "briefing.js must display an 'EDITABLE MODE' badge on editor cards"
    )


def test_br_editor_textarea_class_present(src: str) -> None:
    """The edit textarea must use class 'br-editor-textarea'."""
    assert "br-editor-textarea" in src, (
        "briefing.js must use 'br-editor-textarea' on the editable textarea"
    )


def test_save_draft_changes_button_present(src: str) -> None:
    """The save button must read 'Save Draft Changes'."""
    assert "Save Draft Changes" in src, (
        "briefing.js must use 'Save Draft Changes' as the save button label"
    )


def test_refine_section_with_ai_button_present(src: str) -> None:
    """The refine button must read 'Refine Section with AI' since
    api.requestRegeneration is supported."""
    assert "Refine Section with AI" in src, (
        "briefing.js must use 'Refine Section with AI' for the regenerate button"
    )


# ---------------------------------------------------------------------------
# 6. Right panels
# ---------------------------------------------------------------------------

def test_br_panels_class_present(src: str) -> None:
    """The right-side panels container must use class 'br-panels'."""
    assert "br-panels" in src, (
        "briefing.js must use 'br-panels' for the right-side panel column"
    )


def test_section_checklist_panel_present(src: str) -> None:
    """A 'Section Checklist' panel must be present."""
    assert "Section Checklist" in src, (
        "briefing.js must include a 'Section Checklist' panel"
    )


def test_identified_gaps_panel_present(src: str) -> None:
    """An 'Identified Gaps' panel must be present."""
    assert "Identified Gaps" in src, (
        "briefing.js must include an 'Identified Gaps' panel"
    )


def test_verified_citations_panel_present(src: str) -> None:
    """A 'Verified Citations' panel must be present."""
    assert "Verified Citations" in src, (
        "briefing.js must include a 'Verified Citations' panel"
    )


def test_fallback_text_present(src: str) -> None:
    """When no real data exists the panels must show the honest fallback."""
    assert "Not available for this section." in src, (
        "briefing.js must display 'Not available for this section.' "
        "as the honest fallback in contextual panels"
    )


# ---------------------------------------------------------------------------
# 7. API behaviour preserved
# ---------------------------------------------------------------------------

def test_approve_job_api_preserved(src: str) -> None:
    assert "api.approveJob(" in src


def test_patch_briefing_api_preserved(src: str) -> None:
    assert "api.patchBriefing(" in src


def test_request_regeneration_api_preserved(src: str) -> None:
    assert "api.requestRegeneration(" in src


# ---------------------------------------------------------------------------
# 8. No fake data
# ---------------------------------------------------------------------------

_FAKE_CITATION_STRINGS = (
    "example.com", "acme.com", "Lorem ipsum",
    "fake_citation", "hardcoded_result",
)


@pytest.mark.parametrize("needle", _FAKE_CITATION_STRINGS)
def test_no_fake_citation_strings(src: str, needle: str) -> None:
    """No hardcoded fake citation strings must appear in the source."""
    assert needle not in src, (
        f"briefing.js must not contain hardcoded fake data: {needle!r}"
    )


# ---------------------------------------------------------------------------
# 9. Prohibited content
# ---------------------------------------------------------------------------

def test_no_gemini_branding(src: str) -> None:
    assert "Gemini" not in src and "gemini" not in src, (
        "briefing.js must not contain Gemini branding"
    )


@pytest.mark.parametrize("needle", [
    "M365", "m365", "gmail", "Gmail", "smtp", "SMTP",
    "sendMail", "send_mail", "backend.delivery",
])
def test_no_delivery_system_references(src: str, needle: str) -> None:
    assert needle not in src, (
        f"briefing.js must not reference {needle!r}"
    )


def test_no_innerhtml(src: str) -> None:
    assert "innerHTML" not in src


def test_no_websocket(src: str) -> None:
    assert "WebSocket" not in src


def test_no_eventsource(src: str) -> None:
    assert "EventSource" not in src


def test_no_set_interval(src: str) -> None:
    assert "setInterval" not in src


# ---------------------------------------------------------------------------
# 10. CSS declarations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "br-page",
    "br-page-eyebrow",
    "br-layout",
    "br-sidebar",
    "br-sidebar-heading",
    "br-sidebar-helper",
    "br-section-nav",
    "br-section-nav-item",
    "br-section-nav-item--active",
    "br-offline-note",
    "br-editor-card",
    "br-editor-header",
    "br-editor-title",
    "br-editable-badge",
    "br-editor-textarea",
    "br-editor-footer",
    "br-panels",
    "br-panel",
    "br-panel-heading",
    "br-panel-heading--warning",
    "br-panel-muted",
    "br-check-row",
    "br-gap-row",
    "br-citation-row",
])
def test_css_class_declared(css_src: str, cls: str) -> None:
    """Each new Step 50 CSS class must be declared in design.css."""
    assert f".{cls}" in css_src, (
        f"design.css must declare .{cls} for the Step 50 briefing review layout"
    )
