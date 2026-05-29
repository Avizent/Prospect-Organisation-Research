"""Step 33 — frontend Assemble Brief control.

Content scans only — the project has no JS test runner. These tests
pin the structural shape of the Assemble Brief button + handler in
``frontend/js/screens/job_status.js`` and the ``assembleBrief``
wrapper in ``frontend/js/api.js`` so a future edit that silently
broke any of the following would fail here:

* the button is gated by ``snapshot.current_state === "approved"``
  AND by the presence of the ``critic_report`` Stage 2 artefact,
* the button label distinguishes first-time assembly
  ("Assemble Brief") from re-assembly ("Re-assemble Brief"),
* the handler calls ``api.assembleBrief(...)`` (Step 33 ``api.js``
  wrapper) which targets ``POST /api/jobs/{id}/brief/assemble``
  with ``returnStatus: true`` so the toast copy can distinguish
  201 from 200,
* the success path triggers a re-render (so the newly-available
  ``prospect_brief`` artefact row + "View Brief" link appear via
  the existing generic ``available_artefacts`` loop) and does NOT
  auto-navigate to the brief viewer,
* the 409 branches surface the ``state_not_approved`` and
  ``missing_critic_report`` reasons,
* the failure path re-enables the button so the operator can retry,
* the screen does not hard-code document generation, M365,
  delivery, Anthropic, Keychain, orchestrator, or state-machine
  transitions.
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JOB_STATUS_JS = (
    _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"
)
_API_JS = _REPO_ROOT / "frontend" / "js" / "api.js"


@pytest.fixture(scope="module")
def inspector_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code_only_inspector_src() -> str:
    """Inspector source with block + line comments stripped.

    The forbidden-strings scan must look past comments — the module
    docstring legitimately documents what the screen does NOT do.
    """
    src = _JOB_STATUS_JS.read_text(encoding="utf-8")
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


# ---------------------------------------------------------------------------
# 1. An Assemble Brief button/control is present (label variants)
# ---------------------------------------------------------------------------

def test_button_label_assemble_present(inspector_src: str) -> None:
    """The literal label ``Assemble Brief`` (first-time) must appear."""
    assert '"Assemble Brief"' in inspector_src, (
        "inspector must declare a button with text 'Assemble Brief' "
        "so the operator can trigger assembly from the job screen"
    )


def test_button_label_reassemble_present(inspector_src: str) -> None:
    """The literal label ``Re-assemble Brief`` (idempotent regeneration)
    must appear — the label flips based on whether ``prospect_brief``
    is already present."""
    assert '"Re-assemble Brief"' in inspector_src, (
        "inspector must declare a 'Re-assemble Brief' label variant "
        "for the idempotent regeneration case"
    )


def test_button_construction_uses_button_element(
    code_only_inspector_src: str,
) -> None:
    """The control must be an actual ``<button>`` element (not an
    anchor), so it is keyboard-activatable and obeys the disabled
    semantics the handler relies on."""
    assert re.search(
        r'el\(\s*"button"\s*,\s*\{[^}]*text:\s*'
        r'(alreadyAssembled\s*\?\s*"Re-assemble Brief"\s*:\s*"Assemble Brief"'
        r'|"Assemble Brief")',
        code_only_inspector_src,
        flags=re.DOTALL,
    ), (
        "Assemble Brief control must be built via el(\"button\", { ... }) "
        "— an anchor cannot carry the disabled state the handler sets"
    )


# ---------------------------------------------------------------------------
# 2. The button is gated by approved state AND critic_report presence
# ---------------------------------------------------------------------------

def test_button_gated_by_approved_state(
    code_only_inspector_src: str,
) -> None:
    """The button-building branch must be inside an ``if`` that checks
    ``snapshot.current_state === "approved"``.

    The Stage 2 button and the Assemble Brief button share that gate.
    """
    gate_pattern = re.compile(
        r'if\s*\(\s*snapshot\.current_state\s*===\s*"approved"\s*\)'
    )
    gate_match = gate_pattern.search(code_only_inspector_src)
    assert gate_match is not None, (
        "inspector must gate the Assemble Brief button on "
        'snapshot.current_state === "approved" so the control does '
        "not appear for non-approved jobs"
    )
    label_idx = code_only_inspector_src.index('"Assemble Brief"')
    assert gate_match.start() < label_idx, (
        "the approved-state gate must appear before (i.e. enclose) "
        "the Assemble Brief button construction"
    )


def test_button_gated_by_critic_report_present(
    code_only_inspector_src: str,
) -> None:
    """The button must additionally be gated on the ``critic_report``
    artefact being present on disk — the backend would 409 otherwise
    and we don't render a button the operator can never use.
    """
    # We require a structural reference to ``artefacts.critic_report``
    # in the inspector code (not comments), appearing BEFORE the
    # Assemble Brief label literal — that proves the gate encloses
    # the construction.
    pattern = re.compile(
        r'artefacts\.critic_report'
    )
    match = pattern.search(code_only_inspector_src)
    assert match is not None, (
        "inspector must reference artefacts.critic_report as a gate "
        "on the Assemble Brief button — the backend route 409s if "
        "critic_report.json is absent"
    )
    label_idx = code_only_inspector_src.index('"Assemble Brief"')
    assert match.start() < label_idx, (
        "the critic_report gate must appear before the Assemble Brief "
        "button construction"
    )


def test_button_label_appears_exactly_once_each(
    code_only_inspector_src: str,
) -> None:
    """A second literal of either label in CODE (not comments) would
    indicate an ungated copy of the button somewhere — defeating the
    gating invariants the previous tests pin."""
    assert code_only_inspector_src.count('"Assemble Brief"') == 1, (
        "expected exactly one 'Assemble Brief' label literal in code"
    )
    assert code_only_inspector_src.count('"Re-assemble Brief"') == 1, (
        "expected exactly one 'Re-assemble Brief' label literal in code"
    )


def test_label_chooses_by_prospect_brief_presence(
    code_only_inspector_src: str,
) -> None:
    """The label flip ("Assemble Brief" vs "Re-assemble Brief") must
    depend on whether ``prospect_brief`` is already an available
    artefact — that is the structural distinction between "Stage 2
    artefacts present" and "assembled Markdown present"."""
    assert "artefacts.prospect_brief" in code_only_inspector_src, (
        "label flip must read artefacts.prospect_brief — that is "
        "the source of truth for whether the brief has already been "
        "assembled"
    )


# ---------------------------------------------------------------------------
# 3. The handler calls POST /api/jobs/{id}/brief/assemble via api.assembleBrief
# ---------------------------------------------------------------------------

def test_handler_calls_api_assembleBrief(inspector_src: str) -> None:
    """The handler must dispatch through the api.js wrapper, not via a
    raw ``fetch(...)``. This keeps the same-origin / cookie / error
    normalisation logic centralised in api.js."""
    assert "api.assembleBrief(" in inspector_src, (
        "Assemble Brief handler must call api.assembleBrief(...) — "
        "going around the API client would bypass shared error handling"
    )


def test_api_wrapper_targets_brief_assemble_route(api_src: str) -> None:
    """The api.js wrapper must POST to ``/api/jobs/{id}/brief/assemble``."""
    assert "assembleBrief:" in api_src, "api.assembleBrief wrapper missing"
    m = re.search(
        r'assembleBrief:\s*\([^)]*\)\s*=>\s*request\(\s*"POST"\s*,\s*'
        r'`/api/jobs/\$\{[^}]+\}/brief/assemble`',
        api_src,
    )
    assert m is not None, (
        "api.assembleBrief must POST to /api/jobs/{id}/brief/assemble "
        "via the request() helper"
    )


def test_api_wrapper_uses_return_status(api_src: str) -> None:
    """The wrapper must request status-aware return so the inspector
    can distinguish 201 ("Brief assembled") from 200
    ("Brief re-assembled") in the success toast."""
    m = re.search(
        r"assembleBrief:[\s\S]*?returnStatus:\s*true",
        api_src,
    )
    assert m is not None, (
        "api.assembleBrief must pass returnStatus: true so the "
        "handler can branch on 201 vs 200 for toast copy"
    )


def test_request_helper_supports_return_status(api_src: str) -> None:
    """The shared ``request()`` helper must accept ``returnStatus`` and
    return ``{ status, body }`` when it is true. Without this, the
    Step 33 toast distinction is impossible."""
    assert "returnStatus" in api_src, (
        "request() helper must accept a returnStatus option"
    )
    assert re.search(
        r"if\s*\(\s*returnStatus\s*\)\s*\{\s*"
        r"return\s*\{\s*status:\s*res\.status,\s*body\s*\}",
        api_src,
    ), (
        "request() helper must return { status, body } when "
        "returnStatus is true"
    )


# ---------------------------------------------------------------------------
# 4. Loading state — button disabled, label swapped
# ---------------------------------------------------------------------------

def test_handler_disables_button_on_click(inspector_src: str) -> None:
    """On click the handler must disable the button so a double-click
    can't fire two POSTs."""
    assert "button.disabled = true" in inspector_src, (
        "Assemble Brief handler must disable the button while in flight"
    )


def test_handler_swaps_label_during_loading(inspector_src: str) -> None:
    """The label must reflect the in-flight state."""
    assert '"Assembling…"' in inspector_src, (
        "handler must swap the button label to 'Assembling…' while "
        "the POST is in flight"
    )


def test_status_node_has_aria_live(inspector_src: str) -> None:
    """The assembly status node must be a polite live region so a
    screen reader announces success/failure copy without yanking
    focus."""
    assert re.search(
        r'el\(\s*"div"\s*,\s*\{[^}]*'
        r'class:\s*"assembly-status"[^}]*'
        r'role:\s*"status"[^}]*'
        r'"aria-live":\s*"polite"',
        inspector_src,
        flags=re.DOTALL,
    ), (
        "Assemble Brief handler must render its status node with "
        'role="status" and aria-live="polite"'
    )


# ---------------------------------------------------------------------------
# 5. Success path — distinguish 201 vs 200, re-render, NO auto-navigate
# ---------------------------------------------------------------------------

def test_success_path_branches_on_201(inspector_src: str) -> None:
    """The handler must branch on ``result.status === 201`` so first
    assembly and re-assembly produce different copy."""
    assert re.search(
        r"result\.status\s*===\s*201",
        inspector_src,
    ), (
        "Assemble Brief handler must branch on result.status === 201 "
        "to distinguish first assembly from re-assembly"
    )


def test_success_toast_first_assembly_copy(inspector_src: str) -> None:
    """The 201 path must show 'Brief assembled' copy."""
    assert '"Brief assembled"' in inspector_src, (
        "201 success path must surface 'Brief assembled' copy"
    )


def test_success_toast_reassembly_copy(inspector_src: str) -> None:
    """The 200 path must show 'Brief re-assembled' copy."""
    assert '"Brief re-assembled"' in inspector_src, (
        "200 success path must surface 'Brief re-assembled' copy"
    )


def test_success_path_references_markdown_sha256(inspector_src: str) -> None:
    """A signal from the response body must reach the success path so
    the toast carries something operators can grep against the
    manifest."""
    assert "markdown_sha256" in inspector_src, (
        "Assemble Brief handler must reference result.body.markdown_sha256 "
        "so the success toast carries a content-integrity signal"
    )


def test_success_path_rerenders_snapshot(inspector_src: str) -> None:
    """On success the handler must re-invoke ``render(container,
    params)`` so the newly-available ``prospect_brief`` artefact row
    and its "View Brief" link appear via the existing generic loop."""
    # Constrain the assertion to the assembly handler block so a future
    # change to _onRunStage2 cannot accidentally satisfy this test.
    match = re.search(
        r"async function _onAssembleBrief[\s\S]*?\n\}\n",
        inspector_src,
    )
    assert match is not None, (
        "_onAssembleBrief handler not found in inspector source"
    )
    handler_body = match.group(0)
    assert re.search(
        r"render\(\s*container\s*,\s*params\s*\)",
        handler_body,
    ), (
        "Assemble Brief success path must call render(container, params) "
        "to refresh the snapshot and pick up the new prospect_brief row"
    )


def test_handler_does_not_navigate_to_viewer(inspector_src: str) -> None:
    """The success path must NOT auto-navigate to the brief viewer.
    The operator stays on the inspector page after a successful
    assembly; the "View Brief" link is the operator's explicit
    next step."""
    match = re.search(
        r"async function _onAssembleBrief[\s\S]*?\n\}\n",
        inspector_src,
    )
    assert match is not None
    handler_body = match.group(0)
    # The handler may still navigate("#/login") on a 401 — that is
    # the auth-redirect, not the brief-viewer redirect. We forbid
    # navigating to any hash route under #/jobs/.
    forbidden = re.findall(
        r'navigate\(\s*[`"]#/jobs/',
        handler_body,
    )
    assert not forbidden, (
        "Assemble Brief handler must not auto-navigate to the brief "
        f"viewer — found: {forbidden}"
    )


def test_view_brief_link_appears_after_successful_assembly(
    inspector_src: str,
) -> None:
    """Structural proof that a successful assembly + re-render
    surfaces both the ``prospect_brief`` artefact row and the
    "View Brief" link.

    Three structural conditions must hold simultaneously:

    1. The handler's success path calls ``render(container, params)``
       (asserted in detail by ``test_success_path_rerenders_snapshot``).
    2. The renderer's ``available_artefacts`` loop recognises
       ``prospect_brief`` as a special-case key (the "display
       artefact" branch from Step 31).
    3. That branch emits a "View Brief" link.

    If any of the three break, the operator will trigger assembly,
    see a success toast, but never see the "View Brief" link — the
    user-visible outcome the test is named for.
    """
    # Condition 1 — success path re-renders.
    match = re.search(
        r"async function _onAssembleBrief[\s\S]*?\n\}\n",
        inspector_src,
    )
    assert match is not None
    handler_body = match.group(0)
    assert re.search(
        r"render\(\s*container\s*,\s*params\s*\)",
        handler_body,
    ), (
        "successful assembly must re-invoke render(container, params) "
        "so the next snapshot picks up the prospect_brief artefact row"
    )
    # Condition 2 — renderer special-cases the prospect_brief key.
    assert 'name === "prospect_brief"' in inspector_src, (
        "renderer must special-case the 'prospect_brief' key inside "
        "the available_artefacts loop so the artefact row links into "
        "the in-app brief viewer rather than opening raw JSON"
    )
    # Condition 3 — renderer emits the 'View Brief' link text.
    assert '"View Brief"' in inspector_src, (
        "renderer must emit the literal 'View Brief' link copy when "
        "the prospect_brief artefact is present"
    )


# ---------------------------------------------------------------------------
# 6. Failure paths — button re-enabled, structured error reasons surfaced
# ---------------------------------------------------------------------------

def test_failure_path_reenables_button(inspector_src: str) -> None:
    """A failure must re-enable the button so the operator can retry."""
    match = re.search(
        r"async function _onAssembleBrief[\s\S]*?\n\}\n",
        inspector_src,
    )
    assert match is not None
    handler_body = match.group(0)
    assert "button.disabled = false" in handler_body, (
        "failure paths in the Assemble Brief handler must re-enable "
        "the button so the operator can retry"
    )


def test_failure_branches_on_state_not_approved(inspector_src: str) -> None:
    """The 409 ``state_not_approved`` reason must be surfaced
    explicitly so the operator knows why the call failed."""
    assert '"state_not_approved"' in inspector_src, (
        "handler must branch on the 'state_not_approved' reason"
    )


def test_failure_branches_on_missing_critic_report(
    inspector_src: str,
) -> None:
    """The 409 ``missing_critic_report`` reason must be surfaced
    explicitly so the operator is directed to re-run Stage 2."""
    assert '"missing_critic_report"' in inspector_src, (
        "handler must branch on the 'missing_critic_report' reason"
    )


def test_handler_redirects_to_login_on_401(inspector_src: str) -> None:
    """A 401 must redirect to the login screen, matching the
    inspector's existing auth-redirect pattern."""
    match = re.search(
        r"async function _onAssembleBrief[\s\S]*?\n\}\n",
        inspector_src,
    )
    assert match is not None
    handler_body = match.group(0)
    assert re.search(
        r'navigate\(\s*"#/login"\s*\)',
        handler_body,
    ), (
        "Assemble Brief handler must redirect to #/login on a 401, "
        "matching the inspector's existing auth-redirect pattern"
    )


# ---------------------------------------------------------------------------
# 7. Hard-rule fences — no forbidden integrations in the inspector
# ---------------------------------------------------------------------------

_FORBIDDEN_SUBSTRINGS = (
    "delivery",
    "m365",
    "outlook",
    "keychain",
    "anthropic",
    "append_transition",
    "write_state",
    "record_failure",
    "smtp",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_strings(
    code_only_inspector_src: str, needle: str,
) -> None:
    """The inspector must not reference any of the forbidden
    integrations. The check runs against comment-stripped source so
    documentation can still mention these names where useful."""
    assert needle not in code_only_inspector_src.lower(), (
        f"inspector code must not reference {needle!r}"
    )


def test_no_local_or_session_storage(inspector_src: str) -> None:
    """Hard rule 6: no localStorage/sessionStorage for any data. The
    session cookie is HttpOnly and JS never reads it."""
    assert "localStorage" not in inspector_src
    assert "sessionStorage" not in inspector_src


def test_assemble_handler_does_not_touch_docx(
    inspector_src: str,
) -> None:
    """The ``_onAssembleBrief`` handler must never reference DOCX
    generation. Step 42 adds DOCX support to the file via a separate
    ``_onGenerateDocuments`` handler; this targeted check confirms the
    assembly handler stays narrowly scoped.

    We extract the ``_onAssembleBrief`` function body via brace
    matching (same technique used by Step 27's equivalent test) so
    Step 42's legitimate DOCX references in ``_onGenerateDocuments``
    do not cause false positives.
    """
    import re as _re
    m = _re.search(
        r"async\s+function\s+_onAssembleBrief\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, "_onAssembleBrief not found in job_status.js"
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
    assert end is not None, "could not find closing brace of _onAssembleBrief"
    body = inspector_src[start:end + 1]
    assert "docx" not in body.lower(), (
        "_onAssembleBrief must not reference 'docx' — DOCX generation "
        "belongs exclusively to the _onGenerateDocuments pipeline handler"
    )
