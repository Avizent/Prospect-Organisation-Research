"""Step 52 — Stage 1 frontend trigger.

Content scans only. Confirms that:

1. api.js exposes runStage1()
2. job_status.js shows a "Start Research Sweep" button for "created" jobs
3. job_status.js calls api.runStage1()
4. job_status.js renders a sweep-status status node
5. No innerHTML, WebSocket, EventSource, or setInterval introduced
6. No M365/Gmail/SMTP/sendMail/backend.delivery references introduced
"""

from __future__ import annotations

import pathlib

import pytest


_REPO_ROOT      = pathlib.Path(__file__).resolve().parents[2]
_API_JS         = _REPO_ROOT / "frontend" / "js" / "api.js"
_JOB_STATUS_JS  = _REPO_ROOT / "frontend" / "js" / "screens" / "job_status.js"


@pytest.fixture(scope="module")
def api_src() -> str:
    return _API_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def status_src() -> str:
    return _JOB_STATUS_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. api.js — runStage1 method
# ---------------------------------------------------------------------------

def test_run_stage1_method_exists(api_src: str) -> None:
    assert "runStage1" in api_src, (
        "api.js must expose a runStage1 method"
    )


def test_run_stage1_calls_stage1_run_route(api_src: str) -> None:
    assert "stage1/run" in api_src, (
        "api.js runStage1 must call /stage1/run"
    )


# ---------------------------------------------------------------------------
# 2. job_status.js — Start Research Sweep button
# ---------------------------------------------------------------------------

def test_start_research_sweep_button_present(status_src: str) -> None:
    assert "Start Research Sweep" in status_src, (
        "job_status.js must include a 'Start Research Sweep' button"
    )


def test_api_run_stage1_called(status_src: str) -> None:
    assert "api.runStage1(" in status_src, (
        "job_status.js must call api.runStage1()"
    )


def test_sweep_status_node_present(status_src: str) -> None:
    assert "sweep-status" in status_src, (
        "job_status.js must render a sweep-status status node"
    )


def test_button_guarded_by_created_state(status_src: str) -> None:
    assert '"created"' in status_src, (
        "job_status.js must guard the Stage 1 button on current_state === 'created'"
    )


# ---------------------------------------------------------------------------
# 3. Safety fences — api.js
# ---------------------------------------------------------------------------

def test_no_innerhtml_api(api_src: str) -> None:
    assert "innerHTML" not in api_src


@pytest.mark.parametrize("needle", [
    "M365", "m365", "gmail", "Gmail", "smtp", "SMTP",
    "sendMail", "send_mail", "backend.delivery",
])
def test_no_delivery_refs_api(api_src: str, needle: str) -> None:
    assert needle not in api_src, (
        f"api.js must not reference {needle!r}"
    )


# ---------------------------------------------------------------------------
# 4. Safety fences — job_status.js
# ---------------------------------------------------------------------------

def test_no_innerhtml_status(status_src: str) -> None:
    assert "innerHTML" not in status_src


def test_no_websocket_status(status_src: str) -> None:
    assert "WebSocket" not in status_src


def test_no_eventsource_status(status_src: str) -> None:
    assert "EventSource" not in status_src


def test_no_set_interval_status(status_src: str) -> None:
    assert "setInterval" not in status_src


@pytest.mark.parametrize("needle", [
    "M365", "m365", "gmail", "Gmail", "smtp", "SMTP",
    "sendMail", "send_mail", "backend.delivery",
])
def test_no_delivery_refs_status(status_src: str, needle: str) -> None:
    assert needle not in status_src, (
        f"job_status.js must not reference {needle!r}"
    )
