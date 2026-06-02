"""Step 50 — briefing review screen: API behaviour and safety fences.

Content scans only. Pins the form-submission behaviour, all API calls,
and safety properties of ``frontend/js/screens/briefing.js``.

Coverage:
* api.approveJob is still called
* api.patchBriefing is still called (save section + user context)
* api.requestRegeneration is still called (refine section with AI)
* api.openForEditing is still called
* api.failRegeneration is still called
* api.getJobStatus and api.getBriefing are still called
* 401 still redirects to #/login
* navigate still called after approval
* toast still called after successful operations
* EDITABLE_SECTIONS still contains all five section keys
* no innerHTML, no WebSocket, no EventSource, no setInterval
* no forbidden delivery/system references
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BRIEFING_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "briefing.js"


@pytest.fixture(scope="module")
def src() -> str:
    return _BRIEFING_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. API behaviour preserved
# ---------------------------------------------------------------------------

def test_api_approve_job_called(src: str) -> None:
    """api.approveJob must still be called for the approval action."""
    assert "api.approveJob(" in src, (
        "briefing.js must call api.approveJob() on approval"
    )


def test_api_patch_briefing_called(src: str) -> None:
    """api.patchBriefing must still be called when saving sections."""
    assert "api.patchBriefing(" in src, (
        "briefing.js must call api.patchBriefing() when saving"
    )


def test_api_request_regeneration_called(src: str) -> None:
    """api.requestRegeneration must still be called for refine-section."""
    assert "api.requestRegeneration(" in src, (
        "briefing.js must call api.requestRegeneration() for section refine"
    )


def test_api_open_for_editing_called(src: str) -> None:
    """api.openForEditing must still be called."""
    assert "api.openForEditing(" in src, (
        "briefing.js must call api.openForEditing()"
    )


def test_api_fail_regeneration_called(src: str) -> None:
    """api.failRegeneration must still be called."""
    assert "api.failRegeneration(" in src, (
        "briefing.js must call api.failRegeneration()"
    )


def test_api_get_job_status_called(src: str) -> None:
    """api.getJobStatus must still be called to read current state."""
    assert "api.getJobStatus(" in src, (
        "briefing.js must call api.getJobStatus()"
    )


def test_api_get_briefing_called(src: str) -> None:
    """api.getBriefing must still be called to load briefing data."""
    assert "api.getBriefing(" in src, (
        "briefing.js must call api.getBriefing()"
    )


def test_401_redirects_to_login(src: str) -> None:
    """A 401 response at any point must redirect to #/login."""
    assert re.search(r'navigate\(\s*"#/login"\s*\)', src), (
        "briefing.js must redirect to #/login on 401"
    )


def test_navigate_to_job_after_approval(src: str) -> None:
    """After approval, navigate must go to the job status screen."""
    assert re.search(r"navigate\(\s*`#/jobs/", src), (
        "briefing.js must navigate to #/jobs/{id} after approval"
    )


def test_toast_called_on_operations(src: str) -> None:
    """toast() must be called to confirm successful operations."""
    assert "toast(" in src, (
        "briefing.js must call toast() to confirm operations"
    )


# ---------------------------------------------------------------------------
# 2. Section keys preserved
# ---------------------------------------------------------------------------

def test_editable_sections_snapshot(src: str) -> None:
    assert '"snapshot"' in src, "EDITABLE_SECTIONS must include 'snapshot'"


def test_editable_sections_business_context(src: str) -> None:
    assert '"business_context"' in src


def test_editable_sections_it_landscape(src: str) -> None:
    assert '"it_landscape"' in src


def test_editable_sections_key_people(src: str) -> None:
    assert '"key_people"' in src


def test_editable_sections_opportunity(src: str) -> None:
    assert '"opportunity"' in src


def test_user_context_patched(src: str) -> None:
    """user_context must still be patchable via api.patchBriefing."""
    assert "user_context" in src, (
        "briefing.js must handle user_context as a patchable field"
    )


# ---------------------------------------------------------------------------
# 3. Safety fences
# ---------------------------------------------------------------------------

def test_no_innerhtml(src: str) -> None:
    assert "innerHTML" not in src, "briefing.js must not use innerHTML"


def test_no_websocket(src: str) -> None:
    assert "WebSocket" not in src, "briefing.js must not use WebSocket"


def test_no_eventsource(src: str) -> None:
    assert "EventSource" not in src, "briefing.js must not use EventSource"


def test_no_set_interval(src: str) -> None:
    assert "setInterval" not in src, "briefing.js must not use setInterval"


_FORBIDDEN = (
    "M365", "m365", "Graph", "gmail", "Gmail",
    "smtp", "SMTP", "sendMail", "send_mail",
    "backend.delivery", "delivery_tracking",
    "anthropic", "Anthropic", "keyring", "keychain",
    "Gemini", "gemini",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_references(src: str, needle: str) -> None:
    """briefing.js must not reference any forbidden delivery/system string."""
    assert needle not in src, (
        f"briefing.js must not reference {needle!r}"
    )
