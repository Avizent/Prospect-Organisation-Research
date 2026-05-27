"""The frontend mount serves index.html and its static assets.

We also check that the static mount does NOT shadow any of the
API mounts — a regression where ``/`` is mounted before the
routers would silently break every backend route.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_serves_index_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    assert "ANS Prospect Tool" in r.text
    # The SPA must load app.js as a module — not inline script.
    assert '<script type="module" src="/js/app.js">' in r.text


def test_serves_app_js(client: TestClient) -> None:
    r = client.get("/js/app.js")
    assert r.status_code == 200, r.text
    ctype = r.headers.get("content-type", "")
    assert (
        "javascript" in ctype or "ecmascript" in ctype or "text/" in ctype
    ), ctype
    # Sanity check the module exposes the dispatcher wiring.
    assert "hashchange" in r.text
    assert "DOMContentLoaded" in r.text


def test_serves_api_js(client: TestClient) -> None:
    r = client.get("/js/api.js")
    assert r.status_code == 200, r.text
    assert "createJob" in r.text
    assert "approveJob" in r.text
    assert "patchBriefing" in r.text


def test_serves_inspector_css(client: TestClient) -> None:
    r = client.get("/styles/inspector.css")
    assert r.status_code == 200, r.text
    ctype = r.headers.get("content-type", "")
    assert "css" in ctype, ctype
    assert ".app-main" in r.text


def test_serves_screen_modules(client: TestClient) -> None:
    """All five screens must be reachable as static modules."""
    for name in ("login", "setup", "new_job", "job_status", "briefing"):
        r = client.get(f"/js/screens/{name}.js")
        assert r.status_code == 200, (name, r.text)
        assert "export" in r.text, name


def test_api_routes_still_win(client: TestClient) -> None:
    """The static mount must not eat the API routes."""
    # /health is a real API endpoint — it must NOT be served as a
    # static 404 by the frontend mount.
    r = client.get("/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}

    # Anonymous /api/jobs/<uuid> must still return 401, not be
    # short-circuited to a 200/404 by the static mount.
    r = client.get("/api/jobs/00000000-0000-4000-8000-000000000000")
    assert r.status_code == 401, r.text

    # Anonymous /auth/me must still return 401.
    r = client.get("/auth/me")
    assert r.status_code == 401, r.text


def test_unknown_static_path_404(client: TestClient) -> None:
    r = client.get("/js/does-not-exist.js")
    assert r.status_code == 404
