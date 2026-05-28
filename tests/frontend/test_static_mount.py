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
    """All screens must be reachable as static modules."""
    for name in (
        "login",
        "setup",
        "new_job",
        "job_status",
        "briefing",
        "brief_viewer",
    ):
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


def test_static_assets_set_no_cache_header(client: TestClient) -> None:
    """The static mount must instruct the browser to revalidate every
    ES module on every navigation.

    Background (Step 31 follow-up): ``app.js`` is a graph of static
    ``import``s wired in the browser. When we ship a fix to a leaf
    module like ``util.js`` (e.g. a new ``parseRoute`` branch), an
    aggressively-cached previous response would keep the hash router
    returning ``not_found`` for the new route even though the source
    on disk is correct. Symptom: 'No screen for #/jobs/<id>/brief'.

    ``no-cache`` does NOT disable caching — it forces the browser to
    revalidate with the origin before serving a cached copy. The
    underlying ``StaticFiles`` handler still returns 304s cheaply
    via Last-Modified/ETag when content hasn't changed.
    """
    for path in (
        "/",
        "/js/app.js",
        "/js/util.js",
        "/js/markdown.js",
        "/js/screens/brief_viewer.js",
        "/js/screens/job_status.js",
    ):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        cc = r.headers.get("cache-control", "")
        assert "no-cache" in cc, (
            f"{path} must include 'no-cache' in Cache-Control "
            f"so the browser revalidates stale ES modules; got: {cc!r}"
        )
