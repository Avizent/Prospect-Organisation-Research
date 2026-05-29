"""Step 44 — job progress timeline in the job status screen.

Content scans only — the project has no JS test runner. These tests pin
the structural shape of the progress-timeline code added to
``frontend/js/screens/job_status.js``.

Coverage:
* ``_renderProgressTimeline``, ``_computeStages``, and ``_computeEta``
  functions are present
* ``_ACTIVE_STATES`` contains exactly the three running states
* polling uses ``setTimeout`` (not ``setInterval``)
* the navigation guard (``_timelineGeneration``) is present and checked
  inside the polling closure
* the timeline section carries the ``job-progress-timeline`` class
* an approximate ETA line is rendered for active jobs
* an honest-approximation caveat is present
* the panel contains no api.* calls and no POST literal
* no forbidden delivery/system references inside the rendering function
* ``_renderProgressTimeline`` is called from ``render()``
* ``_POLL_INTERVAL_MS`` constant is defined
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
    brace-matching so assertions are scoped to the new timeline code."""
    m = re.search(
        r"function\s+_renderProgressTimeline\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, (
        "_renderProgressTimeline not found in job_status.js — "
        "Step 44 implementation is missing"
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
    assert end is not None, (
        "could not find closing brace of _renderProgressTimeline"
    )
    return inspector_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. Required functions present
# ---------------------------------------------------------------------------

def test_render_progress_timeline_present(inspector_src: str) -> None:
    """``_renderProgressTimeline`` must be declared in the file."""
    assert "_renderProgressTimeline" in inspector_src, (
        "job_status.js must declare _renderProgressTimeline"
    )


def test_compute_stages_present(inspector_src: str) -> None:
    """``_computeStages`` must be declared — it is the artefact-to-stage
    inference function that drives the timeline display."""
    assert "_computeStages" in inspector_src, (
        "job_status.js must declare _computeStages"
    )


def test_compute_eta_present(inspector_src: str) -> None:
    """``_computeEta`` must be declared — it produces the approximate
    time-remaining estimate shown below the timeline."""
    assert "_computeEta" in inspector_src, (
        "job_status.js must declare _computeEta"
    )


# ---------------------------------------------------------------------------
# 2. Active states set
# ---------------------------------------------------------------------------

def test_active_states_contains_created(inspector_src: str) -> None:
    """``_ACTIVE_STATES`` must include ``"created"`` so a brand-new
    job shows the polling timeline immediately."""
    assert '"created"' in inspector_src, (
        '_ACTIVE_STATES must include "created"'
    )


def test_active_states_contains_researching(inspector_src: str) -> None:
    """``_ACTIVE_STATES`` must include ``"researching"``."""
    assert '"researching"' in inspector_src, (
        '_ACTIVE_STATES must include "researching"'
    )


def test_active_states_contains_generating_documents(
    inspector_src: str,
) -> None:
    """``_ACTIVE_STATES`` must include ``"generating_documents"`` so the
    Generate Documents pipeline (Step 42) is also covered by the
    timeline."""
    assert '"generating_documents"' in inspector_src, (
        '_ACTIVE_STATES must include "generating_documents"'
    )


# ---------------------------------------------------------------------------
# 3. Polling mechanics
# ---------------------------------------------------------------------------

def test_poll_uses_set_timeout_not_interval(inspector_src: str) -> None:
    """Polling must use ``setTimeout`` rather than ``setInterval`` so a
    slow fetch cannot queue a second in-flight request."""
    assert "setTimeout" in inspector_src, (
        "job_status.js must use setTimeout for polling"
    )
    assert "setInterval" not in inspector_src, (
        "job_status.js must not use setInterval — setTimeout prevents "
        "concurrent in-flight requests"
    )


def test_poll_interval_constant_defined(inspector_src: str) -> None:
    """``_POLL_INTERVAL_MS`` must be defined as the polling cadence so
    the interval can be adjusted in one place."""
    assert "_POLL_INTERVAL_MS" in inspector_src, (
        "job_status.js must declare _POLL_INTERVAL_MS"
    )


def test_timeline_generation_guard_declared(inspector_src: str) -> None:
    """``_timelineGeneration`` must be declared at module scope as the
    navigation guard counter."""
    assert "_timelineGeneration" in inspector_src, (
        "job_status.js must declare _timelineGeneration as a "
        "navigation guard for the polling closure"
    )


def test_generation_guard_checked_in_poll(inspector_src: str) -> None:
    """The polling closure must compare ``_timelineGeneration`` to the
    captured ``generation`` value so stale ticks are dropped after
    navigation."""
    assert re.search(
        r"_timelineGeneration\s*!==\s*generation",
        inspector_src,
    ), (
        "polling closure must check _timelineGeneration !== generation "
        "to discard ticks that arrive after navigation"
    )


def test_generation_counter_incremented_in_render(
    inspector_src: str,
) -> None:
    """``render()`` must increment ``_timelineGeneration`` at call start
    so each navigation cancels the previous polling loop."""
    assert "++_timelineGeneration" in inspector_src, (
        "render() must increment _timelineGeneration at call start"
    )


def test_polling_gated_on_active_state(inspector_src: str) -> None:
    """The ``setTimeout`` call must be inside a branch that checks
    ``_ACTIVE_STATES.has(snapshot.current_state)`` so terminal jobs
    do not poll indefinitely."""
    assert re.search(
        r"_ACTIVE_STATES\.has\(\s*snapshot\.current_state\s*\)",
        inspector_src,
    ), (
        "setTimeout polling must be gated on "
        "_ACTIVE_STATES.has(snapshot.current_state)"
    )


# ---------------------------------------------------------------------------
# 4. DOM structure
# ---------------------------------------------------------------------------

def test_timeline_section_class(timeline_body: str) -> None:
    """The timeline container must carry the ``job-progress-timeline``
    class so CSS and accessibility tooling can target it."""
    assert "job-progress-timeline" in timeline_body, (
        "_renderProgressTimeline must create a section with "
        "class 'job-progress-timeline'"
    )


def test_timeline_aria_label(timeline_body: str) -> None:
    """The section must carry an ``aria-label`` for screen readers."""
    assert re.search(
        r'"aria-label":\s*"Job progress"',
        timeline_body,
    ), (
        "_renderProgressTimeline section must have "
        'aria-label="Job progress"'
    )


def test_timeline_heading_present(timeline_body: str) -> None:
    """A ``Progress`` heading must be present so operators can locate
    the panel quickly."""
    assert '"Progress"' in timeline_body, (
        "_renderProgressTimeline must include a heading 'Progress'"
    )


def test_progress_timeline_list_class(timeline_body: str) -> None:
    """The stage list must carry the ``progress-timeline`` class."""
    assert "progress-timeline" in timeline_body, (
        "_renderProgressTimeline must create a ul with "
        "class 'progress-timeline'"
    )


# ---------------------------------------------------------------------------
# 5. ETA and honest-approximation caveat
# ---------------------------------------------------------------------------

def test_eta_text_pattern_present(timeline_body: str) -> None:
    """The ETA line must be prefixed with ``Estimated time remaining:``
    so operators understand the figure is an estimate, not a countdown."""
    assert "Estimated time remaining:" in timeline_body, (
        "_renderProgressTimeline must include 'Estimated time remaining:' "
        "for active jobs"
    )


def test_eta_fallback_calculating(timeline_body: str) -> None:
    """When no stages are pending the ETA falls back to ``calculating…``
    so the label is never empty."""
    assert "calculating…" in timeline_body, (
        "_renderProgressTimeline must display 'calculating…' when ETA "
        "cannot be determined"
    )


def test_honest_approximation_caveat_present(timeline_body: str) -> None:
    """An explicit caveat must state that progress is inferred from
    artefact availability, not live agent events, so operators are not
    misled about the precision of the display."""
    assert "inferred from completed outputs" in timeline_body, (
        "_renderProgressTimeline must include the honest-approximation "
        "caveat about progress being inferred from completed outputs"
    )


# ---------------------------------------------------------------------------
# 6. Read-only — no api.* calls, no POST
# ---------------------------------------------------------------------------

def test_timeline_makes_no_api_calls(timeline_body: str) -> None:
    """The timeline renderer must not call any api.* method — it is a
    pure display function."""
    api_calls = re.findall(r"api\.(\w+)\(", timeline_body)
    assert not api_calls, (
        f"_renderProgressTimeline must not call any api.* methods; "
        f"found: {api_calls}"
    )


def test_timeline_contains_no_post_literal(timeline_body: str) -> None:
    """No ``'POST'`` or ``"POST"`` literal may appear in the renderer."""
    assert '"POST"' not in timeline_body, (
        "_renderProgressTimeline must not contain a \"POST\" literal"
    )
    assert "'POST'" not in timeline_body, (
        "_renderProgressTimeline must not contain a 'POST' literal"
    )


# ---------------------------------------------------------------------------
# 7. No forbidden delivery/system references
# ---------------------------------------------------------------------------

_FORBIDDEN_TIMELINE_TERMS = (
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
    "anthropic",
    "Anthropic",
    "keyring",
    "keychain",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_TIMELINE_TERMS)
def test_no_forbidden_in_timeline(timeline_body: str, needle: str) -> None:
    """The progress timeline must not reference any external delivery
    or credential system."""
    assert needle not in timeline_body, (
        f"_renderProgressTimeline must not reference {needle!r}; "
        "Step 44 is a read-only display function"
    )


# ---------------------------------------------------------------------------
# 8. Wired into render()
# ---------------------------------------------------------------------------

def test_render_progress_timeline_called(inspector_src: str) -> None:
    """``_renderProgressTimeline`` must be called from ``render()`` so
    the timeline actually appears in the DOM."""
    assert "_renderProgressTimeline(" in inspector_src, (
        "_renderProgressTimeline must be called from render()"
    )
