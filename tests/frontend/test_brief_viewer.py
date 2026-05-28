"""Step 31 — frontend Markdown brief viewer.

Content scans only — the project has no JS test runner. The tests
pin the structural shape of the Step 31 components in
``frontend/js/markdown.js``, ``frontend/js/screens/brief_viewer.js``,
``frontend/js/api.js``, ``frontend/js/screens/job_status.js``,
``frontend/js/util.js``, and ``frontend/js/app.js`` so a future edit
that silently broke any of the following would fail here:

* an ``api.getProspectBriefMarkdown`` wrapper points at
  ``/api/jobs/{id}/brief/markdown``,
* the Markdown renderer ``renderMarkdown`` exists and never uses
  ``innerHTML``, ``eval(``, ``new Function(``, or any other script
  sink (defence-in-depth alongside ``tests/frontend/test_no_leakage.py``),
* the viewer screen calls the api wrapper and handles 404 as
  "brief not yet assembled" rather than a crash,
* the inspector adds a "View Brief" link gated on
  ``snapshot.available_artefacts.prospect_brief``,
* the link points at the in-app viewer route (``#/jobs/<id>/brief``)
  and not at a JSON URL — the brief is a *display* artefact,
* the hash router resolves ``#/jobs/<id>/brief`` to the new screen
  and ``app.js`` dispatches the route,
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
_MARKDOWN_JS = _FRONTEND / "markdown.js"
_VIEWER_JS = _FRONTEND / "screens" / "brief_viewer.js"
_JOB_STATUS_JS = _FRONTEND / "screens" / "job_status.js"
_UTIL_JS = _FRONTEND / "util.js"
_APP_JS = _FRONTEND / "app.js"


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markdown_src() -> str:
    return _MARKDOWN_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def viewer_src() -> str:
    return _VIEWER_JS.read_text(encoding="utf-8")


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
# 1. api.js wrapper targets the new read-only route
# ---------------------------------------------------------------------------

def test_api_wrapper_present(api_src: str) -> None:
    """``api.getProspectBriefMarkdown`` must exist as the named wrapper."""
    assert "getProspectBriefMarkdown:" in api_src, (
        "api.getProspectBriefMarkdown wrapper missing"
    )


def test_api_wrapper_targets_brief_markdown_route(api_src: str) -> None:
    """The wrapper GETs ``/api/jobs/{id}/brief/markdown`` via ``request()``."""
    m = re.search(
        r'getProspectBriefMarkdown:\s*\([^)]*\)\s*=>\s*request\("GET",\s*'
        r'`/api/jobs/\$\{[^}]+\}/brief/markdown`',
        api_src,
    )
    assert m is not None, (
        "api.getProspectBriefMarkdown must GET "
        "/api/jobs/{id}/brief/markdown via request()"
    )


# ---------------------------------------------------------------------------
# 2. markdown.js — exports a safe renderer
# ---------------------------------------------------------------------------

def test_markdown_module_exports_render_function(markdown_src: str) -> None:
    """The renderer is named ``renderMarkdown`` and is the module export."""
    assert "export function renderMarkdown" in markdown_src, (
        "markdown.js must export a `renderMarkdown` function"
    )


def test_markdown_renderer_never_uses_innerHTML(markdown_src: str) -> None:
    """No ``innerHTML`` anywhere — text reaches the DOM via textContent."""
    assert "innerHTML" not in markdown_src, (
        "markdown.js must not use innerHTML — defeats the safe-by-"
        "construction rendering invariant"
    )


def test_markdown_renderer_never_uses_script_sinks(
    markdown_src: str,
) -> None:
    """No ``eval(``, ``new Function(``, ``document.write(`` either."""
    forbidden = ("eval(", "new Function(", "document.write(")
    for needle in forbidden:
        assert needle not in markdown_src, (
            f"markdown.js must not contain {needle!r}"
        )


def test_markdown_renderer_uses_create_text_node(
    markdown_src: str,
) -> None:
    """Plain text must reach the DOM via createTextNode (textContent
    on element creation is also fine through the ``el()`` ``text:``
    channel — assert at least one of the two is present)."""
    assert (
        "createTextNode" in markdown_src
        or 'text:' in markdown_src
    ), (
        "markdown.js must build text nodes via createTextNode or "
        "the el() text channel — never via innerHTML"
    )


# ---------------------------------------------------------------------------
# 3. brief_viewer.js — calls the api wrapper, handles 404 as empty state
# ---------------------------------------------------------------------------

def test_viewer_imports_render_markdown(viewer_src: str) -> None:
    """The viewer must call the safe renderer rather than emit raw HTML."""
    assert "renderMarkdown" in viewer_src, (
        "brief_viewer.js must use renderMarkdown to render the body"
    )


def test_viewer_calls_api_wrapper(viewer_src: str) -> None:
    """The viewer fetches the body via the wrapper, not raw fetch()."""
    assert "api.getProspectBriefMarkdown(" in viewer_src, (
        "brief_viewer.js must call api.getProspectBriefMarkdown(...) "
        "— going around the API client would bypass shared error "
        "handling"
    )


def test_viewer_handles_404_as_not_yet_assembled(viewer_src: str) -> None:
    """A 404 must surface a clean "brief not yet assembled" state."""
    assert "err.status === 404" in viewer_src, (
        "brief_viewer.js must branch on HTTP 404 so a missing "
        "prospect_brief.md surfaces as an empty state, not a crash"
    )
    assert "not yet assembled" in viewer_src.lower(), (
        "brief_viewer.js must surface a 'not yet assembled' message "
        "for the 404 branch"
    )


def test_viewer_handles_401_by_navigating_to_login(viewer_src: str) -> None:
    """Mirrors the job_status screen — 401 sends the user back to login."""
    assert "err.status === 401" in viewer_src, (
        "brief_viewer.js must branch on HTTP 401"
    )
    assert "#/login" in viewer_src, (
        "brief_viewer.js must navigate to #/login on 401"
    )


# ---------------------------------------------------------------------------
# 4. job_status.js — gated "View Brief" link points to in-app viewer
# ---------------------------------------------------------------------------

def test_inspector_renders_view_brief_link(job_status_src: str) -> None:
    """A literal ``View Brief`` label must appear so the operator can
    locate the entry point on the screen."""
    assert 'text: "View Brief"' in job_status_src, (
        "inspector must declare a link with text 'View Brief' "
        "so the operator can open the assembled Markdown brief"
    )


def test_inspector_view_brief_link_targets_in_app_viewer(
    job_status_src: str,
) -> None:
    """The link must target the hash-route viewer, not a JSON URL.

    A bug that hung the JSON-style ``/api/jobs/{id}/artefacts/...``
    URL off the brief boolean would dump raw Markdown bytes into the
    browser instead of the rendered view.
    """
    m = re.search(
        r'href:\s*`#/jobs/\$\{[^}]+\}/brief`',
        job_status_src,
    )
    assert m is not None, (
        "View Brief link must target #/jobs/{id}/brief — the in-app "
        "viewer route, not a JSON URL"
    )


def test_inspector_view_brief_gated_on_prospect_brief_boolean(
    job_status_src: str,
) -> None:
    """The View Brief branch must key off ``prospect_brief``.

    We assert the gate appears before the ``View Brief`` literal so
    a future refactor cannot accidentally render the link
    unconditionally.
    """
    gate = re.search(
        r'name === "prospect_brief"',
        job_status_src,
    )
    assert gate is not None, (
        "inspector must gate the View Brief link on the "
        "'prospect_brief' artefact key"
    )
    label_idx = job_status_src.index('text: "View Brief"')
    assert gate.start() < label_idx, (
        "the prospect_brief gate must enclose the View Brief label"
    )


# ---------------------------------------------------------------------------
# 5. Router wiring — util.js + app.js
# ---------------------------------------------------------------------------

def test_router_parses_brief_viewer_route(util_src: str) -> None:
    """``parseRoute`` must resolve ``#/jobs/<id>/brief`` to the viewer."""
    m = re.search(
        r'parts\[0\]\s*===?\s*"jobs"\s*&&\s*parts\[2\]\s*===?\s*"brief"\s*'
        r'&&\s*parts\.length\s*===?\s*3',
        util_src,
    )
    assert m is not None, (
        "util.parseRoute must match #/jobs/<id>/brief and return "
        "the brief_viewer route"
    )
    assert '"brief_viewer"' in util_src, (
        "util.parseRoute must return name 'brief_viewer' for the brief route"
    )


def test_app_dispatches_brief_viewer_case(app_src: str) -> None:
    """``app.js`` must register a ``case 'brief_viewer'`` in the dispatch
    switch and call the imported renderer."""
    assert 'case "brief_viewer":' in app_src, (
        "app.js must dispatch the 'brief_viewer' route"
    )
    assert "renderBriefViewer(" in app_src, (
        "app.js must call the imported renderBriefViewer"
    )


# ---------------------------------------------------------------------------
# 6. Strict exclusions — no leakage into the new files
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
def test_markdown_module_does_not_reference_forbidden_systems(
    markdown_src: str, needle: str,
) -> None:
    assert needle not in markdown_src, (
        f"markdown.js must not reference {needle!r}"
    )


@pytest.mark.parametrize("needle", _FORBIDDEN_SYSTEMS)
def test_viewer_does_not_reference_forbidden_systems(
    viewer_src: str, needle: str,
) -> None:
    assert needle not in viewer_src, (
        f"brief_viewer.js must not reference {needle!r}"
    )


# Step 31 is strictly read-only — the viewer must not introduce any
# state-changing or document-generation copy.
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
            f"brief_viewer.js code must not contain {literal} "
            f"— Step 31 is read-only Markdown rendering"
        )


# ---------------------------------------------------------------------------
# 7. Viewer must not call state-changing routes
# ---------------------------------------------------------------------------

def test_viewer_does_not_call_any_state_changing_api(
    viewer_src: str,
) -> None:
    """The viewer must only call ``api.getProspectBriefMarkdown``.

    Any other ``api.<method>(`` call in this file would suggest the
    viewer is wiring up state changes (approval, regeneration, run
    Stage 2, document generation) — none of which are in Step 31's
    scope.
    """
    other_api_calls = re.findall(r"api\.(\w+)\(", viewer_src)
    extra = [name for name in other_api_calls
             if name != "getProspectBriefMarkdown"]
    assert not extra, (
        f"brief_viewer.js must not call other api.* methods; found: "
        f"{extra}"
    )
