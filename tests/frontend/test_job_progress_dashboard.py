"""Step 45 (improved) — polished job progress dashboard in the job status screen.

Content scans only — the project has no JS test runner. These tests pin
the structural shape of the redesigned progress panel in
``frontend/js/screens/job_status.js``.

Coverage:
* ``progress-card`` wrapper exists in the timeline renderer
* ``progress-overall`` section exists
* ``progress-overall-bar`` class on the determinate <progress> element
* ``progress-stage-row`` replaces the old bullet-list items
* ``progress-stage-badge`` exists for status labels
* state badge text: Complete / In progress / Pending / Waiting / Failed
* each user-facing stage label is present in the source
* ``_PAST_APPROVAL_STATES`` constant is declared
* virtual stages: "submitted" and "approval" are defined in _TIMELINE_STAGES
* ``_computeStages`` handles the "approval" virtual stage via _PAST_APPROVAL_STATES
* ``_computeEta`` skips virtual stages and de-duplicates shared artefact keys
* existing ETA line retained
* honest-approximation caveat retained (updated wording)
* no fake per-task percentages
* no setInterval / WebSocket / EventSource
* no M365/email/delivery/backend references in the renderer
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JOB_STATUS_JS = (
    _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"
)


@pytest.fixture(scope="module")
def inspector_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timeline_body(inspector_src: str) -> str:
    """Extract the ``_renderProgressTimeline`` function body."""
    m = re.search(
        r"function\s+_renderProgressTimeline\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, "_renderProgressTimeline not found"
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
    assert end is not None
    return inspector_src[m.start() : end + 1]


@pytest.fixture(scope="module")
def compute_stages_body(inspector_src: str) -> str:
    """Extract the ``_computeStages`` function body."""
    m = re.search(
        r"function\s+_computeStages\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, "_computeStages not found"
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
    assert end is not None
    return inspector_src[m.start() : end + 1]


@pytest.fixture(scope="module")
def eta_body(inspector_src: str) -> str:
    """Extract the ``_computeEta`` function body."""
    m = re.search(
        r"function\s+_computeEta\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, "_computeEta not found"
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
    assert end is not None
    return inspector_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. Card / wrapper DOM structure
# ---------------------------------------------------------------------------

def test_progress_card_class(timeline_body: str) -> None:
    """The dashboard must be wrapped in a ``progress-card`` div so it
    can be styled as a distinct panel."""
    assert "progress-card" in timeline_body, (
        "_renderProgressTimeline must create a 'progress-card' wrapper div"
    )


def test_progress_card_uses_div(timeline_body: str) -> None:
    """The card wrapper must be a ``<div>`` element."""
    assert re.search(
        r'el\(\s*"div"\s*,\s*\{[^}]*progress-card',
        timeline_body,
        flags=re.DOTALL,
    ), (
        "progress-card must be built via el(\"div\", { class: 'progress-card' })"
    )


def test_progress_overall_section(timeline_body: str) -> None:
    """A ``progress-overall`` div must wrap the overall label and bar."""
    assert "progress-overall" in timeline_body, (
        "_renderProgressTimeline must include a 'progress-overall' section"
    )


def test_progress_stage_row_replaces_bullet_list(timeline_body: str) -> None:
    """Stage rows must use the ``progress-stage-row`` class instead of
    the old ``timeline-item`` class."""
    assert "progress-stage-row" in timeline_body, (
        "_renderProgressTimeline must use 'progress-stage-row' for stage rows"
    )
    assert "timeline-item" not in timeline_body, (
        "_renderProgressTimeline must not use the old 'timeline-item' class — "
        "it has been replaced by 'progress-stage-row'"
    )


def test_progress_stage_row_uses_status_template(timeline_body: str) -> None:
    """Stage rows must derive their modifier via a template literal."""
    assert re.search(
        r"progress-stage-row\s+progress-stage-row--\$\{stage\.status\}",
        timeline_body,
    ), (
        "stage rows must use class "
        "``progress-stage-row progress-stage-row--${stage.status}``"
    )


# ---------------------------------------------------------------------------
# 2. State badges
# ---------------------------------------------------------------------------

def test_progress_stage_badge_class(timeline_body: str) -> None:
    """Each stage row must include a ``progress-stage-badge`` element
    so operators can read the state at a glance."""
    assert "progress-stage-badge" in timeline_body, (
        "_renderProgressTimeline must include a 'progress-stage-badge' element "
        "per stage row"
    )


def test_badge_uses_status_template(timeline_body: str) -> None:
    """The badge modifier class must also be driven by ``stage.status``."""
    assert re.search(
        r"progress-stage-badge--\$\{stage\.status\}",
        timeline_body,
    ), (
        "badge must use class 'progress-stage-badge--${stage.status}'"
    )


@pytest.mark.parametrize("badge_text", [
    "Complete",
    "In progress",
    "Pending",
    "Waiting",
    "Failed",
])
def test_badge_text_present(inspector_src: str, badge_text: str) -> None:
    """Each badge label must appear as a quoted string somewhere in the
    file (inside ``_BADGE_LABELS`` or equivalent) so the DOM text is
    correct for every status."""
    assert f'"{badge_text}"' in inspector_src or f"'{badge_text}'" in inspector_src, (
        f"badge text \"{badge_text}\" must appear in job_status.js"
    )


def test_badge_labels_object_declared(inspector_src: str) -> None:
    """A ``_BADGE_LABELS`` mapping must be declared to keep the badge
    text → status mapping in one place."""
    assert "_BADGE_LABELS" in inspector_src, (
        "job_status.js must declare _BADGE_LABELS for badge text mapping"
    )


# ---------------------------------------------------------------------------
# 3. User-facing stage labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Submitted",
    "Researching company",
    "Finding contacts",
    "Assessing needs",
    "Preparing briefing",
    "Awaiting approval",
    "Assembling brief",
    "Exporting PDF",
    "Exporting DOCX",
    "Delivery-ready",
])
def test_stage_label_present(inspector_src: str, label: str) -> None:
    """Each user-facing stage label must appear as a quoted string in
    the source so the DOM text matches the spec."""
    assert f'"{label}"' in inspector_src or f"'{label}'" in inspector_src, (
        f"_TIMELINE_STAGES must include the label \"{label}\""
    )


# ---------------------------------------------------------------------------
# 4. Virtual stages and _PAST_APPROVAL_STATES
# ---------------------------------------------------------------------------

def test_past_approval_states_declared(inspector_src: str) -> None:
    """``_PAST_APPROVAL_STATES`` must be declared so the approval virtual
    stage can determine its done condition without hard-coding state
    names in multiple places."""
    assert "_PAST_APPROVAL_STATES" in inspector_src, (
        "job_status.js must declare _PAST_APPROVAL_STATES"
    )


def test_past_approval_states_contains_generating(inspector_src: str) -> None:
    """``_PAST_APPROVAL_STATES`` must include ``"generating_documents"``
    so the approval gate shows done once doc generation begins."""
    assert '"generating_documents"' in inspector_src, (
        '_PAST_APPROVAL_STATES must include "generating_documents"'
    )


def test_past_approval_states_contains_complete(inspector_src: str) -> None:
    """``_PAST_APPROVAL_STATES`` must include ``"complete"``."""
    assert '"complete"' in inspector_src, (
        '_PAST_APPROVAL_STATES must include "complete"'
    )


def test_virtual_submitted_in_timeline_stages(inspector_src: str) -> None:
    """The ``_TIMELINE_STAGES`` array must include a stage with
    ``virtual: "submitted"`` so the Submitted row is always done."""
    assert '"submitted"' in inspector_src, (
        "_TIMELINE_STAGES must define the submitted virtual stage"
    )


def test_virtual_approval_in_timeline_stages(inspector_src: str) -> None:
    """The ``_TIMELINE_STAGES`` array must include a stage with
    ``virtual: "approval"`` so the Awaiting approval row is driven by
    state rather than an artefact."""
    assert '"approval"' in inspector_src, (
        "_TIMELINE_STAGES must define the approval virtual stage"
    )


def test_compute_stages_uses_past_approval_states(
    compute_stages_body: str,
) -> None:
    """``_computeStages`` must check ``_PAST_APPROVAL_STATES`` to
    determine the done state for the approval virtual stage."""
    assert "_PAST_APPROVAL_STATES" in compute_stages_body, (
        "_computeStages must use _PAST_APPROVAL_STATES for the approval gate"
    )


# ---------------------------------------------------------------------------
# 5. ETA de-duplication for shared artefact keys
# ---------------------------------------------------------------------------

def test_eta_deduplicates_keys(eta_body: str) -> None:
    """``_computeEta`` must track already-counted artefact keys to avoid
    inflating the estimate when multiple stages share the same key
    (e.g. Exporting PDF and Exporting DOCX both map to document_manifest)."""
    assert re.search(
        r"counted|countedKeys|seen",
        eta_body,
    ), (
        "_computeEta must de-duplicate artefact keys to avoid double-counting "
        "stages that share the same key"
    )


def test_eta_skips_virtual_stages(eta_body: str) -> None:
    """``_computeEta`` must skip virtual stages (submitted, approval) since
    they represent overhead or operator time, not machine duration."""
    assert re.search(
        r"stage\.virtual|!stage\.virtual|virtual\s*!==",
        eta_body,
    ), (
        "_computeEta must skip virtual stages when computing the duration estimate"
    )


# ---------------------------------------------------------------------------
# 6. Step 44 features retained
# ---------------------------------------------------------------------------

def test_eta_label_retained(timeline_body: str) -> None:
    """The Step 44 ETA label must remain present."""
    assert "Estimated time remaining:" in timeline_body, (
        "ETA label must not be removed"
    )


def test_updated_caveat_wording(timeline_body: str) -> None:
    """The caveat must use the updated wording referencing 'completed
    outputs' and 'live agent telemetry' rather than the old Step 44 phrasing,
    to better communicate to non-technical operators."""
    assert "completed outputs" in timeline_body, (
        "caveat must mention 'completed outputs'"
    )
    assert "live agent telemetry" in timeline_body, (
        "caveat must mention 'live agent telemetry'"
    )


# ---------------------------------------------------------------------------
# 7. No fake precision
# ---------------------------------------------------------------------------

def test_no_per_stage_percentage(timeline_body: str) -> None:
    """The renderer must not show per-stage percentage figures — only
    the overall integer percentage from _computeOverallProgress."""
    pct_count = timeline_body.count("%")
    assert pct_count <= 2, (
        f"_renderProgressTimeline must not introduce per-stage percentages; "
        f"found {pct_count} '%' occurrences (expected ≤2 for overall label)"
    )


# ---------------------------------------------------------------------------
# 8. No forbidden references
# ---------------------------------------------------------------------------

def test_no_websocket(timeline_body: str) -> None:
    assert "WebSocket" not in timeline_body


def test_no_eventsource(timeline_body: str) -> None:
    assert "EventSource" not in timeline_body


def test_no_setinterval(inspector_src: str) -> None:
    assert "setInterval" not in inspector_src, (
        "job_status.js must not use setInterval"
    )


def test_no_api_calls_in_renderer(timeline_body: str) -> None:
    api_calls = re.findall(r"api\.(\w+)\(", timeline_body)
    assert not api_calls, (
        f"_renderProgressTimeline must not call api.* methods; found: {api_calls}"
    )


_FORBIDDEN = (
    "M365", "m365", "Graph", "gmail", "Gmail",
    "smtp", "SMTP", "sendMail", "backend.delivery",
    "anthropic", "Anthropic", "keyring", "keychain",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_in_renderer(timeline_body: str, needle: str) -> None:
    assert needle not in timeline_body, (
        f"_renderProgressTimeline must not reference {needle!r}"
    )
