"""Tests for ``GET /api/jobs/{job_id}/brief/markdown`` (Step 31).

The route exposes the Step 29 assembly output (``prospect_brief.md``)
to the in-app viewer. It is strictly read-only:

* It does not trigger assembly.
* It does not change the job status or append a transition.
* It does not import ``backend.assembly`` (proven by the static fence
  in ``test_no_production_client_or_keychain.py``).
* It only reads the on-disk Markdown via
  :func:`backend.jobs.storage.read_prospect_brief_markdown` and wraps
  the body in a JSON envelope ``{"markdown": "..."}``.

Error contract:

* file present     → 200 with the verbatim UTF-8 body
* file absent      → 404
* job folder absent → 404
* non-UUID job_id  → 404

The Markdown body is *display* output — there is no Pydantic round-trip
and no on-disk schema, so there is no 500 corruption mode at this layer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.jobs.storage import write_prospect_brief_markdown


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_route_returns_markdown_envelope_when_file_present(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """File on disk → 200 with the body wrapped in ``{"markdown": ...}``."""
    body_text = (
        "**INTERNAL DRAFT — not for external release.**\n\n"
        "# Acme Ltd\n\n"
        "Body paragraph.\n"
    )
    write_prospect_brief_markdown(created_job, body_text)

    r = authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert set(payload.keys()) == {"markdown"}
    assert payload["markdown"] == body_text


def test_route_preserves_unicode_verbatim(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """Non-ASCII glyphs must round-trip with no escaping."""
    body_text = "# Société Générale — résumé\n"
    write_prospect_brief_markdown(created_job, body_text)

    r = authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
    assert r.status_code == 200, r.text
    assert r.json()["markdown"] == body_text


def test_route_is_a_json_response_not_text_markdown(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The response is JSON, not ``text/markdown``.

    The frontend's ``request()`` helper parses with ``response.json()``
    — switching the content type would break it.
    """
    write_prospect_brief_markdown(created_job, "# hello\n")
    r = authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
    assert r.status_code == 200, r.text
    content_type = r.headers.get("content-type", "")
    assert "application/json" in content_type.lower(), (
        f"expected JSON content-type, got {content_type!r}"
    )


# ---------------------------------------------------------------------------
# Missing / unknown — both surface as 404
# ---------------------------------------------------------------------------

def test_route_returns_404_when_file_absent(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job has no prospect_brief.md → 404."""
    r = authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
    assert r.status_code == 404, r.text


def test_route_returns_404_for_unknown_job(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000/brief/markdown"
    )
    assert r.status_code == 404, r.text


def test_route_returns_404_for_non_uuid_job_id(
    authed_client: TestClient,
) -> None:
    """Non-UUID ids are mapped to 404 with no echo of the raw id."""
    r = authed_client.get("/api/jobs/not-a-uuid/brief/markdown")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Read-only — neither the file nor state.json changes
# ---------------------------------------------------------------------------

def test_route_does_not_mutate_the_markdown_on_disk(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    body_text = "# Original\n\nbody.\n"
    write_prospect_brief_markdown(created_job, body_text)

    before = (
        isolated_jobs_root / created_job / "prospect_brief.md"
    ).read_bytes()

    for _ in range(3):
        r = authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
        assert r.status_code == 200, r.text

    after = (
        isolated_jobs_root / created_job / "prospect_brief.md"
    ).read_bytes()
    assert before == after, (
        "GET /brief/markdown must not modify the on-disk Markdown"
    )


def test_route_does_not_mutate_state_json(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    """The viewer must not flip the job state machine.

    We snapshot state.json bytes before/after the read; the route is
    read-only so the bytes must be identical.
    """
    write_prospect_brief_markdown(created_job, "# hi\n")

    state_path = isolated_jobs_root / created_job / "state.json"
    before = state_path.read_bytes()
    for _ in range(3):
        authed_client.get(f"/api/jobs/{created_job}/brief/markdown")
    after = state_path.read_bytes()

    assert before == after, (
        "GET /brief/markdown must not modify state.json — it does not "
        "trigger assembly, change status, or append transitions"
    )


# ---------------------------------------------------------------------------
# AvailableArtefacts.prospect_brief boolean (Step 31 extension)
# ---------------------------------------------------------------------------

def test_available_artefacts_exposes_prospect_brief_false_for_fresh_job(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job has no assembled brief → False."""
    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert "prospect_brief" in arts
    assert arts["prospect_brief"] is False


def test_available_artefacts_flips_prospect_brief_when_md_written(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """Writing prospect_brief.md flips exactly that boolean to True.

    Mirrors the Stage 2 per-artefact flip proof in
    ``test_available_artefacts_stage2.py``.
    """
    write_prospect_brief_markdown(created_job, "# hi\n")

    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert arts["prospect_brief"] is True
    # Nothing else flipped — Stage 1 + Stage 2 still False for a fresh
    # job that only had the Markdown seeded.
    other_keys = set(arts.keys()) - {"prospect_brief"}
    for key in other_keys:
        assert arts[key] is False, (
            f"writing prospect_brief.md should not flip {key} "
            f"(found {arts[key]})"
        )
