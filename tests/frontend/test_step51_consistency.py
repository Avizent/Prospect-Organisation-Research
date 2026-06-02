"""Step 51 — frontend visual consistency pass.

Content scans only. Confirms that all redesigned screens share a coherent
design system and that the login/setup screens are now aligned with it.

Coverage:
1.  Login screen uses ls-page wrapper
2.  Login screen uses ls-card
3.  Login screen uses ls-title
4.  Login screen uses nj-field / nj-label / nj-input
5.  Login screen preserves api.login behaviour
6.  Login screen navigates to #/jobs/new on success
7.  Setup screen uses ls-page wrapper
8.  Setup screen uses ls-card
9.  Setup screen preserves api.setup behaviour
10. Setup screen navigates to #/login on success
11. design.css declares ls-* classes
12. design.css overrides .br-editor-column .briefing-section margin
13. design.css declares export-history-table
14. No Gemini branding in any frontend JS file
15. No innerHTML in login.js
16. No innerHTML in setup.js
17. No WebSocket in login.js
18. No WebSocket in setup.js
19. No setInterval in login.js
20. No setInterval in setup.js
21. Progress screen uses progress-stage-row (Step 47 class)
22. Briefing screen uses br-page (Step 50 class)
23. Delivery screen uses dr-hero (Step 48 class)
24. New job screen uses nj-page (Step 49 class)
"""

from __future__ import annotations

import pathlib

import pytest


_REPO_ROOT   = pathlib.Path(__file__).resolve().parents[2]
_LOGIN_JS    = _REPO_ROOT / "frontend" / "js" / "screens" / "login.js"
_SETUP_JS    = _REPO_ROOT / "frontend" / "js" / "screens" / "setup.js"
_PROGRESS_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"
_BRIEFING_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "briefing.js"
_MANIFEST_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "manifest_viewer.js"
_NEW_JOB_JS  = _REPO_ROOT / "frontend" / "js" / "screens" / "new_job.js"
_DESIGN_CSS  = _REPO_ROOT / "frontend" / "styles" / "design.css"


@pytest.fixture(scope="module")
def login_src() -> str:
    return _LOGIN_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def setup_src() -> str:
    return _SETUP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def progress_src() -> str:
    return _PROGRESS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def briefing_src() -> str:
    return _BRIEFING_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest_src() -> str:
    return _MANIFEST_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def new_job_src() -> str:
    return _NEW_JOB_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_src() -> str:
    return _DESIGN_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Login screen — design alignment
# ---------------------------------------------------------------------------

def test_login_ls_page(login_src: str) -> None:
    assert "ls-page" in login_src, "login.js must use 'ls-page' wrapper"


def test_login_ls_card(login_src: str) -> None:
    assert "ls-card" in login_src, "login.js must use 'ls-card'"


def test_login_ls_title(login_src: str) -> None:
    assert "ls-title" in login_src, "login.js must use 'ls-title'"


def test_login_nj_field(login_src: str) -> None:
    assert "nj-field" in login_src, "login.js must use 'nj-field' for field rows"


def test_login_nj_label(login_src: str) -> None:
    assert "nj-label" in login_src, "login.js must use 'nj-label'"


def test_login_nj_input(login_src: str) -> None:
    assert "nj-input" in login_src, "login.js must use 'nj-input'"


def test_login_api_login_preserved(login_src: str) -> None:
    assert "api.login(" in login_src, "login.js must call api.login()"


def test_login_navigate_jobs_new(login_src: str) -> None:
    assert "#/jobs/new" in login_src, "login.js must navigate to #/jobs/new on success"


# ---------------------------------------------------------------------------
# 2. Setup screen — design alignment
# ---------------------------------------------------------------------------

def test_setup_ls_page(setup_src: str) -> None:
    assert "ls-page" in setup_src, "setup.js must use 'ls-page' wrapper"


def test_setup_ls_card(setup_src: str) -> None:
    assert "ls-card" in setup_src, "setup.js must use 'ls-card'"


def test_setup_api_setup_preserved(setup_src: str) -> None:
    assert "api.setup(" in setup_src, "setup.js must call api.setup()"


def test_setup_navigate_login(setup_src: str) -> None:
    assert "#/login" in setup_src, "setup.js must navigate to #/login on success"


def test_setup_recovery_email_field(setup_src: str) -> None:
    assert "recovery_email" in setup_src, "setup.js must preserve recovery_email field"


# ---------------------------------------------------------------------------
# 3. CSS — new ls-* classes and consistency overrides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    "ls-page", "ls-card", "ls-form", "ls-title", "ls-subtitle",
])
def test_css_ls_class_declared(css_src: str, cls: str) -> None:
    assert f".{cls}" in css_src, (
        f"design.css must declare .{cls} for the Step 51 auth screens"
    )


def test_css_briefing_section_margin_override(css_src: str) -> None:
    """design.css must override .briefing-section margin inside br-editor-column."""
    assert ".br-editor-column .briefing-section" in css_src, (
        "design.css must override .br-editor-column .briefing-section margin"
    )


def test_css_export_history_table_declared(css_src: str) -> None:
    assert ".export-history-table" in css_src, (
        "design.css must declare .export-history-table"
    )


# ---------------------------------------------------------------------------
# 4. No Gemini branding anywhere in the frontend JS
# ---------------------------------------------------------------------------

_FRONTEND_JS_FILES = [
    _LOGIN_JS, _SETUP_JS, _PROGRESS_JS, _BRIEFING_JS,
    _MANIFEST_JS, _NEW_JOB_JS,
]


@pytest.mark.parametrize("js_file", [f.name for f in _FRONTEND_JS_FILES])
def test_no_gemini_in_frontend(js_file: str) -> None:
    path = next(f for f in _FRONTEND_JS_FILES if f.name == js_file)
    src = path.read_text(encoding="utf-8")
    assert "Gemini" not in src and "gemini" not in src, (
        f"{js_file} must not contain Gemini branding"
    )


# ---------------------------------------------------------------------------
# 5. Safety fences on login + setup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src_fixture,name", [
    ("login_src", "login.js"), ("setup_src", "setup.js"),
])
def test_no_innerhtml(src_fixture: str, name: str, request) -> None:
    src = request.getfixturevalue(src_fixture)
    assert "innerHTML" not in src, f"{name} must not use innerHTML"


@pytest.mark.parametrize("src_fixture,name", [
    ("login_src", "login.js"), ("setup_src", "setup.js"),
])
def test_no_websocket(src_fixture: str, name: str, request) -> None:
    src = request.getfixturevalue(src_fixture)
    assert "WebSocket" not in src, f"{name} must not use WebSocket"


@pytest.mark.parametrize("src_fixture,name", [
    ("login_src", "login.js"), ("setup_src", "setup.js"),
])
def test_no_set_interval(src_fixture: str, name: str, request) -> None:
    src = request.getfixturevalue(src_fixture)
    assert "setInterval" not in src, f"{name} must not use setInterval"


# ---------------------------------------------------------------------------
# 6. Cross-screen design class spot-checks
# ---------------------------------------------------------------------------

def test_progress_screen_design_class(progress_src: str) -> None:
    assert "progress-stage-row" in progress_src, (
        "job_status.js must still use 'progress-stage-row' (Step 47)"
    )


def test_briefing_screen_design_class(briefing_src: str) -> None:
    assert "br-page" in briefing_src, (
        "briefing.js must still use 'br-page' (Step 50)"
    )


def test_delivery_screen_design_class(manifest_src: str) -> None:
    assert "dr-hero" in manifest_src, (
        "manifest_viewer.js must still use 'dr-hero' (Step 48)"
    )


def test_new_job_screen_design_class(new_job_src: str) -> None:
    assert "nj-page" in new_job_src, (
        "new_job.js must still use 'nj-page' (Step 49)"
    )
