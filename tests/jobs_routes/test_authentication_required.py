"""Every route in ``backend/jobs/routes.py`` must require a session.

We hit each route without a session cookie and assert HTTP 401. The
list of (method, path, body) tuples is exhaustive — adding a new
route without adding a parametrize entry here will fail the
``test_all_routes_covered`` self-check below.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


# Every approved route, plus a representative request body.
_ROUTES: list[tuple[str, str, dict | None]] = [
    ("POST", "/api/jobs", {
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
    }),
    ("GET", "/api/jobs/00000000-0000-4000-8000-000000000000", None),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/research-dossier",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000/artefacts/contacts",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/needs-assessment",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000/briefing",
        None,
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000/approval/open",
        None,
    ),
    (
        "PATCH",
        "/api/jobs/00000000-0000-4000-8000-000000000000/briefing",
        {"user_context": "x"},
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000/approval/approve",
        None,
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/approval/regenerate/request",
        {"section": "snapshot", "instructions": "x"},
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/approval/regenerate/complete",
        {"section": "snapshot", "briefing": {}},
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/approval/regenerate/fail",
        {"category": "crashed", "details": "x"},
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/product-mapping",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/benefits",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000/artefacts/faq",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/objections",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000"
        "/artefacts/critic-report",
        None,
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000/stage2/run",
        {"knowledge_bundle": "x"},
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000/brief/markdown",
        None,
    ),
    (
        "POST",
        "/api/jobs/00000000-0000-4000-8000-000000000000/brief/assemble",
        None,
    ),
    (
        "GET",
        "/api/jobs/00000000-0000-4000-8000-000000000000/manifest",
        None,
    ),
]


@pytest.mark.parametrize("method, path, body", _ROUTES)
def test_anonymous_returns_401(
    anon_client: TestClient,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    """Each route under ``/api/jobs`` rejects anonymous callers."""
    if body is None:
        r = anon_client.request(method, path)
    else:
        r = anon_client.request(method, path, json=body)
    assert r.status_code == 401, (method, path, r.text)


def test_all_jobs_routes_covered() -> None:
    """Self-check: if a new route appears under /api/jobs but is not
    listed in ``_ROUTES``, the auth fence misses it."""
    seen_paths: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/jobs"):
            continue
        for method in methods:
            if method == "HEAD":
                continue
            seen_paths.add((method, path))

    # Map the test's path UUIDs back to the FastAPI path template.
    sample_uuid = "00000000-0000-4000-8000-000000000000"
    listed = {
        (method, path.replace(sample_uuid, "{job_id}"))
        for method, path, _ in _ROUTES
    }
    missing = seen_paths - listed
    assert not missing, (
        f"these routes have no anonymous-rejects-with-401 check: {missing}"
    )
