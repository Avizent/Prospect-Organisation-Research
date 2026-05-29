"""Step 42 — frontend Generate Documents one-click workflow.

Content scans only — the project has no JS test runner. These tests
pin the structural shape of the ``_onGenerateDocuments`` handler and
the "Generate Documents" button in ``frontend/js/screens/job_status.js``.

Coverage:
* the button is gated by ``snapshot.current_state === "approved"``
* the handler calls the four pipeline steps in strict order:
    api.runStage2 → api.assembleBrief → api.runPdfExport → api.runDocxExport
* each step updates a visible progress label before awaiting
* the success path navigates to ``#/jobs/{id}/manifest``
* any step failure stops the chain, re-enables the button, and shows
  a human-readable error
* 401 redirects to login; 503 surfaces the runtime-disabled message
* the handler never references M365, delivery, email, or any system
  outside the standard API client wrappers
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
def handler_body(inspector_src: str) -> str:
    """Extract the ``_onGenerateDocuments`` function body (inclusive of
    opening and closing braces) by brace-matching from the function
    declaration, scoping all assertions to that function alone.
    """
    m = re.search(
        r"async\s+function\s+_onGenerateDocuments\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, (
        "_onGenerateDocuments not found in job_status.js — "
        "Step 42 implementation is missing"
    )
    start = m.end() - 1  # opening {
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
    assert end is not None, "could not find closing brace of _onGenerateDocuments"
    return inspector_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. Button presence and element type
# ---------------------------------------------------------------------------

def test_button_label_present(inspector_src: str) -> None:
    """The literal label ``Generate Documents`` must appear so the
    operator can locate the control on the approved-job screen."""
    assert 'text: "Generate Documents"' in inspector_src, (
        "inspector must declare a button with text 'Generate Documents'"
    )


def test_button_construction_uses_button_element(inspector_src: str) -> None:
    """The control must be a ``<button>`` element (not an anchor) so
    it is keyboard-activatable and can carry the disabled state the
    handler sets while the pipeline is in flight."""
    assert re.search(
        r'el\(\s*"button"\s*,\s*\{[^}]*text:\s*"Generate Documents"',
        inspector_src,
        flags=re.DOTALL,
    ), (
        "Generate Documents control must be built via "
        'el("button", { ... }) — an anchor cannot carry disabled state'
    )


def test_button_label_appears_exactly_once(inspector_src: str) -> None:
    """A second ``text: "Generate Documents"`` literal would indicate
    an ungated copy of the button, defeating the approved-state gate."""
    assert inspector_src.count('text: "Generate Documents"') == 1, (
        "expected exactly one Generate Documents label literal in the file"
    )


# ---------------------------------------------------------------------------
# 2. State gate — button only for approved jobs
# ---------------------------------------------------------------------------

def test_button_gated_by_approved_state(inspector_src: str) -> None:
    """The button-building branch must sit inside an ``if`` that checks
    ``snapshot.current_state === "approved"`` so the control is
    invisible for all other job states."""
    gate_pattern = re.compile(
        r'if\s*\(\s*snapshot\.current_state\s*===\s*"approved"\s*\)'
    )
    gate_match = gate_pattern.search(inspector_src)
    assert gate_match is not None, (
        "inspector must gate the Generate Documents button on "
        'snapshot.current_state === "approved"'
    )
    label_idx = inspector_src.index('text: "Generate Documents"')
    assert gate_match.start() < label_idx, (
        "the approved-state gate must appear before (i.e. enclose) "
        "the Generate Documents button construction"
    )


# ---------------------------------------------------------------------------
# 3. Pipeline steps — correct api methods called inside handler
# ---------------------------------------------------------------------------

def test_handler_calls_runStage2(handler_body: str) -> None:
    """Stage 2 must be the first pipeline step."""
    assert "api.runStage2(" in handler_body, (
        "_onGenerateDocuments must call api.runStage2()"
    )


def test_handler_calls_assembleBrief(handler_body: str) -> None:
    """Brief assembly must run after Stage 2 completes."""
    assert "api.assembleBrief(" in handler_body, (
        "_onGenerateDocuments must call api.assembleBrief()"
    )


def test_handler_calls_runPdfExport(handler_body: str) -> None:
    """PDF export must run after brief assembly."""
    assert "api.runPdfExport(" in handler_body, (
        "_onGenerateDocuments must call api.runPdfExport()"
    )


def test_handler_calls_runDocxExport(handler_body: str) -> None:
    """DOCX export must be the final export step."""
    assert "api.runDocxExport(" in handler_body, (
        "_onGenerateDocuments must call api.runDocxExport()"
    )


def test_pipeline_calls_in_correct_order(handler_body: str) -> None:
    """The four pipeline steps must appear in the documented source
    order: runStage2 → assembleBrief → runPdfExport → runDocxExport.

    This is a structural proof that the ``await`` chain executes in
    the right sequence — a transposed call would produce incorrect
    results (e.g. DOCX before the markdown is assembled).
    """
    positions = {
        "runStage2":     handler_body.index("api.runStage2("),
        "assembleBrief": handler_body.index("api.assembleBrief("),
        "runPdfExport":  handler_body.index("api.runPdfExport("),
        "runDocxExport": handler_body.index("api.runDocxExport("),
    }
    assert positions["runStage2"] < positions["assembleBrief"], (
        "api.runStage2 must appear before api.assembleBrief in the handler"
    )
    assert positions["assembleBrief"] < positions["runPdfExport"], (
        "api.assembleBrief must appear before api.runPdfExport in the handler"
    )
    assert positions["runPdfExport"] < positions["runDocxExport"], (
        "api.runPdfExport must appear before api.runDocxExport in the handler"
    )


def test_handler_does_not_call_extra_api_methods(handler_body: str) -> None:
    """The handler must call only the four pipeline api.* methods.

    Any additional ``api.*`` invocation would reach outside the
    documented pipeline into state-machine or approval edges.
    """
    all_calls = re.findall(r"api\.(\w+)\(", handler_body)
    allowed = {"runStage2", "assembleBrief", "runPdfExport", "runDocxExport"}
    extra = [name for name in all_calls if name not in allowed]
    assert not extra, (
        f"_onGenerateDocuments must only call the four pipeline "
        f"api.* methods; found unexpected call(s): {extra}"
    )


# ---------------------------------------------------------------------------
# 4. Success path — navigate to manifest viewer
# ---------------------------------------------------------------------------

def test_success_navigates_to_manifest_viewer(handler_body: str) -> None:
    """On success the handler must navigate to ``#/jobs/{id}/manifest``
    so the operator lands on the export download screen."""
    assert re.search(
        r"navigate\(\s*`#/jobs/\$\{[^}]+\}/manifest`\s*\)",
        handler_body,
    ), (
        "_onGenerateDocuments must navigate to #/jobs/{id}/manifest "
        "on success"
    )


def test_success_navigate_comes_after_all_exports(handler_body: str) -> None:
    """The manifest redirect must be the very last operation — it must
    appear after the ``api.runDocxExport`` call in the source."""
    docx_pos = handler_body.index("api.runDocxExport(")
    nav_match = re.search(
        r"navigate\(\s*`#/jobs/\$\{[^}]+\}/manifest`\s*\)",
        handler_body,
    )
    assert nav_match is not None
    assert docx_pos < nav_match.start(), (
        "manifest redirect must appear after api.runDocxExport() — "
        "navigate must be the final step in the success path"
    )


# ---------------------------------------------------------------------------
# 5. Progress labels — each step has a human-readable in-flight label
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Running Stage 2\u2026",   # … (U+2026) as stored in the JS source
    "Assembling brief\u2026",
    "Exporting PDF\u2026",
    "Exporting DOCX\u2026",
    "Complete",
])
def test_progress_label_present(handler_body: str, label: str) -> None:
    """Each pipeline step must update the status region with a
    progress message so the operator can track which step is in
    flight without watching the network tab."""
    assert label in handler_body, (
        f"_onGenerateDocuments must display the progress label {label!r}"
    )


def test_button_text_updated_per_step(handler_body: str) -> None:
    """The button label itself must reflect the current step so an
    operator can see progress even without looking at the status
    region."""
    assert handler_body.count("button.textContent =") >= 4, (
        "handler must update button.textContent at each pipeline step "
        "(at least 4 assignments — one per step)"
    )


# ---------------------------------------------------------------------------
# 6. Error handling
# ---------------------------------------------------------------------------

def test_failure_reenables_button(handler_body: str) -> None:
    """Any step failure must re-enable the button so the operator
    can retry without reloading the page."""
    assert "button.disabled = false" in handler_body, (
        "failure path must set button.disabled = false for retry"
    )


def test_button_disabled_on_click(handler_body: str) -> None:
    """The handler must disable the button at the start of the run
    to prevent double-submission."""
    assert "button.disabled = true" in handler_body, (
        "handler must disable the button while the pipeline is in flight"
    )


def test_401_redirects_to_login(handler_body: str) -> None:
    """A 401 at any pipeline step must redirect to the login screen,
    matching the inspector's existing auth-redirect pattern."""
    assert re.search(
        r'navigate\(\s*"#/login"\s*\)',
        handler_body,
    ), (
        "_onGenerateDocuments must redirect to #/login on 401"
    )


def test_503_branch_present(handler_body: str) -> None:
    """A 503 (Stage 2 runtime disabled) must be handled explicitly
    so the operator sees the runtime-disabled message rather than a
    generic HTTP error."""
    assert re.search(
        r"err\.status\s*===\s*503",
        handler_body,
    ), (
        "_onGenerateDocuments must branch on HTTP 503 to surface the "
        "Stage 2 runtime-disabled state clearly"
    )


def test_503_does_not_rethrow(handler_body: str) -> None:
    """The 503 branch must exit cleanly — it must not re-throw."""
    m = re.search(
        r"err\.status\s*===\s*503[^}]{0,400}",
        handler_body,
        flags=re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "return" in body, "503 branch must exit with a bare return"
    assert "throw" not in body, "503 branch must not re-throw"


def test_error_surfaces_http_status(handler_body: str) -> None:
    """Generic API errors must include the HTTP status code so the
    operator can correlate the failure with the backend log."""
    assert re.search(
        r"HTTP\s*\$\{?err\.status\}?",
        handler_body,
    ), (
        "generic ApiError branch must include err.status in the "
        "user-visible error message"
    )


# ---------------------------------------------------------------------------
# 7. Status region accessibility
# ---------------------------------------------------------------------------

def test_status_node_has_aria_live(inspector_src: str) -> None:
    """The generate-docs status node must be a polite ARIA live region
    so a screen reader announces progress and error copy without
    yanking keyboard focus."""
    assert re.search(
        r'el\(\s*"div"\s*,\s*\{[^}]*'
        r'class:\s*"gen-docs-status"[^}]*'
        r'role:\s*"status"[^}]*'
        r'"aria-live":\s*"polite"',
        inspector_src,
        flags=re.DOTALL,
    ), (
        'Generate Documents status node must have '
        'role="status" and aria-live="polite"'
    )


def test_status_node_appended_to_container(inspector_src: str) -> None:
    """The ``generateDocsStatus`` node must be added to the DOM (either via
    container.appendChild or via the pageChildren array that is appended to
    the container) so it appears when the button is clicked."""
    assert "generateDocsStatus" in inspector_src, (
        "generateDocsStatus variable must be declared and appended"
    )
    # Accept either the direct-append pattern or the page-wrapper pattern.
    appended = (
        "container.appendChild(generateDocsStatus)" in inspector_src
        or "pageChildren.push(generateDocsStatus)" in inspector_src
    )
    assert appended, (
        "generateDocsStatus must be added to the DOM — either via "
        "container.appendChild(generateDocsStatus) or "
        "pageChildren.push(generateDocsStatus)"
    )


# ---------------------------------------------------------------------------
# 8. No M365 / delivery / email references inside the handler
# ---------------------------------------------------------------------------

_FORBIDDEN_IN_HANDLER = (
    "delivery",
    "m365",
    "M365",
    "email",
    "smtp",
    "outlook",
    "keychain",
    "anthropic",
    "append_transition",
    "write_state",
    "record_failure",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_IN_HANDLER)
def test_no_forbidden_in_handler(handler_body: str, needle: str) -> None:
    """The one-click handler must not reference any out-of-scope
    system. Step 42 is pure frontend pipeline wiring — no delivery,
    no email, no Keychain, no Anthropic SDK."""
    assert needle not in handler_body.lower(), (
        f"_onGenerateDocuments must not reference {needle!r}; "
        "Step 42 is frontend-only pipeline wiring"
    )
