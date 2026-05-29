"""Step 47 — polished job status screen: page layout, task rows, and hints.

Content scans only. Pins the structural shape of the Step 47 additions to
``frontend/js/screens/job_status.js`` and the new CSS in ``design.css``.

Coverage:
* render() uses design-system page layout (.page, .page-header, .page-title,
  .page-subtitle)
* _renderProgressTimeline uses the new .progress-heading-row structure with
  "Aggregated Pipeline Progress" heading
* task rows contain .task-number, .task-info, and .task-hint elements
* "Task Execution Sequence" section heading is present
* all 10 hint strings are present in the source
* Stage rows index is captured (stage, idx) form
* existing progress classes are still present (regression check)
* no innerHTML, no setInterval, no WebSocket, no EventSource
* no forbidden delivery / system references in the file
* design.css declares new Step 47 classes
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JOB_STATUS_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"
_DESIGN_CSS    = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def inspector_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timeline_body(inspector_src: str) -> str:
    """Extract _renderProgressTimeline function body by brace-matching."""
    m = re.search(
        r"function\s+_renderProgressTimeline\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, "_renderProgressTimeline not found in job_status.js"
    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(inspector_src)):
        if inspector_src[i] == "{":
            depth += 1
        elif inspector_src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "could not find closing brace of _renderProgressTimeline"
    return inspector_src[m.start() : end + 1]


@pytest.fixture(scope="module")
def css_src() -> str:
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Page layout — render() uses design-system page wrapper and header
# ---------------------------------------------------------------------------

def test_page_class_used(inspector_src: str) -> None:
    """render() must wrap content in a div.page for consistent layout."""
    assert '"page"' in inspector_src or "'page'" in inspector_src, (
        "job_status.js render() must use the 'page' layout class"
    )


def test_page_header_class_used(inspector_src: str) -> None:
    """render() must use 'page-header' for the job header section."""
    assert "page-header" in inspector_src, (
        "job_status.js render() must use the 'page-header' class"
    )


def test_page_title_class_used(inspector_src: str) -> None:
    """render() must use 'page-title' for the company name heading."""
    assert "page-title" in inspector_src, (
        "job_status.js render() must use the 'page-title' class"
    )


def test_page_subtitle_class_used(inspector_src: str) -> None:
    """render() must use 'page-subtitle' for the job URL line."""
    assert "page-subtitle" in inspector_src, (
        "job_status.js render() must use the 'page-subtitle' class"
    )


# ---------------------------------------------------------------------------
# 2. Progress heading row — "Aggregated Pipeline Progress"
# ---------------------------------------------------------------------------

def test_aggregated_pipeline_progress_heading(timeline_body: str) -> None:
    """The timeline must open with an 'Aggregated Pipeline Progress' heading."""
    assert '"Aggregated Pipeline Progress"' in timeline_body, (
        "_renderProgressTimeline must include 'Aggregated Pipeline Progress' "
        "as the panel heading"
    )


def test_progress_heading_row_class(timeline_body: str) -> None:
    """The heading area must use the 'progress-heading-row' layout class."""
    assert "progress-heading-row" in timeline_body, (
        "_renderProgressTimeline must use 'progress-heading-row' "
        "for the heading layout div"
    )


def test_progress_overall_pct_class(timeline_body: str) -> None:
    """The large percentage figure must use the 'progress-overall-pct' class."""
    assert "progress-overall-pct" in timeline_body, (
        "_renderProgressTimeline must render the overall percentage using "
        "the 'progress-overall-pct' class"
    )


# ---------------------------------------------------------------------------
# 3. Task Execution Sequence heading and task rows
# ---------------------------------------------------------------------------

def test_task_seq_heading_present(timeline_body: str) -> None:
    """A 'Task Execution Sequence' heading must appear above the stage list."""
    assert '"Task Execution Sequence"' in timeline_body, (
        "_renderProgressTimeline must include a 'Task Execution Sequence' "
        "heading before the stage list"
    )


def test_task_number_class_present(timeline_body: str) -> None:
    """Each stage row must include a 'task-number' span for the sequence number."""
    assert "task-number" in timeline_body, (
        "_renderProgressTimeline must render a 'task-number' span in each row"
    )


def test_task_info_class_present(timeline_body: str) -> None:
    """Each stage row must include a 'task-info' wrapper div."""
    assert "task-info" in timeline_body, (
        "_renderProgressTimeline must render a 'task-info' wrapper div "
        "containing the label and hint"
    )


def test_task_hint_class_present(timeline_body: str) -> None:
    """Each stage row must include a 'task-hint' span for the descriptor."""
    assert "task-hint" in timeline_body, (
        "_renderProgressTimeline must render a 'task-hint' span in each row"
    )


def test_stages_map_uses_index(timeline_body: str) -> None:
    """stages.map() must capture the index for zero-padded task numbers."""
    assert re.search(r"stages\.map\(\s*\(\s*stage\s*,\s*idx\s*\)", timeline_body), (
        "stages.map() must use (stage, idx) destructuring to obtain "
        "the zero-padded task sequence number"
    )


# ---------------------------------------------------------------------------
# 4. Hint text — all 10 entries present in source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hint", [
    "Job created and queued for processing",
    "Gathering company intelligence from public sources",
    "Identifying key decision-makers and stakeholders",
    "Analysing business challenges and technology gaps",
    "Compiling research into a structured briefing",
    "Operator review and approval required",
    "Rendering the Markdown prospect brief",
    "Converting brief to PDF format",
    "Converting brief to Word document format",
    "All documents ready for delivery",
])
def test_hint_text_present(inspector_src: str, hint: str) -> None:
    """Each _TIMELINE_STAGES hint string must appear in job_status.js."""
    assert hint in inspector_src, (
        f"_TIMELINE_STAGES must include hint: {hint!r}"
    )


# ---------------------------------------------------------------------------
# 5. Regression — existing progress classes still present
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "progress-stage-row",
    "progress-stage-main",
    "progress-stage-label",
    "progress-stage-badge",
    "progress-stage-bar",
    "progress-stage-bar-fill",
    "progress-stage-icon",
    "progress-overall",
    "progress-card",
    "job-progress-timeline",
])
def test_existing_progress_class_retained(timeline_body: str, cls: str) -> None:
    """Existing progress CSS classes must not be removed by Step 47."""
    assert cls in timeline_body, (
        f"Step 47 must not remove the existing class '{cls}' — "
        f"it is relied on by existing CSS and tests"
    )


# ---------------------------------------------------------------------------
# 6. Safety fences
# ---------------------------------------------------------------------------

def test_no_innerhtml_in_file(inspector_src: str) -> None:
    assert "innerHTML" not in inspector_src, (
        "job_status.js must not use innerHTML"
    )


def test_no_set_interval(inspector_src: str) -> None:
    assert "setInterval" not in inspector_src, (
        "job_status.js must not use setInterval"
    )


def test_no_websocket_in_timeline(timeline_body: str) -> None:
    assert "WebSocket" not in timeline_body


def test_no_eventsource_in_timeline(timeline_body: str) -> None:
    assert "EventSource" not in timeline_body


_FORBIDDEN = (
    "M365", "m365", "Graph", "gmail", "Gmail",
    "smtp", "SMTP", "sendMail", "send_mail",
    "backend.delivery", "delivery_tracking",
    "anthropic", "Anthropic", "keyring", "keychain",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_in_file(inspector_src: str, needle: str) -> None:
    """job_status.js must not reference any forbidden delivery/system string."""
    assert needle not in inspector_src, (
        f"job_status.js must not reference {needle!r}"
    )


# ---------------------------------------------------------------------------
# 7. design.css — new Step 47 classes declared
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "progress-heading-row",
    "progress-heading",
    "progress-overall-pct",
    "task-seq-heading",
    "task-number",
    "task-info",
    "task-hint",
])
def test_css_class_declared(css_src: str, cls: str) -> None:
    """Each new Step 47 CSS class must be declared in design.css."""
    assert f".{cls}" in css_src, (
        f"design.css must declare .{cls} for the Step 47 task-row layout"
    )
