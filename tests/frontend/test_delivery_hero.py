"""Step 48 — polished delivery screen: hero, download cards, and footer.

Content scans only. Pins the structural shape of the Step 48 additions to
``frontend/js/screens/manifest_viewer.js`` and the new CSS in ``design.css``.

Coverage:
* delivery hero section is present with class ``dr-hero``
* check-mark icon has class ``dr-check-circle``
* eyebrow text "INTELLIGENCE COMPLETE" is present
* hero title "Briefing Ready for Hand-off" is present
* ``dr-hero-subtitle`` class is present
* download card container uses ``dr-downloads``
* ``dr-download-card`` and variant classes are all present
* ``dr-download-title`` and ``dr-download-sub`` are present
* footer actions include "Back to Briefing Review" and "Prepare Another Job"
* design.css declares all new Step 48 CSS classes
"""

from __future__ import annotations

import pathlib

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VIEWER_JS  = _REPO_ROOT / "frontend" / "js" / "screens" / "manifest_viewer.js"
_DESIGN_CSS = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_src() -> str:
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Hero element structure
# ---------------------------------------------------------------------------

def test_dr_hero_class_present(viewer_src: str) -> None:
    """The delivery hero section must use the ``dr-hero`` class."""
    assert "dr-hero" in viewer_src, (
        "manifest_viewer.js must declare a hero div with class 'dr-hero'"
    )


def test_dr_check_circle_class_present(viewer_src: str) -> None:
    """The check-mark icon must use the ``dr-check-circle`` class."""
    assert "dr-check-circle" in viewer_src, (
        "manifest_viewer.js must include a check icon with class 'dr-check-circle'"
    )


def test_intelligence_complete_eyebrow_present(viewer_src: str) -> None:
    """The eyebrow text must read 'INTELLIGENCE COMPLETE'."""
    assert "INTELLIGENCE COMPLETE" in viewer_src, (
        "manifest_viewer.js must display 'INTELLIGENCE COMPLETE' as the eyebrow"
    )


def test_briefing_ready_heading_present(viewer_src: str) -> None:
    """The hero title must read 'Briefing Ready for Hand-off'."""
    assert "Briefing Ready for Hand-off" in viewer_src, (
        "manifest_viewer.js must display 'Briefing Ready for Hand-off' "
        "as the hero heading"
    )


def test_dr_hero_subtitle_class_present(viewer_src: str) -> None:
    """The subtitle paragraph must use the ``dr-hero-subtitle`` class."""
    assert "dr-hero-subtitle" in viewer_src, (
        "manifest_viewer.js must include a subtitle with class 'dr-hero-subtitle'"
    )


# ---------------------------------------------------------------------------
# 2. Download cards
# ---------------------------------------------------------------------------

def test_dr_downloads_container_present(viewer_src: str) -> None:
    """The card row container must use the ``dr-downloads`` class."""
    assert "dr-downloads" in viewer_src, (
        "manifest_viewer.js must use 'dr-downloads' as the card-row container"
    )


def test_dr_download_card_class_present(viewer_src: str) -> None:
    """Every download card must carry the base ``dr-download-card`` class."""
    assert "dr-download-card" in viewer_src, (
        "manifest_viewer.js must declare download cards with class 'dr-download-card'"
    )


def test_dr_download_card_primary_variant_present(viewer_src: str) -> None:
    """The PDF card must use the ``dr-download-card--primary`` variant."""
    assert "dr-download-card--primary" in viewer_src, (
        "manifest_viewer.js must include a primary download card variant"
    )


def test_dr_download_card_secondary_variant_present(viewer_src: str) -> None:
    """The DOCX card must use the ``dr-download-card--secondary`` variant."""
    assert "dr-download-card--secondary" in viewer_src, (
        "manifest_viewer.js must include a secondary download card variant"
    )


def test_dr_download_card_missing_variant_present(viewer_src: str) -> None:
    """When an export is absent the card must use ``dr-download-card--missing``."""
    assert "dr-download-card--missing" in viewer_src, (
        "manifest_viewer.js must include a missing-state download card variant"
    )


def test_dr_download_title_class_present(viewer_src: str) -> None:
    """Each card title must use the ``dr-download-title`` class."""
    assert "dr-download-title" in viewer_src, (
        "manifest_viewer.js must use 'dr-download-title' for card labels"
    )


def test_dr_download_sub_class_present(viewer_src: str) -> None:
    """Each card subtitle must use the ``dr-download-sub`` class."""
    assert "dr-download-sub" in viewer_src, (
        "manifest_viewer.js must use 'dr-download-sub' for card sub-labels"
    )


# ---------------------------------------------------------------------------
# 3. Footer navigation actions
# ---------------------------------------------------------------------------

def test_back_to_briefing_review_present(viewer_src: str) -> None:
    """A 'Back to Briefing Review' footer link must navigate back to the
    brief viewer so the operator can make corrections without reloading."""
    assert "Back to Briefing Review" in viewer_src, (
        "manifest_viewer.js must include a 'Back to Briefing Review' footer link"
    )


def test_prepare_another_job_present(viewer_src: str) -> None:
    """A 'Prepare Another Job' footer link must give the operator a clear
    path to start the next research run."""
    assert "Prepare Another Job" in viewer_src, (
        "manifest_viewer.js must include a 'Prepare Another Job' footer link"
    )


# ---------------------------------------------------------------------------
# 4. CSS declarations
# ---------------------------------------------------------------------------

def test_css_declares_dr_hero(css_src: str) -> None:
    """design.css must declare ``.dr-hero`` for the hero panel layout."""
    assert ".dr-hero" in css_src, (
        "design.css must declare .dr-hero for the Step 48 delivery hero"
    )


def test_css_declares_dr_download_card(css_src: str) -> None:
    """design.css must declare ``.dr-download-card`` for the card base styles."""
    assert ".dr-download-card" in css_src, (
        "design.css must declare .dr-download-card for Step 48 download cards"
    )


def test_css_declares_dr_hero_title(css_src: str) -> None:
    """design.css must declare ``.dr-hero-title`` for the hero heading."""
    assert ".dr-hero-title" in css_src, (
        "design.css must declare .dr-hero-title"
    )


def test_css_declares_dr_downloads(css_src: str) -> None:
    """design.css must declare ``.dr-downloads`` for the card-row container."""
    assert ".dr-downloads" in css_src, (
        "design.css must declare .dr-downloads for the download card row"
    )
