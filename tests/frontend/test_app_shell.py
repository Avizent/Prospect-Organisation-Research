"""Step 46 — app shell and workflow stepper (frontend/js/app.js).

Content scans only. Pins the structural shape of the renderShell()
function and _STEPS constant added in Step 46.

Coverage:
* renderShell present; renderNav removed
* _STEPS constant declared with all four workflow routes
* app-step--active and app-step--complete class references present
* targets app-stepper and app-operator IDs
* active-job chip class present
* el() helper used throughout; no innerHTML
* sign-out behaviour preserved (api.logout + navigate to #/login)
* route-to-step mapping covers all four named routes
* no forbidden delivery / system references
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_APP_JS = _REPO_ROOT / "frontend" / "js" / "app.js"


@pytest.fixture(scope="module")
def app_src() -> str:
    return _APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def shell_body(app_src: str) -> str:
    """Extract the renderShell function body by brace-matching."""
    m = re.search(r"function\s+renderShell\s*\([^)]*\)\s*\{", app_src)
    assert m is not None, (
        "renderShell not found in app.js — Step 46 implementation missing"
    )
    start = m.end() - 1
    depth = 0
    end = None
    for i in range(start, len(app_src)):
        if app_src[i] == "{":
            depth += 1
        elif app_src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "could not find closing brace of renderShell"
    return app_src[m.start() : end + 1]


# ---------------------------------------------------------------------------
# 1. renderShell present; renderNav replaced
# ---------------------------------------------------------------------------

def test_render_shell_declared(app_src: str) -> None:
    """renderShell must be declared as the app shell renderer."""
    assert "renderShell" in app_src, (
        "app.js must declare renderShell() — Step 46 implementation missing"
    )


def test_render_nav_removed(app_src: str) -> None:
    """renderNav must be replaced by renderShell (not a duplicate function)."""
    # The word may appear in a comment describing the replacement — that is
    # acceptable. It must not appear as a function declaration or call site.
    assert "function renderNav" not in app_src, (
        "renderNav function declaration must be removed"
    )
    assert "renderNav(" not in app_src, (
        "renderNav() call sites must be replaced by renderShell()"
    )


# ---------------------------------------------------------------------------
# 2. _STEPS constant and route mapping
# ---------------------------------------------------------------------------

def test_steps_constant_declared(app_src: str) -> None:
    """_STEPS constant must be declared."""
    assert "_STEPS" in app_src, (
        "app.js must declare _STEPS for the workflow stepper"
    )


def test_steps_includes_new_job(app_src: str) -> None:
    assert '"new_job"' in app_src, '_STEPS must include "new_job" route'


def test_steps_includes_job_status(app_src: str) -> None:
    assert '"job_status"' in app_src, '_STEPS must include "job_status" route'


def test_steps_includes_briefing(app_src: str) -> None:
    assert '"briefing"' in app_src, '_STEPS must include "briefing" route'


def test_steps_includes_brief_viewer(app_src: str) -> None:
    assert '"brief_viewer"' in app_src, (
        '_STEPS must include "brief_viewer" route'
    )


def test_steps_includes_manifest_viewer(app_src: str) -> None:
    assert '"manifest_viewer"' in app_src, (
        '_STEPS must include "manifest_viewer" route'
    )


# ---------------------------------------------------------------------------
# 3. Step state class names
# ---------------------------------------------------------------------------

def test_app_step_active_class(app_src: str) -> None:
    """app-step--active must be referenced in app.js."""
    assert "app-step--active" in app_src, (
        "app.js must reference 'app-step--active' for active step styling"
    )


def test_app_step_complete_class(app_src: str) -> None:
    """app-step--complete must be referenced in app.js."""
    assert "app-step--complete" in app_src, (
        "app.js must reference 'app-step--complete' for complete step styling"
    )


# ---------------------------------------------------------------------------
# 4. Target DOM IDs
# ---------------------------------------------------------------------------

def test_targets_app_stepper(app_src: str) -> None:
    """renderShell must target the #app-stepper element."""
    assert '"app-stepper"' in app_src, (
        'app.js must call getElementById("app-stepper")'
    )


def test_targets_app_operator(app_src: str) -> None:
    """renderShell must target the #app-operator element."""
    assert '"app-operator"' in app_src, (
        'app.js must call getElementById("app-operator")'
    )


# ---------------------------------------------------------------------------
# 5. Active job chip
# ---------------------------------------------------------------------------

def test_active_job_chip_class(app_src: str) -> None:
    """The app-active-job chip class must be used in the stepper."""
    assert "app-active-job" in app_src, (
        "app.js must render the app-active-job chip for job routes"
    )


# ---------------------------------------------------------------------------
# 6. DOM construction — el() only, no innerHTML
# ---------------------------------------------------------------------------

def test_no_innerhtml_in_shell(shell_body: str) -> None:
    """renderShell must not use innerHTML — all DOM via el()."""
    assert "innerHTML" not in shell_body, (
        "renderShell must not use innerHTML"
    )


def test_uses_el_helper(shell_body: str) -> None:
    """renderShell must construct DOM using el()."""
    assert re.search(r"\bel\(", shell_body), (
        "renderShell must use el() for all DOM construction"
    )


# ---------------------------------------------------------------------------
# 7. Sign-out behaviour preserved
# ---------------------------------------------------------------------------

def test_sign_out_calls_api_logout(shell_body: str) -> None:
    """The sign-out handler must call api.logout()."""
    assert "api.logout()" in shell_body, (
        "renderShell must call api.logout() on sign-out"
    )


def test_sign_out_navigates_to_login(shell_body: str) -> None:
    """The sign-out handler must navigate to #/login."""
    assert 'navigate("#/login")' in shell_body, (
        "renderShell must navigate to #/login after sign-out"
    )


# ---------------------------------------------------------------------------
# 8. renderShell called from dispatch() for authenticated routes
# ---------------------------------------------------------------------------

def test_render_shell_called_with_route(app_src: str) -> None:
    """renderShell must be called with both currentUser and route arguments."""
    assert re.search(
        r"renderShell\s*\(\s*username\s*,\s*route\s*\)",
        app_src,
    ), (
        "dispatch() must call renderShell(username, route) for auth routes"
    )


def test_render_shell_called_for_public_routes(app_src: str) -> None:
    """renderShell must also be called for public routes (null user)."""
    assert re.search(
        r"renderShell\s*\(\s*null\s*,\s*route\s*\)",
        app_src,
    ), (
        "dispatch() must call renderShell(null, route) for public routes"
    )


# ---------------------------------------------------------------------------
# 9. No forbidden references
# ---------------------------------------------------------------------------

_FORBIDDEN = (
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
    "WebSocket",
    "EventSource",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_in_app(app_src: str, needle: str) -> None:
    """app.js must not reference any delivery or system forbidden strings."""
    assert needle not in app_src, (
        f"app.js must not reference {needle!r}"
    )
