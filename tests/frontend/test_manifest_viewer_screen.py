"""Step 34 — frontend document manifest / provenance viewer.

Content scans only — the project has no JS test runner. These tests pin
the structural shape of the Step 34 components in
``frontend/js/screens/manifest_viewer.js``, ``frontend/js/api.js``,
``frontend/js/util.js``, ``frontend/js/app.js``,
``frontend/js/screens/brief_viewer.js``, and
``frontend/js/screens/job_status.js`` so a future edit that silently
broke any of the following would fail here:

* an ``api.getDocumentManifest`` wrapper targets
  ``/api/jobs/{id}/manifest``,
* the viewer screen calls the api wrapper, handles 401/404/500
  (manifest_corrupt) deliberately, and renders the FULL
  ``markdown_sha256`` value (no truncation),
* the viewer never POSTs anywhere and never calls
  ``api.assembleBrief`` (the assembly is an explicit operator action
  on the inspector — the viewer is strictly read-only),
* the hash router resolves ``#/jobs/<id>/manifest`` to the new screen
  and ``app.js`` dispatches it,
* the inspector and the brief-viewer header both expose entry-point
  links to the manifest viewer,
* the manifest viewer exposes the secondary "Open raw JSON" link to
  the underlying GET route for the rare unformatted-file case,
* no document generation, M365, delivery, Anthropic, Keychain,
  orchestrator, or assembly references leak into any of the touched
  files.
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONTEND = _REPO_ROOT / "frontend" / "js"
_API_JS = _FRONTEND / "api.js"
_VIEWER_JS = _FRONTEND / "screens" / "manifest_viewer.js"
_BRIEF_VIEWER_JS = _FRONTEND / "screens" / "brief_viewer.js"
_JOB_STATUS_JS = _FRONTEND / "screens" / "job_status.js"
_UTIL_JS = _FRONTEND / "util.js"
_APP_JS = _FRONTEND / "app.js"


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code_only_viewer_src() -> str:
    """Viewer source with block and line comments stripped.

    The docstring at the top of ``manifest_viewer.js`` mentions the
    forbidden ``/brief/assemble`` route by name — that prose is part of
    the file's *design rationale*, not its executed code. Tests that
    assert "no reference in the *code*" must look past the docstring.
    Mirrors the ``code_only_inspector_src`` pattern from
    ``test_stage2_artefact_inspector.py``.
    """
    src = _VIEWER_JS.read_text(encoding="utf-8")
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


@pytest.fixture(scope="module")
def brief_viewer_src() -> str:
    return _BRIEF_VIEWER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def job_status_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def util_src() -> str:
    return _UTIL_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_src() -> str:
    return _APP_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. api.js wrapper targets the new read-only manifest route
# ---------------------------------------------------------------------------

def test_api_wrapper_present(api_src: str) -> None:
    assert "getDocumentManifest:" in api_src, (
        "api.getDocumentManifest wrapper missing"
    )


def test_api_wrapper_targets_manifest_route(api_src: str) -> None:
    m = re.search(
        r'getDocumentManifest:\s*\([^)]*\)\s*=>\s*request\("GET",\s*'
        r'`/api/jobs/\$\{[^}]+\}/manifest`',
        api_src,
    )
    assert m is not None, (
        "api.getDocumentManifest must GET /api/jobs/{id}/manifest via "
        "request()"
    )


# ---------------------------------------------------------------------------
# 2. manifest_viewer.js — calls the api wrapper, handles error states
# ---------------------------------------------------------------------------

def test_viewer_exports_render_function(viewer_src: str) -> None:
    assert "export async function render" in viewer_src, (
        "manifest_viewer.js must export an async render(container, params)"
    )


def test_viewer_calls_api_wrapper(viewer_src: str) -> None:
    assert "api.getDocumentManifest(" in viewer_src, (
        "manifest_viewer.js must call api.getDocumentManifest(...)"
    )


def test_viewer_handles_401_by_navigating_to_login(viewer_src: str) -> None:
    assert "err.status === 401" in viewer_src
    assert "#/login" in viewer_src, (
        "manifest_viewer.js must navigate to #/login on 401"
    )


def test_viewer_handles_404_as_not_yet_produced(viewer_src: str) -> None:
    assert "err.status === 404" in viewer_src, (
        "manifest_viewer.js must branch on 404 so the empty state is "
        "rendered cleanly"
    )
    assert "not yet produced" in viewer_src.lower(), (
        "manifest_viewer.js must surface a 'not yet produced' message "
        "for the 404 branch"
    )


def test_viewer_handles_500_manifest_corrupt(viewer_src: str) -> None:
    """Distinguish the corrupt branch from the generic-error branch."""
    assert "err.status === 500" in viewer_src
    assert '"manifest_corrupt"' in viewer_src, (
        "manifest_viewer.js must branch specifically on "
        "detail.reason === 'manifest_corrupt' so the operator sees "
        "'file present but unreadable' rather than the generic 5xx banner"
    )


def test_viewer_renders_full_markdown_sha256(viewer_src: str) -> None:
    """The viewer must render the FULL 64-character hash, not a slice.

    Step 34 plan decision: operators verify integrity by eye against the
    on-disk file; a truncated hash defeats that workflow.
    """
    assert "markdown_sha256" in viewer_src, (
        "manifest_viewer.js must reference manifest.markdown_sha256"
    )
    # No slice/substring operations on the hash anywhere in the file.
    forbidden_slices = (
        ".slice(0, 12)",
        ".slice(0,12)",
        ".substring(0, 12)",
        ".substring(0,12)",
        ".slice(0, 8)",
        ".slice(0, 16)",
        # Truncation via String() with substring — defence in depth.
        "markdown_sha256.slice",
        "markdown_sha256.substring",
    )
    for needle in forbidden_slices:
        assert needle not in viewer_src, (
            f"manifest_viewer.js must not truncate the SHA-256 — "
            f"found {needle!r}"
        )


def test_viewer_renders_summary_fields(viewer_src: str) -> None:
    """The summary block must surface the required fields from the plan:
    generated_at, schema_version, critic_verdict, plus outputs,
    artefacts, warnings."""
    for field in (
        "generated_at",
        "schema_version",
        "critic_verdict",
        "outputs",
        "artefacts",
        "warnings",
    ):
        assert field in viewer_src, (
            f"manifest_viewer.js must reference manifest.{field}"
        )


def test_viewer_exposes_open_raw_json_link(viewer_src: str) -> None:
    """Plan decision (3): keep the secondary 'Open raw JSON' link.

    The link targets the underlying GET route (``/api/jobs/{id}/manifest``)
    in a new tab, not the hash-route — operators who want the
    unformatted JSON drop straight into the browser's raw view.
    """
    assert "Open raw JSON" in viewer_src, (
        "manifest_viewer.js must expose a secondary 'Open raw JSON' link"
    )
    m = re.search(
        r'href:\s*`/api/jobs/\$\{[^}]+\}/manifest`',
        viewer_src,
    )
    assert m is not None, (
        "the 'Open raw JSON' link must target the JSON route "
        "/api/jobs/{id}/manifest"
    )


def test_viewer_never_uses_innerHTML(viewer_src: str) -> None:
    """Defence in depth — text reaches the DOM via textContent only."""
    assert "innerHTML" not in viewer_src


def test_viewer_never_uses_script_sinks(viewer_src: str) -> None:
    forbidden = ("eval(", "new Function(", "document.write(")
    for needle in forbidden:
        assert needle not in viewer_src, (
            f"manifest_viewer.js must not contain {needle!r}"
        )


# ---------------------------------------------------------------------------
# 3. Required: the manifest viewer never triggers assembly
# ---------------------------------------------------------------------------

def test_manifest_viewer_never_calls_assemble_route(
    code_only_viewer_src: str,
) -> None:
    """The viewer must never POST anywhere and never call
    ``api.assembleBrief``.

    Why: assembly is an explicit operator action wired into the
    inspector (Step 33). A viewer that auto-regenerated on miss would
    burn Claude tokens silently every time an operator navigated to the
    manifest screen for a job that hadn't been assembled — exactly the
    "runaway by ergonomic accident" failure mode the project's runaway
    traps were designed to catch.

    Comments are stripped (``code_only_viewer_src``) so the docstring
    explaining *why* the viewer must not POST does not itself trigger
    this check.
    """
    src = code_only_viewer_src
    # No string mentions of the assemble route in code.
    assert "/brief/assemble" not in src, (
        "manifest_viewer.js must not reference /brief/assemble in code "
        "— the viewer is strictly read-only"
    )
    # No call to the named API wrapper.
    assert "assembleBrief" not in src, (
        "manifest_viewer.js must not call api.assembleBrief — assembly "
        "is an explicit operator action on the inspector"
    )
    # No POST requests at all — defence in depth against a hand-rolled
    # fetch() that bypasses the api client.
    assert 'request("POST"' not in src
    assert '"POST"' not in src, (
        "manifest_viewer.js must not contain any POST string literal"
    )
    assert "method: 'POST'" not in src
    assert 'method: "POST"' not in src
    # No other state-changing or generation references either.
    forbidden = (
        "runStage2",
        "approveJob",
        "openForEditing",
        "patchBriefing",
        "requestRegeneration",
        "completeRegeneration",
        "failRegeneration",
        "generate_document",
        "regenerate",
    )
    for needle in forbidden:
        assert needle not in src, (
            f"manifest_viewer.js code must not reference {needle!r} — "
            f"the viewer is strictly read-only"
        )


def test_viewer_only_calls_get_document_manifest(viewer_src: str) -> None:
    """The viewer's api.* surface is restricted to the manifest read +
    the Step 36 PDF-export trigger. Any other api.* call would mean
    the viewer is doing work that belongs to a different screen."""
    allowed = {"getDocumentManifest", "runPdfExport"}
    other_api_calls = re.findall(r"api\.(\w+)\(", viewer_src)
    extra = [name for name in other_api_calls if name not in allowed]
    assert not extra, (
        f"manifest_viewer.js must only call {sorted(allowed)}; "
        f"found extra: {extra}"
    )


# ---------------------------------------------------------------------------
# 4. Router wiring — util.js + app.js
# ---------------------------------------------------------------------------

def test_router_parses_manifest_viewer_route(util_src: str) -> None:
    m = re.search(
        r'parts\[0\]\s*===?\s*"jobs"\s*&&\s*parts\[2\]\s*===?\s*"manifest"\s*'
        r'&&\s*parts\.length\s*===?\s*3',
        util_src,
    )
    assert m is not None, (
        "util.parseRoute must match #/jobs/<id>/manifest and return "
        "the manifest_viewer route"
    )
    assert '"manifest_viewer"' in util_src


def test_app_dispatches_manifest_viewer_case(app_src: str) -> None:
    assert 'case "manifest_viewer":' in app_src, (
        "app.js must dispatch the 'manifest_viewer' route"
    )
    assert "renderManifestViewer(" in app_src, (
        "app.js must call the imported renderManifestViewer"
    )


def test_app_imports_manifest_viewer(app_src: str) -> None:
    assert (
        "from \"./screens/manifest_viewer.js\"" in app_src
        or "from './screens/manifest_viewer.js'" in app_src
    ), "app.js must import the manifest viewer screen module"


# ---------------------------------------------------------------------------
# 5. Entry points — inspector + brief viewer header
# ---------------------------------------------------------------------------

def test_inspector_renders_view_manifest_link(job_status_src: str) -> None:
    assert 'text: "View Manifest"' in job_status_src, (
        "inspector must declare a link with text 'View Manifest'"
    )


def test_inspector_view_manifest_link_targets_hash_route(
    job_status_src: str,
) -> None:
    m = re.search(
        r'href:\s*`#/jobs/\$\{[^}]+\}/manifest`',
        job_status_src,
    )
    assert m is not None, (
        "View Manifest link in the inspector must target "
        "#/jobs/{id}/manifest"
    )


def test_brief_viewer_header_links_to_manifest_viewer(
    brief_viewer_src: str,
) -> None:
    """Entry-point option (b): the brief viewer's header strip exposes a
    sibling link to the manifest viewer."""
    assert "View Manifest" in brief_viewer_src, (
        "brief_viewer.js header must expose a 'View Manifest' link"
    )
    m = re.search(
        r'href:\s*`#/jobs/\$\{[^}]+\}/manifest`',
        brief_viewer_src,
    )
    assert m is not None, (
        "the brief viewer's 'View Manifest' link must target "
        "#/jobs/{id}/manifest"
    )


# ---------------------------------------------------------------------------
# 6. Strict exclusions — no leakage into the new file
# ---------------------------------------------------------------------------

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
def test_viewer_does_not_reference_forbidden_systems(
    viewer_src: str, needle: str,
) -> None:
    assert needle not in viewer_src, (
        f"manifest_viewer.js must not reference {needle!r}"
    )


# Step 34 is strictly read-only — the viewer must not introduce any
# document-generation or delivery copy.
_STATE_DRIFT_FORBIDDEN = (
    "generating_documents",
    ".docx",
    ".pdf",
    ".eml",
    ".msg",
    "generate_document",
)


@pytest.mark.parametrize("needle", _STATE_DRIFT_FORBIDDEN)
def test_viewer_no_document_generation_drift(
    viewer_src: str, needle: str,
) -> None:
    forbidden_literals = (f'"{needle}"', f"'{needle}'")
    for literal in forbidden_literals:
        assert literal not in viewer_src, (
            f"manifest_viewer.js code must not contain {literal} — "
            f"Step 34 is read-only manifest rendering"
        )
