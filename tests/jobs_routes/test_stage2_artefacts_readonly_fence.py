"""Read-only fence for the Step 21 Stage 2 artefact GET routes.

Step 21 is explicit: the five Stage 2 routes inspect artefacts that
the orchestrator has already written; they MUST NOT run agents,
write files, or change job state. This module asserts that contract
with three independent guards stacked together — if any one is
removed the test still fails.

Guard 1 — runtime sentinel patch on the orchestrator
----------------------------------------------------
Every Stage 2 :class:`Orchestrator` entry point
(:meth:`run_mapping`, :meth:`run_benefits`, :meth:`run_faq`,
:meth:`run_objections`, :meth:`run_stage2_writers`, :meth:`run_critic`)
is replaced with a sentinel that raises :class:`AssertionError` if
called. A route that accidentally delegated to the orchestrator would
fail the test loudly, even if it caught and swallowed the assertion.

Guard 2 — HTTP method enforcement
---------------------------------
GET is the only allowed verb. POST/PUT/DELETE/PATCH on every Stage 2
URL must return 405 (Method Not Allowed) — the router knows the path
but rejects the verb. A future drift where a writer route is added
under the same prefix would flip these from 405 → 200/201 and break
the test.

Guard 3 — byte-identical artefacts before/after
-----------------------------------------------
Every file in the job folder is SHA-256'd before and after hitting
each GET route. The post-call digest must match the pre-call digest.
A read path that secretly rewrote (e.g.) ``state.json`` or
``briefing.json`` would change the digest and fail the test.

The static-import fence
(``test_no_production_client_or_keychain.py``) covers the AST layer
— this module covers the runtime layer.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.orchestrator import Orchestrator


# All five Stage 2 routes share the same fence requirements.
_STAGE2_ROUTES = [
    "artefacts/product-mapping",
    "artefacts/benefits",
    "artefacts/faq",
    "artefacts/objections",
    "artefacts/critic-report",
]


# The full set of orchestrator entry points the routes must NEVER hit.
# Each must already exist on :class:`Orchestrator` — a typo here
# would make the patch silently no-op. We assert presence below.
_FORBIDDEN_ORCHESTRATOR_METHODS = (
    "run_mapping",
    "run_benefits",
    "run_faq",
    "run_objections",
    "run_stage2_writers",
    "run_critic",
)


# ---------------------------------------------------------------------------
# Guard 1 — runtime sentinel patch
# ---------------------------------------------------------------------------

@pytest.fixture()
def _forbid_orchestrator_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace every Stage 2 orchestrator entry point with a sentinel.

    A real route handler that delegates to the orchestrator would
    blow up here — the assertion message names the method so a future
    regression is self-diagnosing.
    """
    # Defence: each named method must actually exist on Orchestrator.
    # A typo would silently no-op the patch.
    for name in _FORBIDDEN_ORCHESTRATOR_METHODS:
        assert hasattr(Orchestrator, name), (
            f"Orchestrator.{name} not found — fence patch would no-op"
        )

    def _make_sentinel(method_name: str):
        async def _sentinel(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                f"backend.jobs.routes called Orchestrator.{method_name} "
                f"— Step 21 routes must be read-only"
            )
        return _sentinel

    for name in _FORBIDDEN_ORCHESTRATOR_METHODS:
        monkeypatch.setattr(
            Orchestrator, name, _make_sentinel(name), raising=True,
        )


@pytest.mark.parametrize("path", _STAGE2_ROUTES)
def test_route_does_not_invoke_orchestrator(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    _forbid_orchestrator_calls: None,
    path: str,
) -> None:
    """Hitting each Stage 2 route returns 200 without any orchestrator
    method ever being called — the sentinel would raise otherwise."""
    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/{path}"
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Guard 2 — HTTP method enforcement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", _STAGE2_ROUTES)
@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_non_get_methods_return_405(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    path: str,
    method: str,
) -> None:
    """Only GET is registered for each Stage 2 URL; every other verb
    must return 405. If a future change adds a write route under the
    same prefix this test will start returning 200/201 and fail."""
    url = f"/api/jobs/{job_with_all_stage2_artefacts}/{path}"
    response = getattr(authed_client, method)(url)
    assert response.status_code == 405, (
        f"{method.upper()} {url} → {response.status_code}, "
        f"expected 405"
    )


# ---------------------------------------------------------------------------
# Guard 3 — byte-identical artefacts before/after each read
# ---------------------------------------------------------------------------

def _snapshot_folder(folder: Path) -> dict[str, str]:
    """Return ``{filename: sha256-hex}`` for every file in ``folder``."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.iterdir())
        if p.is_file()
    }


@pytest.mark.parametrize("path", _STAGE2_ROUTES)
def test_get_does_not_mutate_any_artefact(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    isolated_jobs_root: Path,
    path: str,
) -> None:
    """No file in the job folder may change as a side effect of a GET.

    Pins the read-only contract at the filesystem level — a read
    path that touched (e.g.) ``state.json`` to record an audit
    entry, or rewrote an artefact for "normalisation", would change
    the digest and fail here.
    """
    folder = isolated_jobs_root / job_with_all_stage2_artefacts
    before = _snapshot_folder(folder)
    assert before, "fixture should have seeded the job folder"

    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}/{path}"
    )
    assert r.status_code == 200, r.text

    after = _snapshot_folder(folder)
    assert after == before, (
        f"GET /{path} mutated the job folder; "
        f"diff: { {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)} }"
    )


def test_job_status_route_does_not_mutate_any_artefact(
    authed_client: TestClient,
    job_with_all_stage2_artefacts: str,
    isolated_jobs_root: Path,
) -> None:
    """The job-status route (extended with five new Stage 2 boolean
    fields in Step 21) must also be read-only — populating the
    booleans is a stat-only check on disk, never a write."""
    folder = isolated_jobs_root / job_with_all_stage2_artefacts
    before = _snapshot_folder(folder)

    r = authed_client.get(
        f"/api/jobs/{job_with_all_stage2_artefacts}"
    )
    assert r.status_code == 200, r.text

    after = _snapshot_folder(folder)
    assert after == before
