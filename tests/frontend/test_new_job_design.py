"""Step 49 — new job screen: visual design and layout.

Content scans only. Pins the structural shape of the Step 49 additions
to ``frontend/js/screens/new_job.js`` and the new CSS in ``design.css``.

Coverage (24 assertions matching spec):
1.  nj-page wrapper present
2.  nj-hero section present
3.  "Create Corporate Briefing Report" heading present
4.  hero subtitle text present
5.  nj-layout two-column wrapper present
6.  nj-form-card present
7.  "Prospect Details" card heading present
8.  Company Name field label present
9.  Website URL field label present
10. "Start Research Sweep" CTA present
11. form submission/API behaviour preserved (api.createJob)
12. nj-whatnext panel present
13. "What happens next?" heading present
14. "Data Ingestion" step present
15. "Stakeholder Auditing" step present
16. "Pitch Generation" step present
17. nj-operator-tip panel present
18. no Gemini branding
19. no M365/Gmail/SMTP references
20. no innerHTML
21. no WebSocket
22. no EventSource
23. no setInterval
24. design.css declares new nj-* CSS classes
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_NEW_JOB_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "new_job.js"
_DESIGN_CSS  = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def src() -> str:
    return _NEW_JOB_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_src() -> str:
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Page wrapper
# ---------------------------------------------------------------------------

def test_nj_page_class_present(src: str) -> None:
    """The screen must wrap content in a div.nj-page."""
    assert "nj-page" in src, (
        "new_job.js must use the 'nj-page' wrapper class"
    )


# ---------------------------------------------------------------------------
# 2. Hero section
# ---------------------------------------------------------------------------

def test_nj_hero_class_present(src: str) -> None:
    """A hero div with class 'nj-hero' must be present."""
    assert "nj-hero" in src, (
        "new_job.js must include a hero section with class 'nj-hero'"
    )


def test_create_corporate_briefing_report_heading(src: str) -> None:
    """The main heading must read 'Create Corporate Briefing Report'."""
    assert "Create Corporate Briefing Report" in src, (
        "new_job.js must include the heading 'Create Corporate Briefing Report'"
    )


def test_hero_subtitle_text_present(src: str) -> None:
    """The hero subtitle must describe the intelligence sweep."""
    assert "Initiate deep intelligence sweeps" in src, (
        "new_job.js must include the hero subtitle starting with "
        "'Initiate deep intelligence sweeps'"
    )


# ---------------------------------------------------------------------------
# 3. Layout
# ---------------------------------------------------------------------------

def test_nj_layout_class_present(src: str) -> None:
    """The two-column layout wrapper must use class 'nj-layout'."""
    assert "nj-layout" in src, (
        "new_job.js must use 'nj-layout' for the two-column wrapper"
    )


# ---------------------------------------------------------------------------
# 4. Form card
# ---------------------------------------------------------------------------

def test_nj_form_card_class_present(src: str) -> None:
    """The form must be wrapped in an 'nj-form-card' card."""
    assert "nj-form-card" in src, (
        "new_job.js must use 'nj-form-card' as the form card class"
    )


def test_prospect_details_heading_present(src: str) -> None:
    """The form card must be headed 'Prospect Details'."""
    assert "Prospect Details" in src, (
        "new_job.js must display 'Prospect Details' as the form card heading"
    )


def test_company_name_label_present(src: str) -> None:
    """The Company Name label must be visible to the operator."""
    assert "Company Name" in src, (
        "new_job.js must display a 'Company Name' label"
    )


def test_website_url_label_present(src: str) -> None:
    """The Website URL label must be visible to the operator."""
    assert "Website URL" in src, (
        "new_job.js must display a 'Website URL' label"
    )


def test_start_research_sweep_cta(src: str) -> None:
    """The primary CTA must read 'Start Research Sweep'."""
    assert "Start Research Sweep" in src, (
        "new_job.js must use 'Start Research Sweep' as the submit button text"
    )


def test_api_create_job_still_called(src: str) -> None:
    """api.createJob() must still be called — form behaviour must be preserved."""
    assert "api.createJob(" in src, (
        "new_job.js must still call api.createJob() on form submission"
    )


# ---------------------------------------------------------------------------
# 5. What happens next?
# ---------------------------------------------------------------------------

def test_nj_whatnext_class_present(src: str) -> None:
    """The 'What happens next?' panel must use class 'nj-whatnext'."""
    assert "nj-whatnext" in src, (
        "new_job.js must include a panel with class 'nj-whatnext'"
    )


def test_what_happens_next_heading(src: str) -> None:
    """The panel heading must read 'What happens next?'."""
    assert "What happens next?" in src, (
        "new_job.js must display 'What happens next?' as the panel heading"
    )


def test_data_ingestion_step_present(src: str) -> None:
    """Step 1 must be labelled 'Data Ingestion'."""
    assert "Data Ingestion" in src, (
        "new_job.js must include a 'Data Ingestion' step"
    )


def test_stakeholder_auditing_step_present(src: str) -> None:
    """Step 2 must be labelled 'Stakeholder Auditing'."""
    assert "Stakeholder Auditing" in src, (
        "new_job.js must include a 'Stakeholder Auditing' step"
    )


def test_pitch_generation_step_present(src: str) -> None:
    """Step 3 must be labelled 'Pitch Generation'."""
    assert "Pitch Generation" in src, (
        "new_job.js must include a 'Pitch Generation' step"
    )


# ---------------------------------------------------------------------------
# 6. Operator tip
# ---------------------------------------------------------------------------

def test_nj_operator_tip_class_present(src: str) -> None:
    """The operator tip must use class 'nj-operator-tip'."""
    assert "nj-operator-tip" in src, (
        "new_job.js must include an operator tip with class 'nj-operator-tip'"
    )


# ---------------------------------------------------------------------------
# 7. Prohibited content
# ---------------------------------------------------------------------------

def test_no_gemini_branding(src: str) -> None:
    """The screen must not contain any Gemini branding."""
    assert "Gemini" not in src and "gemini" not in src, (
        "new_job.js must not contain Gemini branding"
    )


@pytest.mark.parametrize("needle", [
    "M365", "m365", "gmail", "Gmail", "smtp", "SMTP",
    "sendMail", "send_mail", "backend.delivery",
])
def test_no_delivery_system_references(src: str, needle: str) -> None:
    """new_job.js must not reference any delivery system."""
    assert needle not in src, (
        f"new_job.js must not reference {needle!r}"
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
# 8. CSS declarations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "nj-page",
    "nj-hero",
    "nj-hero-title",
    "nj-hero-subtitle",
    "nj-layout",
    "nj-form-card",
    "nj-card-title",
    "nj-whatnext",
    "nj-whatnext-step",
    "nj-whatnext-icon",
    "nj-operator-tip",
    "nj-submit",
    "nj-input",
    "nj-label",
    "nj-field",
    "nj-side",
])
def test_css_class_declared(css_src: str, cls: str) -> None:
    """Each new Step 49 CSS class must be declared in design.css."""
    assert f".{cls}" in css_src, (
        f"design.css must declare .{cls} for the Step 49 new-job layout"
    )
