"""Step 49 — new job screen: form behaviour and safety fences.

Content scans only. Pins the form-submission behaviour and safety
properties of ``frontend/js/screens/new_job.js``.

Coverage:
* api.createJob is still called
* company_name and company_url fields are still present
* 401 still redirects to login
* navigate still called after job creation
* toast still called after job creation
* no innerHTML, no WebSocket, no EventSource, no setInterval
* no forbidden delivery/system references
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_NEW_JOB_JS = _REPO_ROOT / "frontend" / "js" / "screens" / "new_job.js"


@pytest.fixture(scope="module")
def src() -> str:
    return _NEW_JOB_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Form submission behaviour preserved
# ---------------------------------------------------------------------------

def test_api_create_job_called(src: str) -> None:
    """api.createJob must still be called on form submit."""
    assert "api.createJob(" in src, (
        "new_job.js must call api.createJob() on form submission"
    )


def test_company_name_field_present(src: str) -> None:
    """The company_name input must remain present."""
    assert "company_name" in src, (
        "new_job.js must include a company_name form field"
    )


def test_company_url_field_present(src: str) -> None:
    """The company_url input must remain present."""
    assert "company_url" in src, (
        "new_job.js must include a company_url form field"
    )


def test_401_redirects_to_login(src: str) -> None:
    """A 401 response must redirect to #/login."""
    assert re.search(r'navigate\(\s*"#/login"\s*\)', src), (
        "new_job.js must redirect to #/login on 401"
    )


def test_navigate_called_on_success(src: str) -> None:
    """navigate() must be called after a successful job creation."""
    assert re.search(r"navigate\(\s*`#/jobs/", src), (
        "new_job.js must navigate to the new job on success"
    )


def test_toast_called_on_success(src: str) -> None:
    """toast() must be called after a successful job creation."""
    assert "toast(" in src, (
        "new_job.js must call toast() after successful job creation"
    )


def test_form_reads_company_name_value(src: str) -> None:
    """The form must read company_name from form.elements."""
    assert "form.elements.company_name" in src, (
        "new_job.js must read company_name from form.elements"
    )


def test_form_reads_company_url_value(src: str) -> None:
    """The form must read company_url from form.elements."""
    assert "form.elements.company_url" in src, (
        "new_job.js must read company_url from form.elements"
    )


# ---------------------------------------------------------------------------
# 2. Safety fences
# ---------------------------------------------------------------------------

def test_no_innerhtml(src: str) -> None:
    assert "innerHTML" not in src, "new_job.js must not use innerHTML"


def test_no_websocket(src: str) -> None:
    assert "WebSocket" not in src, "new_job.js must not use WebSocket"


def test_no_eventsource(src: str) -> None:
    assert "EventSource" not in src, "new_job.js must not use EventSource"


def test_no_set_interval(src: str) -> None:
    assert "setInterval" not in src, "new_job.js must not use setInterval"


_FORBIDDEN = (
    "M365", "m365", "Graph", "gmail", "Gmail",
    "smtp", "SMTP", "sendMail", "send_mail",
    "backend.delivery", "delivery_tracking",
    "anthropic", "Anthropic", "keyring", "keychain",
    "Gemini", "gemini",
)


@pytest.mark.parametrize("needle", _FORBIDDEN)
def test_no_forbidden_references(src: str, needle: str) -> None:
    """new_job.js must not reference any forbidden delivery/system string."""
    assert needle not in src, (
        f"new_job.js must not reference {needle!r}"
    )
