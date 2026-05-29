"""Step 22 fence — the existing inspector already renders Stage 2 links.

Step 21 added five backend GET routes for Stage 2 artefacts and
extended ``AvailableArtefacts`` with five matching booleans. The
inspector in ``frontend/js/screens/job_status.js`` iterates
``available_artefacts`` generically and converts each key from
``snake_case`` to ``kebab-case`` before composing the artefact URL,
so the five new keys flow through the existing renderer with **zero
runtime-JS changes**.

This module proves that claim by content-scan (the project has no
JS test runner — see ``tests/frontend/conftest.py``):

* The inspector uses ``Object.entries(snapshot.available_artefacts
  || {})`` over the dict — a hard-coded allow-list would defeat the
  generic flow.
* The URL transform is the literal ``name.replace(/_/g, "-")``.
* Each Stage 2 key, run through that transform, names a live
  backend route under ``GET /api/jobs/{job_id}/artefacts/<segment>``.
* The anchor construction stays inside the ``if (present)`` guard,
  so a ``False`` boolean produces no link.
* The renderer does not hard-code any Stage 2 artefact key — adding
  the literal ``"benefits"`` (etc.) to a quoted string in the JS
  source would fail the no-hard-coded-key test below.
* The ``briefing`` special-case branch is intact — ``briefing`` is
  the only key whose backend route does NOT live under
  ``/artefacts/``.
* ``api.js`` did NOT grow Stage 2 wrappers — the inspector links via
  ``<a href>``, so wrappers would be dead code.

Why this layer of fence
-----------------------
A future refactor that switched the inspector to a hard-coded
artefact list (e.g. for ordering) would silently break URL alignment
the moment Stage 2 routes drift. Pinning the transform plus the
absence of hard-coded keys catches that class of regression before
it reaches operators.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.main import app


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_JOB_STATUS_JS = (
    _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"
)
_API_JS = _REPO_ROOT / "frontend" / "js" / "api.js"


# The five new boolean keys Step 21 introduced into AvailableArtefacts.
# In ``snake_case`` form on the wire; the inspector kebab-cases each
# before composing the URL.
_STAGE2_KEYS = (
    "product_mapping",
    "benefits",
    "faq",
    "objections",
    "critic_report",
)


# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def inspector_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code_only_inspector_src() -> str:
    """Inspector source with block and line comments stripped.

    The docstring at the top of ``job_status.js`` now lists the Stage
    2 artefact names for human reference (Step 22 docstring polish).
    Tests that need to assert "no hard-coded Stage 2 key string in the
    *code*" must look past the docstring — comments are not source.
    """
    src = _JOB_STATUS_JS.read_text(encoding="utf-8")
    # Strip /** ... */ and /* ... */ blocks, then // line comments.
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


@pytest.fixture(scope="module")
def artefact_loop_only(code_only_inspector_src: str) -> str:
    """Extract only the ``for (const [name, present] of
    Object.entries(artefacts))`` loop body from the comment-stripped
    source so the no-hard-coded-key fence is scoped to the artefact
    renderer itself.

    Step 44 adds ``_TIMELINE_STAGES`` which legitimately names artefact
    keys as data for the progress timeline — a different component that
    must not trip the artefact-renderer fence.  Scoping to the loop body
    means the guard still catches the specific regression it was written
    to prevent (special-casing an artefact inside the loop) without
    producing false positives from unrelated code that also knows
    artefact names.
    """
    m = re.search(
        r"for\s*\(\s*const\s+\[name,\s*present\]\s+of\s+Object\.entries\(\s*artefacts\s*\)\s*\)\s*\{",
        code_only_inspector_src,
    )
    assert m is not None, (
        "could not find 'for (const [name, present] of Object.entries(artefacts))' "
        "loop in comment-stripped source — artefact renderer may have changed"
    )
    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(code_only_inspector_src)):
        ch = code_only_inspector_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "could not find closing brace of artefact loop"
    return code_only_inspector_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. Generic iteration over available_artefacts
# ---------------------------------------------------------------------------

def test_inspector_iterates_available_artefacts_generically(
    inspector_src: str,
) -> None:
    """Pins the data-driven iteration. A regression that swapped to a
    hard-coded loop (``for (const name of ["dossier", ...])``) would
    miss this exact pattern.

    The inspector takes a local alias of ``snapshot.available_artefacts``
    (with ``|| {}`` defence) and then passes that alias to
    ``Object.entries`` — all three pieces must be present and the
    alias variable used in ``Object.entries`` must match the variable
    bound to ``snapshot.available_artefacts``."""
    # Pin the ``|| {}`` defence — a malformed response that
    # omits available_artefacts must not crash the renderer.
    assert "snapshot.available_artefacts || {}" in inspector_src, (
        "inspector must read snapshot.available_artefacts with a "
        "``|| {}`` fallback to survive malformed responses"
    )
    # Pin the alias assignment and the Object.entries call against
    # that alias — together these prove generic iteration over
    # whatever keys the backend currently exposes.
    m = re.search(
        r"const\s+(\w+)\s*=\s*snapshot\.available_artefacts\s*\|\|\s*\{\}\s*;",
        inspector_src,
    )
    assert m is not None, (
        "inspector must bind snapshot.available_artefacts to a local "
        "alias before iterating"
    )
    alias = m.group(1)
    assert f"Object.entries({alias})" in inspector_src, (
        f"inspector must iterate the available_artefacts alias "
        f"(``{alias}``) via Object.entries to remain generic over "
        "future artefact keys"
    )


# ---------------------------------------------------------------------------
# 2. URL transform is underscore-to-hyphen
# ---------------------------------------------------------------------------

def test_inspector_url_transform_is_underscore_to_hyphen(
    inspector_src: str,
) -> None:
    """The literal ``name.replace(/_/g, "-")`` must appear in the
    renderer. This is the exact transform that turns ``product_mapping``
    into ``product-mapping`` for the backend route segment."""
    assert 'name.replace(/_/g, "-")' in inspector_src, (
        "inspector must use ``name.replace(/_/g, \"-\")`` as the "
        "snake→kebab URL transform; a different transform (e.g. "
        "regex with /__/g, or an explicit table) would drift from "
        "the backend route convention"
    )


# ---------------------------------------------------------------------------
# 3. Each Stage 2 key maps to a live backend route
# ---------------------------------------------------------------------------

def _registered_paths() -> set[str]:
    """The live FastAPI path templates the app exposes."""
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "GET" in methods:
            paths.add(path)
    return paths


@pytest.mark.parametrize("key", _STAGE2_KEYS)
def test_stage2_key_kebab_cases_to_a_live_backend_route(key: str) -> None:
    """For each new Stage 2 boolean key, applying the inspector's JS
    transform (snake → kebab) must produce a URL segment that names a
    live backend GET route.

    If a future rename made the boolean key ``benefit_brief`` while
    the backend route stayed ``/artefacts/benefits``, the inspector
    would build ``/artefacts/benefit-brief`` and 404 — this test
    surfaces that drift before operators see a dead link.
    """
    segment = key.replace("_", "-")
    expected_template = (
        f"/api/jobs/{{job_id}}/artefacts/{segment}"
    )
    paths = _registered_paths()
    assert expected_template in paths, (
        f"inspector would build {segment!r} from {key!r}, but no "
        f"backend GET route exists at {expected_template!r}. "
        f"Registered GET paths under /api/jobs: "
        f"{sorted(p for p in paths if p.startswith('/api/jobs'))}"
    )


# ---------------------------------------------------------------------------
# 4. Anchor stays inside the if (present) guard
# ---------------------------------------------------------------------------

def test_open_json_anchor_is_inside_present_guard(
    inspector_src: str,
) -> None:
    """The 'open JSON' anchor must remain gated by ``if (present)``.

    Hoisting the anchor outside the guard would render dead links
    for missing artefacts — the precise regression this test
    pre-empts. We assert by line-number positioning:

      1. The ``if (present) {`` line index is `g_open`.
      2. The matching closing ``}`` for that block is `g_close`
         (the first standalone ``    }`` after `g_open` at the
         same four-space indent).
      3. The ``text: "open JSON"`` line index is between them.

    Co-locating the structural check with the indent assumption
    makes a future re-indent of the file visible (the test would
    fail and prompt a re-pin) rather than silently weakening the
    fence.
    """
    lines = inspector_src.splitlines()
    g_open = next(
        i for i, line in enumerate(lines)
        if line.strip() == "if (present) {"
    )
    # Match the closing brace at the same indent as the if-line.
    if_indent = len(lines[g_open]) - len(lines[g_open].lstrip())
    g_close: int | None = None
    for j in range(g_open + 1, len(lines)):
        stripped = lines[j].rstrip()
        if stripped == (" " * if_indent + "}"):
            g_close = j
            break
    assert g_close is not None, (
        "could not locate the closing brace of ``if (present) {`` "
        "— file structure changed"
    )
    anchor_line = next(
        i for i, line in enumerate(lines)
        if 'text: "open JSON"' in line
    )
    assert g_open < anchor_line < g_close, (
        f"'open JSON' anchor at line {anchor_line + 1} is not "
        f"inside the if (present) guard "
        f"(lines {g_open + 1}..{g_close + 1})"
    )


def test_missing_artefacts_produce_no_open_json_link(
    inspector_src: str,
) -> None:
    """There is exactly one ``text: "open JSON"`` literal in the
    inspector source — combined with the previous test, this means
    every "open JSON" link goes through the present guard. Two
    occurrences would indicate a second, ungated anchor and
    fail here."""
    occurrences = inspector_src.count('text: "open JSON"')
    assert occurrences == 1, (
        f"expected exactly one 'open JSON' anchor; found "
        f"{occurrences} — a second, ungated anchor would render "
        f"dead links for missing artefacts"
    )


# ---------------------------------------------------------------------------
# 5. No hard-coded Stage 2 artefact keys in the inspector source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", _STAGE2_KEYS)
def test_inspector_does_not_hardcode_stage2_key_in_code(
    artefact_loop_only: str, key: str,
) -> None:
    """No Stage 2 boolean key appears as a quoted string literal
    *inside the artefact-rendering loop* (comments stripped).

    Scoped to the ``for (const [name, present] of Object.entries(...))``
    loop body so Step 44's ``_TIMELINE_STAGES`` (which legitimately
    names artefact keys as data for the progress timeline) does not
    trigger a false positive.  The guard still catches the specific
    regression it was written to prevent: special-casing an artefact
    key inside the renderer loop instead of letting it flow through
    the generic iterator.
    """
    forbidden_literals = (
        f'"{key}"',
        f"'{key}'",
    )
    for literal in forbidden_literals:
        assert literal not in artefact_loop_only, (
            f"artefact renderer loop must not hard-code the Stage 2 key "
            f"{literal} — keep the renderer generic over "
            f"available_artefacts"
        )


def test_inspector_does_not_hardcode_stage1_extra_keys_in_code(
    artefact_loop_only: str,
) -> None:
    """Same fence for Stage 1 keys *other than* ``briefing`` — they
    must also flow through the generic loop, not be hard-coded.

    ``briefing`` is the documented exception: its backend route is
    ``/api/jobs/{id}/briefing`` (not under ``/artefacts/``), so the
    ternary must reference it by name. The other three Stage 1 keys
    have no such exception and must not appear as quoted literals
    inside the loop body.

    Scoped to ``artefact_loop_only`` for the same reason as
    ``test_inspector_does_not_hardcode_stage2_key_in_code`` above.
    """
    for key in ("research_dossier", "contacts", "needs_assessment"):
        for literal in (f'"{key}"', f"'{key}'"):
            assert literal not in artefact_loop_only, (
                f"artefact renderer loop must not hard-code {literal} — "
                f"keep the renderer generic"
            )


# ---------------------------------------------------------------------------
# 6. Briefing special-case route remains intact
# ---------------------------------------------------------------------------

def test_briefing_special_case_branch_present(
    inspector_src: str,
) -> None:
    """``briefing`` is the only artefact whose backend GET lives
    outside ``/artefacts/``. The inspector ternary must still
    reference the key by name and route to ``/briefing`` directly."""
    assert 'name === "briefing"' in inspector_src, (
        "the briefing special-case ternary must remain — its "
        "backend route is /api/jobs/{id}/briefing, not "
        "/api/jobs/{id}/artefacts/briefing"
    )
    assert "/briefing`" in inspector_src, (
        "the briefing route template literal must remain in the "
        "URL construction"
    )
    # Defence-in-depth: the backend route table must agree.
    paths = _registered_paths()
    assert "/api/jobs/{job_id}/briefing" in paths


# ---------------------------------------------------------------------------
# 7. api.js did NOT grow Stage 2 wrappers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "wrapper",
    [
        "getProductMapping",
        "getBenefits",
        "getFAQ",
        "getObjections",
        "getCriticReport",
    ],
)
def test_api_js_did_not_grow_stage2_wrappers(
    api_src: str, wrapper: str,
) -> None:
    """The inspector links via ``<a href>``, not via the API client,
    so adding Stage 2 wrappers in Step 22 would be dead code that
    drifts from the actual call sites.

    A future step that introduces pretty-rendering will need these
    wrappers AND will need to update
    ``tests/frontend/test_api_client.py``'s ``_REQUIRED_METHODS``
    / ``_REQUIRED_PATHS`` lists in lockstep — keeping the two in
    sync is the right time to add the wrappers, not now.
    """
    assert f"{wrapper}:" not in api_src, (
        f"api.js must not gain {wrapper!r} in Step 22 — wrappers "
        f"would be dead code until a future pretty-render step "
        f"consumes them"
    )


# ---------------------------------------------------------------------------
# 8. Inspector remains identical at runtime — docstring-only diff
# ---------------------------------------------------------------------------

def test_inspector_runtime_logic_signature_unchanged(
    inspector_src: str,
) -> None:
    """Pin the exact runtime-JS signatures Step 22 must NOT change.

    Step 22 is a docstring polish plus tests; the runtime JS surface
    must be byte-identical to Step 21 for the listed structural
    fragments. A regression that quietly tweaked the URL construction
    (e.g. switched ``encodeURIComponent`` for raw interpolation, or
    moved the special-case to a lookup table) would fail here.
    """
    expected_fragments = (
        # Import line — proves the imports surface did not drift.
        'import { api, ApiError } from "../api.js";',
        # Generic iteration — proves the loop shape did not change.
        "for (const [name, present] of Object.entries(artefacts)) {",
        # Briefing ternary head — proves the special-case is intact.
        'name === "briefing"',
        # URL transform — proves snake→kebab is still inline.
        'name.replace(/_/g, "-")',
        # Anchor construction — proves the "open JSON" link is built
        # via el("a", ...) with the same attribute shape.
        'href, target: "_blank", rel: "noopener noreferrer", text: "open JSON"',
    )
    for fragment in expected_fragments:
        assert fragment in inspector_src, (
            f"expected runtime fragment missing from inspector "
            f"source: {fragment!r}"
        )
