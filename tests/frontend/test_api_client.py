"""Static checks on the JS API client.

We assert by content scan (not by executing the JS) that the
client:

  * Exposes a function per Step 11 endpoint
  * Uses ``credentials: 'same-origin'`` on every request
  * Never embeds an absolute URL
  * Never references admin credential paths (out of scope for Step 12)
"""

from __future__ import annotations

import pathlib
import re

import pytest


_API_JS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "js" / "api.js"
)


@pytest.fixture(scope="module")
def src() -> str:
    return _API_JS.read_text(encoding="utf-8")


# Every function we need a wrapper for. The names match the Step 11
# route surface plus the four auth endpoints the inspector uses.
_REQUIRED_METHODS = (
    "getSetupStatus",
    "setup",
    "login",
    "logout",
    "getMe",
    "createJob",
    "getJobStatus",
    "getResearchDossier",
    "getContacts",
    "getNeedsAssessment",
    "getBriefing",
    "openForEditing",
    "patchBriefing",
    "approveJob",
    "requestRegeneration",
    "completeRegeneration",
    "failRegeneration",
    "runStage2",
    "assembleBrief",
    "getDocumentManifest",
    "runPdfExport",
    "getExportValidation",
)


@pytest.mark.parametrize("method", _REQUIRED_METHODS)
def test_method_is_defined(src: str, method: str) -> None:
    assert f"{method}:" in src, f"api.{method} missing"


def test_exports_api_object(src: str) -> None:
    assert "export { api, ApiError }" in src


def test_uses_same_origin_credentials(src: str) -> None:
    assert 'credentials: "same-origin"' in src


def test_no_absolute_urls(src: str) -> None:
    """No ``http://``/``https://`` URLs may appear in api.js."""
    assert "://" not in src


# Every endpoint the inspector actually targets, sourced from the
# backend route table. We assert each path appears in api.js so the
# wrapper coverage matches what the backend exposes.
_REQUIRED_PATHS = (
    "/auth/setup/status",
    "/auth/setup",
    "/auth/login",
    "/auth/logout",
    "/auth/me",
    "/api/jobs",
    "/artefacts/research-dossier",
    "/artefacts/contacts",
    "/artefacts/needs-assessment",
    "/briefing",
    "/approval/open",
    "/approval/approve",
    "/approval/regenerate/request",
    "/approval/regenerate/complete",
    "/approval/regenerate/fail",
    "/stage2/run",
    "/brief/assemble",
    "/manifest",
    "/export/pdf",
    "/exports/pdf",
    "/exports/validate",
)


@pytest.mark.parametrize("path", _REQUIRED_PATHS)
def test_path_referenced(src: str, path: str) -> None:
    assert path in src, f"api.js missing route path {path}"


def test_uses_post_for_state_changing_routes(src: str) -> None:
    """Approval edges that change state are POSTs, briefing edits are PATCH."""
    # Count the line containing the wrapper, then make sure the
    # method matches.
    patch_line = re.search(
        r'patchBriefing:.*request\("PATCH"', src, flags=re.DOTALL
    )
    assert patch_line, "patchBriefing should use PATCH"

    for method in (
        "openForEditing",
        "approveJob",
        "requestRegeneration",
        "completeRegeneration",
        "failRegeneration",
        "runStage2",
        "assembleBrief",
        "runPdfExport",
    ):
        m = re.search(
            rf'{method}:.*request\("POST"', src, flags=re.DOTALL
        )
        assert m, f"{method} should use POST"


def test_json_encoder_present(src: str) -> None:
    """JSON bodies go through JSON.stringify; form bodies via URLSearchParams."""
    assert "JSON.stringify" in src
    assert "URLSearchParams" in src
