"""Step 27 — frontend Run Stage 2 control.

Content scans only — the project has no JS test runner. The tests
pin the structural shape of the Stage 2 run button + handler in
``frontend/js/screens/job_status.js`` so a future edit that
silently broke any of the following would fail here:

* the button is gated by ``snapshot.current_state === "approved"``,
* the handler calls ``api.runStage2(...)`` (Step 27 ``api.js``
  wrapper) with both ``knowledge_bundle`` and ``user_context``,
* a 201 success path triggers a re-render (so newly-available
  Stage 2 artefact links appear automatically via the existing
  generic loop),
* a 503 response surfaces a documented runtime-disabled message,
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

    A future polish step may add the Stage 2 button copy ("Run Stage
    2") to a comment for human reference; the "absence of state-machine
    strings in code" assertions must look past comments.
    """
    src = _JOB_STATUS_JS.read_text(encoding="utf-8")
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


# ---------------------------------------------------------------------------
# 1. A Run Stage 2 button/control is present for approved jobs
# ---------------------------------------------------------------------------

def test_button_label_present(inspector_src: str) -> None:
    """The literal label ``Run Stage 2`` must appear in the inspector
    so operators can locate the control on the screen."""
    assert 'text: "Run Stage 2"' in inspector_src, (
        "inspector must declare a button with text 'Run Stage 2' "
        "so the operator can trigger Stage 2 from the job screen"
    )


def test_button_construction_uses_button_element(inspector_src: str) -> None:
    """The control must be an actual ``<button>`` element (not an
    anchor), so it is keyboard-activatable and obeys the disabled
    semantics the handler relies on."""
    assert re.search(
        r'el\(\s*"button"\s*,\s*\{[^}]*text:\s*"Run Stage 2"',
        inspector_src,
        flags=re.DOTALL,
    ), (
        "Run Stage 2 control must be built via el(\"button\", { ... }) "
        "— an anchor cannot carry the disabled state the handler sets"
    )


# ---------------------------------------------------------------------------
# 2. The button is gated by approved state — absent for non-approved jobs
# ---------------------------------------------------------------------------

def test_button_gated_by_approved_state(inspector_src: str) -> None:
    """The button-building branch must be inside an ``if`` that checks
    ``snapshot.current_state === "approved"``.

    We assert the gate appears *before* the button-label literal and at
    a strictly lower line number, which is the structural proof that
    the construction is inside that branch.
    """
    gate_pattern = re.compile(
        r'if\s*\(\s*snapshot\.current_state\s*===\s*"approved"\s*\)'
    )
    gate_match = gate_pattern.search(inspector_src)
    assert gate_match is not None, (
        "inspector must gate the Stage 2 button on "
        'snapshot.current_state === "approved" so the control does '
        "not appear for non-approved jobs"
    )
    label_idx = inspector_src.index('text: "Run Stage 2"')
    assert gate_match.start() < label_idx, (
        "the approved-state gate must appear before (i.e. enclose) "
        "the button construction"
    )


def test_button_label_appears_exactly_once(inspector_src: str) -> None:
    """A second ``text: "Run Stage 2"`` literal would indicate an
    ungated copy of the button somewhere — defeat the gating
    invariant the previous test pins."""
    assert inspector_src.count('text: "Run Stage 2"') == 1, (
        "expected exactly one Run Stage 2 label literal"
    )


# ---------------------------------------------------------------------------
# 3. The handler calls POST /api/jobs/{id}/stage2/run via api.runStage2
# ---------------------------------------------------------------------------

def test_handler_calls_api_runStage2(inspector_src: str) -> None:
    """The handler must dispatch through the api.js wrapper, not via a
    raw ``fetch(...)``. This keeps the same-origin / cookie / error
    normalisation logic centralised in api.js."""
    assert "api.runStage2(" in inspector_src, (
        "Stage 2 handler must call api.runStage2(...) — going around "
        "the API client would bypass shared error handling"
    )


def test_api_wrapper_targets_stage2_run_route(api_src: str) -> None:
    """The api.js wrapper must POST to ``/api/jobs/{id}/stage2/run``."""
    assert "runStage2:" in api_src, "api.runStage2 wrapper missing"
    m = re.search(
        r'runStage2:\s*\([^)]*\)\s*=>\s*request\("POST",\s*'
        r'`/api/jobs/\$\{[^}]+\}/stage2/run`',
        api_src,
    )
    assert m is not None, (
        "api.runStage2 must POST to /api/jobs/{id}/stage2/run via "
        "the request() helper"
    )


# ---------------------------------------------------------------------------
# 4. The body sends knowledge_bundle (and the optional user_context)
# ---------------------------------------------------------------------------

def test_handler_sends_knowledge_bundle(inspector_src: str) -> None:
    """The Stage 2 route requires ``knowledge_bundle``; the handler
    must pass one."""
    assert "knowledge_bundle:" in inspector_src, (
        "Stage 2 handler must send a knowledge_bundle field"
    )


def test_handler_sends_user_context(inspector_src: str) -> None:
    """Optional but specified in the Step 27 scope — keep parity."""
    assert "user_context:" in inspector_src, (
        "Stage 2 handler must include the user_context field"
    )


def test_knowledge_bundle_marker_present(inspector_src: str) -> None:
    """The fake-safe marker string the smoke test relies on must be
    present verbatim so operators can correlate frontend-triggered
    runs in any future log scan."""
    assert (
        "Netropy 100G — fake runtime smoke bundle from frontend."
        in inspector_src
    ), (
        "the documented fake-safe knowledge_bundle string must be "
        "carried verbatim into the request body"
    )


# ---------------------------------------------------------------------------
# 5. 201 success refreshes the job snapshot
# ---------------------------------------------------------------------------

def test_success_path_rerenders_snapshot(inspector_src: str) -> None:
    """On success the handler must re-invoke ``render(container,
    params)`` so the newly-available Stage 2 artefact links appear via
    the existing generic loop. Inlining the artefact list update would
    duplicate state with the renderer and drift over time.
    """
    # The handler must call render(container, params).
    assert re.search(
        r"render\(\s*container\s*,\s*params\s*\)",
        inspector_src,
    ), (
        "Stage 2 success path must call render(container, params) to "
        "refresh the snapshot and pick up new artefacts via the "
        "generic available_artefacts loop"
    )


def test_success_path_surfaces_stages_completed(inspector_src: str) -> None:
    """A user-visible signal of which Stage 2 stages ran must reach the
    toast/banner. We pin the ``stages_completed`` field access — the
    backend response shape's source of truth."""
    assert "stages_completed" in inspector_src, (
        "Stage 2 handler must reference response.stages_completed so "
        "the success message reflects what actually ran"
    )


# ---------------------------------------------------------------------------
# 6. 503 is handled as "runtime disabled", not a crash
# ---------------------------------------------------------------------------

def test_503_branch_present(inspector_src: str) -> None:
    """The handler must branch on ``err.status === 503`` so the
    documented runtime-disabled state is visible to the operator
    rather than surfacing as a generic error."""
    assert re.search(
        r"err\.status\s*===\s*503",
        inspector_src,
    ), (
        "Stage 2 handler must explicitly branch on HTTP 503 so the "
        "runtime-disabled state surfaces with the right message"
    )


def test_503_branch_uses_disabled_message(inspector_src: str) -> None:
    """The disabled-runtime message must mention both the disabled
    state and the env-var name an operator needs to flip to enable
    the fake runtime."""
    assert (
        "Stage 2 runtime is disabled"
        in inspector_src
    ), (
        "the 503 branch must surface a clear runtime-disabled "
        "message"
    )
    assert "ANS_ENABLE_FAKE_STAGE2_RUNTIME=1" in inspector_src, (
        "the 503 branch must point the operator at the env-var that "
        "enables the fake runtime"
    )


def test_503_branch_does_not_throw(inspector_src: str) -> None:
    """The 503 branch must not re-throw, navigate away, or otherwise
    treat the response as a crash. We assert the branch contains a
    ``return`` (the handler exits cleanly after surfacing the
    message) and does NOT contain a ``throw`` in the same arm.
    """
    # Locate the 503 branch and check the immediate follow-up has a
    # bare `return;` and no `throw`.
    m = re.search(
        r"err\.status\s*===\s*503[^}]{0,400}",
        inspector_src,
        flags=re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "return" in body, (
        "503 branch must exit with a bare return — re-throwing would "
        "treat a documented disabled state as an unhandled error"
    )
    assert "throw" not in body, (
        "503 branch must not re-throw — runtime-disabled is a "
        "documented state, not an error"
    )


# ---------------------------------------------------------------------------
# 7. Other errors surface the HTTP status and a short message
# ---------------------------------------------------------------------------

def test_other_error_branch_includes_status(inspector_src: str) -> None:
    """A non-503 ApiError must surface the status code so the operator
    can correlate it with a backend log."""
    assert re.search(
        r"HTTP\s*\$\{?err\.status\}?",
        inspector_src,
    ), (
        "the generic ApiError branch must include err.status in the "
        "user-visible message"
    )


# ---------------------------------------------------------------------------
# 8. No document generation, M365, delivery, Keychain, Anthropic,
#    new backend route, or state-machine references
# ---------------------------------------------------------------------------

_STATE_MACHINE_FORBIDDEN = (
    "generating_documents",
    "complete",
    "failed",
    "generate_document",
    "documents/",
    ".docx",
    ".pdf",
    ".eml",
    ".msg",
)


@pytest.mark.parametrize("needle", _STATE_MACHINE_FORBIDDEN)
def test_no_document_generation_or_state_drift(
    code_only_inspector_src: str, needle: str,
) -> None:
    """The Step 27 control must not hard-code document generation,
    delivery artefacts, or downstream state names.

    Comments are stripped so a docstring that mentions "complete" in
    prose does not trip the fence; only references in *code* count.
    The next test still guards literal copy in code separately.
    """
    forbidden_literals = (f'"{needle}"', f"'{needle}'")
    for literal in forbidden_literals:
        assert literal not in code_only_inspector_src, (
            f"inspector code must not contain {literal} — Step 27 "
            f"adds a Stage 2 run trigger only, not document "
            f"generation or state-machine drift"
        )


_FORBIDDEN_SYSTEMS = (
    "anthropic",
    "Anthropic",
    "keyring",
    "Keyring",
    "keychain",
    "Keychain",
    "M365",
    "m365",
    "Microsoft Graph",
    "microsoft graph",
    "CloudClient",
    "cloud_client",
    "backend.delivery",
    "backend.assembly",
    "backend.orchestrator",
    "backend.credentials",
    "sk-ant",
)


@pytest.mark.parametrize("needle", _FORBIDDEN_SYSTEMS)
def test_inspector_does_not_reference_forbidden_systems(
    inspector_src: str, needle: str,
) -> None:
    """Defence-in-depth alongside ``tests/frontend/test_no_leakage.py``
    — the Step 27 control must not introduce any Anthropic, Keychain,
    M365, delivery, assembly, or orchestrator reference."""
    assert needle not in inspector_src, (
        f"inspector must not reference {needle!r}; Step 27 is "
        f"frontend-only, no production system bindings"
    )


@pytest.mark.parametrize("needle", _FORBIDDEN_SYSTEMS)
def test_api_client_does_not_reference_forbidden_systems(
    api_src: str, needle: str,
) -> None:
    """Same fence for the api.js wrapper — the Step 27 ``runStage2``
    wrapper must stay a thin POST against an existing route."""
    assert needle not in api_src, (
        f"api.js must not reference {needle!r}; Step 27 adds only a "
        f"runStage2 wrapper for the existing route"
    )


def test_handler_does_not_call_any_other_state_changing_route(
    inspector_src: str,
) -> None:
    """The Stage 2 handler must not call any other state-changing
    backend route (e.g. approval edges, document generation). Only
    ``api.runStage2`` should fire from the handler.

    We assert by extracting the slice of source between the handler
    declaration and the corresponding closing brace, then checking no
    other ``api.<methodName>(`` call appears inside.
    """
    m = re.search(
        r"async\s+function\s+_onRunStage2\s*\([^)]*\)\s*\{",
        inspector_src,
    )
    assert m is not None, (
        "expected an async function named _onRunStage2 — the Step 27 "
        "handler entry point"
    )
    # Naive but safe: walk braces from the opening one.
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
    assert end is not None, "could not find closing brace of _onRunStage2"
    body = inspector_src[start:end]
    # Allow api.runStage2; ban every other api.* method invocation.
    other_api_calls = re.findall(r"api\.(\w+)\(", body)
    extra = [name for name in other_api_calls if name != "runStage2"]
    assert not extra, (
        f"Stage 2 handler must not call other api.* methods; found: "
        f"{extra}. Only api.runStage2(...) is allowed inside the "
        f"handler — refresh runs through render(container, params)."
    )
