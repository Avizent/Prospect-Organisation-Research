"""Tests for ``GET /api/jobs/{job_id}/manifest`` (Step 34).

The route exposes the Step 29 assembly companion (``document_manifest.json``)
to the in-app provenance viewer. Like the Step 31 ``/brief/markdown`` route
it is strictly read-only:

* It does not trigger assembly.
* It does not change the job status or append a transition.
* It does not import :mod:`backend.assembly` (proven by the static fence
  in ``test_no_production_client_or_keychain.py``).
* It only reads the on-disk JSON via
  :func:`backend.jobs.storage.read_document_manifest` and wraps the dict
  in a JSON envelope ``{"manifest": {...}}``.

Error contract:

* file present, valid JSON   → 200 with verbatim dict under ``manifest``
* file absent                → 404
* job folder absent          → 404
* non-UUID job_id            → 404
* file present, malformed    → 500 with ``detail.reason == "manifest_corrupt"``

The manifest body is a plain ``dict`` written by the assembler — the route
does not Pydantic-validate the inner shape (the assembler is the single
source of truth), so a *structurally* unusual but *valid-JSON* manifest
still returns 200 verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.jobs.storage import write_document_manifest


# A minimal manifest payload that mirrors the Step 29 shape closely enough
# for the route tests. The values are deliberately fixed so the verbatim
# passthrough assertions can compare exact equality.
_SAMPLE_MANIFEST: dict = {
    "schema_version": 1,
    "job_id": "00000000-0000-4000-8000-000000000000",
    "company_name": "Acme Ltd",
    "company_url": "https://acme.example.com/",
    "generated_at": "2026-05-28T00:00:00Z",
    "markdown_filename": "prospect_brief.md",
    "markdown_sha256": "a" * 64,
    "markdown_byte_length": 1234,
    "sections": [
        {"key": "snapshot", "heading": "Snapshot", "byte_length": 200},
    ],
    "artefacts": {
        "briefing": "briefing.json",
        "product_mapping": "product_mapping.json",
    },
    "outputs": [
        {"key": "markdown", "filename": "prospect_brief.md"},
        {"key": "manifest", "filename": "document_manifest.json"},
    ],
    "critic_verdict": "READY_WITH_WARNINGS",
    "warnings": ["FAQ tone slip in entry 3"],
}


# ---------------------------------------------------------------------------
# Happy path — verbatim envelope and passthrough of every documented field
# ---------------------------------------------------------------------------

def test_route_returns_manifest_envelope_when_file_present(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """File on disk → 200 with the dict wrapped in ``{"manifest": ...}``."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)

    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert set(payload.keys()) == {"manifest"}
    assert payload["manifest"] == _SAMPLE_MANIFEST


def test_route_passes_through_schema_version(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """``schema_version`` round-trips verbatim — the frontend keys off it."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200
    assert r.json()["manifest"]["schema_version"] == 1


def test_route_passes_through_full_markdown_sha256(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The FULL 64-char SHA-256 must be returned — no truncation.

    Step 34 deliberately renders the full hash on the provenance viewer so
    operators can verify integrity by eye. A future "compact" formatting
    change at the route layer would break that workflow.
    """
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200
    sha = r.json()["manifest"]["markdown_sha256"]
    assert sha == "a" * 64
    assert len(sha) == 64


def test_route_passes_through_critic_verdict(
    authed_client: TestClient,
    created_job: str,
) -> None:
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.json()["manifest"]["critic_verdict"] == "READY_WITH_WARNINGS"


def test_route_passes_through_outputs_list(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The ``outputs`` list — the canonical list of files produced —
    must round-trip with element order preserved."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    outputs = r.json()["manifest"]["outputs"]
    assert outputs == _SAMPLE_MANIFEST["outputs"]
    # Order matters — the viewer renders the list in source order.
    assert outputs[0]["key"] == "markdown"
    assert outputs[1]["key"] == "manifest"


def test_route_passes_through_artefacts_dict(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """``artefacts`` — the input-file index — round-trips verbatim."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.json()["manifest"]["artefacts"] == _SAMPLE_MANIFEST["artefacts"]


def test_route_passes_through_warnings_list(
    authed_client: TestClient,
    created_job: str,
) -> None:
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.json()["manifest"]["warnings"] == ["FAQ tone slip in entry 3"]


def test_route_passes_through_empty_warnings(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """An empty warnings list returns as ``[]``, not omitted."""
    payload = dict(_SAMPLE_MANIFEST)
    payload["warnings"] = []
    write_document_manifest(created_job, payload)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.json()["manifest"]["warnings"] == []


def test_route_is_json_content_type(
    authed_client: TestClient,
    created_job: str,
) -> None:
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200
    assert "application/json" in r.headers.get("content-type", "").lower()


def test_route_preserves_unicode_verbatim(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """Non-ASCII glyphs must round-trip with no escaping."""
    payload = dict(_SAMPLE_MANIFEST)
    payload["company_name"] = "Société Générale"
    payload["warnings"] = ["Résumé tone slipped"]
    write_document_manifest(created_job, payload)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200
    body = r.json()["manifest"]
    assert body["company_name"] == "Société Générale"
    assert body["warnings"] == ["Résumé tone slipped"]


# ---------------------------------------------------------------------------
# Missing / unknown → 404
# ---------------------------------------------------------------------------

def test_route_returns_404_when_file_absent(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job has no document_manifest.json → 404."""
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 404, r.text


def test_route_returns_404_for_unknown_job(
    authed_client: TestClient,
) -> None:
    r = authed_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000/manifest"
    )
    assert r.status_code == 404, r.text


def test_route_returns_404_for_non_uuid_job_id(
    authed_client: TestClient,
) -> None:
    """Non-UUID ids are mapped to 404 with no echo of the raw id."""
    r = authed_client.get("/api/jobs/not-a-uuid/manifest")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Corrupt manifest → 500 with detail.reason == "manifest_corrupt"
# ---------------------------------------------------------------------------

def test_route_returns_500_manifest_corrupt_for_invalid_json(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    """File present but unparsable JSON → 500 with structured detail.

    Mirrors the Step 32 ``assembly_failed`` precedent (POST /brief/assemble
    returns 500 with a structured ``detail.reason``) rather than collapsing
    onto 404. Distinguishing absent from corrupt matters: the frontend
    surfaces "manifest missing" vs "manifest broken on disk" differently.
    """
    manifest_path = (
        isolated_jobs_root / created_job / "document_manifest.json"
    )
    manifest_path.write_text("{this is not valid json", encoding="utf-8")

    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 500, r.text
    body = r.json()
    assert body["detail"] == {"reason": "manifest_corrupt"}


def test_route_returns_500_manifest_corrupt_for_truncated_json(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    """A truncated JSON file is still a corruption — 500, not 404."""
    manifest_path = (
        isolated_jobs_root / created_job / "document_manifest.json"
    )
    # Valid prefix that is incomplete — json.loads will raise.
    manifest_path.write_text('{"schema_version": 1, "outputs"', encoding="utf-8")

    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 500, r.text
    assert r.json()["detail"]["reason"] == "manifest_corrupt"


# ---------------------------------------------------------------------------
# Read-only — neither the manifest nor state.json changes
# ---------------------------------------------------------------------------

def test_route_does_not_mutate_the_manifest_on_disk(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    """Three reads must leave the manifest bytes byte-identical."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)
    manifest_path = (
        isolated_jobs_root / created_job / "document_manifest.json"
    )
    before = manifest_path.read_bytes()

    for _ in range(3):
        r = authed_client.get(f"/api/jobs/{created_job}/manifest")
        assert r.status_code == 200, r.text

    after = manifest_path.read_bytes()
    assert before == after, (
        "GET /manifest must not modify document_manifest.json"
    )


def test_route_does_not_mutate_state_json(
    authed_client: TestClient,
    created_job: str,
    isolated_jobs_root: Path,
) -> None:
    """The viewer must not flip the job state machine."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)

    state_path = isolated_jobs_root / created_job / "state.json"
    before = state_path.read_bytes()
    for _ in range(3):
        authed_client.get(f"/api/jobs/{created_job}/manifest")
    after = state_path.read_bytes()

    assert before == after, (
        "GET /manifest must not modify state.json — it does not trigger "
        "assembly, change status, or append transitions"
    )


def test_route_does_not_call_assembly_module() -> None:
    """Belt-and-braces: the static fence already forbids *importing*
    :mod:`backend.assembly` from ``backend.jobs.routes`` (proven via AST
    in ``test_no_production_client_or_keychain.py``). This test goes a
    step further and asserts the new handler does not call the
    assembler's public entry point even by name — a defence against a
    future helper that smuggled the call in via ``getattr`` or a string
    indirection.
    """
    routes_src = (
        Path(__file__).resolve().parents[2]
        / "backend" / "jobs" / "routes.py"
    ).read_text(encoding="utf-8")
    # The assembler's public entry point — verbatim. Any reference to it
    # in the routes module would indicate the manifest route is doing
    # more than reading the on-disk file.
    assert "assemble_prospect_brief" not in routes_src


# ---------------------------------------------------------------------------
# Idempotency — repeated reads return identical responses
# ---------------------------------------------------------------------------

def test_route_is_idempotent_across_repeated_reads(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """Three consecutive reads return identical bodies."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)

    bodies = []
    for _ in range(3):
        r = authed_client.get(f"/api/jobs/{created_job}/manifest")
        assert r.status_code == 200
        bodies.append(r.json())
    assert bodies[0] == bodies[1] == bodies[2]


# ---------------------------------------------------------------------------
# AvailableArtefacts.document_manifest boolean (Step 34 extension)
# ---------------------------------------------------------------------------

def test_available_artefacts_exposes_document_manifest_false_for_fresh_job(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A freshly-created job has no manifest → False."""
    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert "document_manifest" in arts
    assert arts["document_manifest"] is False


def test_available_artefacts_flips_document_manifest_when_written(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """Writing document_manifest.json flips exactly that boolean to True.

    Mirrors the Step 31 ``prospect_brief`` per-artefact flip proof. The two
    flags are deliberately surfaced separately because the Markdown and the
    manifest are written by two distinct storage calls.
    """
    write_document_manifest(created_job, _SAMPLE_MANIFEST)

    r = authed_client.get(f"/api/jobs/{created_job}")
    assert r.status_code == 200, r.text
    arts = r.json()["available_artefacts"]
    assert arts["document_manifest"] is True
    other_keys = set(arts.keys()) - {"document_manifest"}
    for key in other_keys:
        assert arts[key] is False, (
            f"writing document_manifest.json should not flip {key} "
            f"(found {arts[key]})"
        )


def test_writing_prospect_brief_does_not_flip_document_manifest(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The two flags are independent — writing prospect_brief.md alone
    must not flip ``document_manifest``."""
    from backend.jobs.storage import write_prospect_brief_markdown

    write_prospect_brief_markdown(created_job, "# hi\n")

    r = authed_client.get(f"/api/jobs/{created_job}")
    arts = r.json()["available_artefacts"]
    assert arts["prospect_brief"] is True
    assert arts["document_manifest"] is False


def test_writing_document_manifest_does_not_flip_prospect_brief(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The two flags are independent — writing document_manifest.json
    alone must not flip ``prospect_brief``."""
    write_document_manifest(created_job, _SAMPLE_MANIFEST)

    r = authed_client.get(f"/api/jobs/{created_job}")
    arts = r.json()["available_artefacts"]
    assert arts["document_manifest"] is True
    assert arts["prospect_brief"] is False
