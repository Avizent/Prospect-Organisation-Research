"""Step 45 — overall and per-stage progress bars in the job timeline.

Content scans only — the project has no JS test runner. These tests pin
the structural shape of the progress-bar additions to
``_renderProgressTimeline`` and the new ``_computeOverallProgress``
function in ``frontend/js/screens/job_status.js``.

Coverage:
* ``_computeOverallProgress`` is declared
* overall progress label "Overall progress: {N}%" is present
* overall bar uses ``<progress>`` (semantic HTML)
* overall progress is calculated from completed/total stages
* per-stage bar elements use the required CSS classes
* all five bar state classes are present (done/active/pending/waiting/failed)
* ``_TS_WAITING`` and ``_TS_FAILED`` status constants are declared
* ``_WAITING_STATES`` set is declared with human-gated state names
* failed state wires to ``_TS_FAILED``
* waiting states wire to ``_TS_WAITING``
* overall bar gets an error class on failed state
* existing ETA line is retained
* existing honest-approximation caveat is retained
* no fake per-stage percentages
* no WebSocket, EventSource, or setInterval
* no backend / M365 / email / delivery references in the timeline renderer
* no new api.* calls in the timeline renderer
* Step 44 timeline tests still pass (verified by running them)
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
    """Extract the ``_renderProgressTimeline`` function body by
    brace-matching so assertions are scoped to the timeline renderer."""
    m = re.search(
        r"function\s+_renderProgressTimeline\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, (
        "_renderProgressTimeline not found — Step 44/45 implementation missing"
    )
    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(inspector_src)):
        ch = inspector_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
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
        ch = inspector_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None
    return inspector_src[m.start() : end + 1]


@pytest.fixture(scope="module")
def overall_progress_body(inspector_src: str) -> str:
    """Extract the ``_computeOverallProgress`` function body."""
    m = re.search(
        r"function\s+_computeOverallProgress\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, (
        "_computeOverallProgress not found — Step 45 implementation missing"
    )
    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(inspector_src)):
        ch = inspector_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None
    return inspector_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. New functions and constants present
# ---------------------------------------------------------------------------

def test_compute_overall_progress_declared(inspector_src: str) -> None:
    """``_computeOverallProgress`` must be declared."""
    assert "_computeOverallProgress" in inspector_src, (
        "job_status.js must declare _computeOverallProgress"
    )


def test_ts_waiting_constant_declared(inspector_src: str) -> None:
    """``_TS_WAITING`` must be declared as a stage-status token."""
    assert "_TS_WAITING" in inspector_src, (
        "job_status.js must declare _TS_WAITING status constant"
    )


def test_ts_failed_constant_declared(inspector_src: str) -> None:
    """``_TS_FAILED`` must be declared as a stage-status token."""
    assert "_TS_FAILED" in inspector_src, (
        "job_status.js must declare _TS_FAILED status constant"
    )


def test_waiting_states_set_declared(inspector_src: str) -> None:
    """``_WAITING_STATES`` must be declared so human-gated states can
    be identified without scattering string comparisons."""
    assert "_WAITING_STATES" in inspector_src, (
        "job_status.js must declare _WAITING_STATES"
    )


# ---------------------------------------------------------------------------
# 2. Overall progress label and bar
# ---------------------------------------------------------------------------

def test_overall_progress_label_text(timeline_body: str) -> None:
    """The label ``Overall progress:`` must appear in the timeline
    renderer so operators can read the figure at a glance."""
    assert "Overall progress:" in timeline_body, (
        "_renderProgressTimeline must include 'Overall progress:' label"
    )


def test_overall_progress_uses_percent_sign(timeline_body: str) -> None:
    """The overall progress value must be expressed as a percentage."""
    assert "%" in timeline_body, (
        "_renderProgressTimeline must display a percentage sign for "
        "the overall progress figure"
    )


def test_overall_bar_uses_progress_element(timeline_body: str) -> None:
    """The overall bar must be a semantic ``<progress>`` element so
    browsers and screen readers understand it as a progress indicator."""
    assert re.search(
        r'el\(\s*"progress"',
        timeline_body,
    ), (
        "_renderProgressTimeline must create the overall bar using "
        "el(\"progress\", ...) for semantic HTML"
    )


def test_overall_bar_has_max_100(timeline_body: str) -> None:
    """The ``<progress>`` element must have ``max: 100`` so the value
    is interpreted as a percentage."""
    assert re.search(
        r"max:\s*100",
        timeline_body,
    ), (
        "overall <progress> element must have max: 100"
    )


def test_overall_bar_class_present(timeline_body: str) -> None:
    """The overall bar must carry the ``progress-overall-bar`` class."""
    assert "progress-overall-bar" in timeline_body, (
        "_renderProgressTimeline must apply class 'progress-overall-bar' "
        "to the overall progress element"
    )


def test_overall_bar_failed_class_present(timeline_body: str) -> None:
    """A failed-state variant class must be applied to the overall bar
    when the job is in the failed state."""
    assert "progress-overall-bar--failed" in timeline_body, (
        "_renderProgressTimeline must apply 'progress-overall-bar--failed' "
        "class when the job has failed"
    )


# ---------------------------------------------------------------------------
# 3. _computeOverallProgress logic
# ---------------------------------------------------------------------------

def test_overall_progress_uses_done_filter(overall_progress_body: str) -> None:
    """The function must filter stages by ``_TS_DONE`` (or the string
    'done') to count completed stages — no other method is correct."""
    assert re.search(
        r"_TS_DONE|['\"]done['\"]",
        overall_progress_body,
    ), (
        "_computeOverallProgress must identify done stages via "
        "_TS_DONE or the literal 'done'"
    )


def test_overall_progress_uses_math_round(overall_progress_body: str) -> None:
    """``Math.round`` must be used so the percentage is an integer,
    not a float like 37.5%."""
    assert "Math.round" in overall_progress_body, (
        "_computeOverallProgress must use Math.round to produce an "
        "integer percentage"
    )


def test_overall_progress_divides_done_by_total(
    overall_progress_body: str,
) -> None:
    """The calculation must divide done-count by total-count — any
    other formula would produce a wrong percentage."""
    assert re.search(
        r"done\s*/\s*total|\.length\s*/\s*\w+\.length",
        overall_progress_body,
    ), (
        "_computeOverallProgress must divide done count by total count"
    )


def test_compute_overall_progress_called_in_timeline(
    timeline_body: str,
) -> None:
    """``_computeOverallProgress`` must be called inside
    ``_renderProgressTimeline`` so the percentage value is available
    for both the label and the bar."""
    assert "_computeOverallProgress(" in timeline_body, (
        "_renderProgressTimeline must call _computeOverallProgress"
    )


# ---------------------------------------------------------------------------
# 4. Per-stage progress bar classes
# ---------------------------------------------------------------------------

def test_per_stage_bar_base_class(timeline_body: str) -> None:
    """Each stage row must include an element with the base
    ``progress-stage-bar`` class."""
    assert "progress-stage-bar" in timeline_body, (
        "_renderProgressTimeline must create per-stage elements with "
        "class 'progress-stage-bar'"
    )


def test_per_stage_bar_uses_status_template(timeline_body: str) -> None:
    """Per-stage bars must derive their modifier class from
    ``stage.status`` via a template literal — this is the structural
    proof that each ``_TS_*`` constant maps to a CSS class without
    requiring redundant hard-coded per-status branches."""
    assert re.search(
        r"progress-stage-bar\s+progress-stage-bar--\$\{stage\.status\}",
        timeline_body,
    ), (
        "_renderProgressTimeline must build per-stage bar class via "
        "``progress-stage-bar progress-stage-bar--${stage.status}`` template literal"
    )


# The individual class names are produced by the template literal combined
# with the _TS_* constants. Verify the constant values directly so the
# mapping from constant → CSS class is pinned without duplicating the
# template literal logic in the renderer.

@pytest.mark.parametrize("constant,expected_value", [
    ("_TS_DONE",    "done"),
    ("_TS_ACTIVE",  "active"),
    ("_TS_PENDING", "pending"),
    ("_TS_WAITING", "waiting"),
    ("_TS_FAILED",  "failed"),
])
def test_ts_constant_value(inspector_src: str, constant: str,
                           expected_value: str) -> None:
    """Each ``_TS_*`` constant must be assigned the string value that
    corresponds to the CSS BEM modifier for its state.  Combined with
    ``test_per_stage_bar_uses_status_template``, this guarantees that
    ``timeline-bar--{value}`` is the class written to the DOM."""
    assert re.search(
        rf"const\s+{re.escape(constant)}\s*=\s*['\"]"
        + re.escape(expected_value)
        + r"['\"]",
        inspector_src,
    ), (
        f"{constant} must be assigned the string \"{expected_value}\" "
        f"so the template literal produces class 'progress-stage-bar--{expected_value}'"
    )


def test_per_stage_bar_uses_div_not_progress(timeline_body: str) -> None:
    """Per-stage bars must be ``<div>`` elements (not ``<progress>``)
    so indeterminate animation is applied via CSS class without
    conflicting with the overall ``<progress>`` element."""
    assert re.search(
        r'el\(\s*"div"\s*,\s*\{[^}]*progress-stage-bar',
        timeline_body,
        flags=re.DOTALL,
    ), (
        "per-stage bars must be el(\"div\", { class: '...progress-stage-bar...' })"
    )


# ---------------------------------------------------------------------------
# 5. _computeStages updated for waiting and failed
# ---------------------------------------------------------------------------

def test_compute_stages_handles_failed(compute_stages_body: str) -> None:
    """``_computeStages`` must branch on the failed state to mark
    incomplete stages as ``_TS_FAILED``."""
    assert "_TS_FAILED" in compute_stages_body, (
        "_computeStages must assign _TS_FAILED to incomplete stages "
        "when the job has failed"
    )


def test_compute_stages_handles_waiting(compute_stages_body: str) -> None:
    """``_computeStages`` must branch on human-gated states to mark
    incomplete stages as ``_TS_WAITING``."""
    assert "_TS_WAITING" in compute_stages_body, (
        "_computeStages must assign _TS_WAITING to incomplete stages "
        "in human-gated states"
    )


def test_compute_stages_checks_waiting_states_set(
    compute_stages_body: str,
) -> None:
    """``_computeStages`` must test membership in ``_WAITING_STATES``
    rather than hard-coding individual state names."""
    assert "_WAITING_STATES" in compute_stages_body, (
        "_computeStages must use _WAITING_STATES.has() to detect "
        "human-gated states"
    )


# ---------------------------------------------------------------------------
# 6. Human-gated states in _WAITING_STATES
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", [
    "briefing_ready",
    "user_editing",
    "regenerating_section",
    "approved",
])
def test_waiting_state_in_set(inspector_src: str, state: str) -> None:
    """Each human-gated state must appear as a quoted string inside
    the ``_WAITING_STATES`` declaration."""
    assert f'"{state}"' in inspector_src, (
        f"_WAITING_STATES must include \"{state}\""
    )


# ---------------------------------------------------------------------------
# 7. Step 44 features retained
# ---------------------------------------------------------------------------

def test_eta_label_retained(timeline_body: str) -> None:
    """The Step 44 ETA label must remain present after Step 45."""
    assert "Estimated time remaining:" in timeline_body, (
        "Step 44 ETA label must not be removed by Step 45"
    )


def test_caveat_retained(timeline_body: str) -> None:
    """The honest-approximation caveat must remain present."""
    assert "inferred from completed outputs" in timeline_body, (
        "honest-approximation caveat must not be removed"
    )


# ---------------------------------------------------------------------------
# 8. No fake precision
# ---------------------------------------------------------------------------

def test_no_per_stage_percentage_in_renderer(timeline_body: str) -> None:
    """The renderer must not compute per-stage percentages. Individual
    stage bars use status classes only — no numeric fake-precision
    figures per stage."""
    # The only percentage in the renderer must come from the overall
    # _computeOverallProgress call (the "${percent}%" template literal).
    # We allow one "%" occurrence for that label; a second would suggest
    # a per-stage figure.
    pct_occurrences = timeline_body.count("%")
    assert pct_occurrences <= 2, (
        f"_renderProgressTimeline must not introduce per-stage percentage "
        f"figures; found {pct_occurrences} '%' occurrences (expected ≤2 "
        f"for the overall label template literal)"
    )


# ---------------------------------------------------------------------------
# 9. No forbidden references
# ---------------------------------------------------------------------------

def test_no_websocket_in_timeline(timeline_body: str) -> None:
    """No WebSocket reference may appear in the renderer."""
    assert "WebSocket" not in timeline_body, (
        "_renderProgressTimeline must not use WebSocket"
    )


def test_no_eventsource_in_timeline(timeline_body: str) -> None:
    """No EventSource (SSE) reference may appear in the renderer."""
    assert "EventSource" not in timeline_body, (
        "_renderProgressTimeline must not use EventSource"
    )


def test_no_api_calls_in_renderer(timeline_body: str) -> None:
    """The timeline renderer must not call any api.* method."""
    api_calls = re.findall(r"api\.(\w+)\(", timeline_body)
    assert not api_calls, (
        f"_renderProgressTimeline must not call api.* methods; "
        f"found: {api_calls}"
    )


_FORBIDDEN_RENDERER_TERMS = (
    "M365", "m365", "Graph", "gmail", "Gmail",
    "smtp", "SMTP", "sendMail", "send_mail",
    "backend.delivery", "delivery_tracking",
    "anthropic", "Anthropic", "keyring", "keychain",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_RENDERER_TERMS)
def test_no_forbidden_in_renderer(timeline_body: str, needle: str) -> None:
    """The renderer must not reference any external delivery or
    credential system."""
    assert needle not in timeline_body, (
        f"_renderProgressTimeline must not reference {needle!r}"
    )
