"""Step 35 — manifest route is schema-agnostic across the v1→v2 bump.

The Step 34 manifest route is a deliberate passthrough — it returns
whatever ``document_manifest.json`` says, verbatim, wrapped in an
envelope. Step 35 bumps the writer schema 1 → 2, but the route MUST
keep accepting on-disk v1 manifests unchanged (no auto-injected
``exports: []``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.jobs.storage import write_document_manifest


_V1_MANIFEST: dict = {
    "schema_version": 1,
    "job_id": "00000000-0000-4000-8000-000000000000",
    "company_name": "Acme Ltd",
    "company_url": "https://acme.example.com/",
    "generated_at": "2026-05-28T00:00:00Z",
    "markdown_filename": "prospect_brief.md",
    "markdown_sha256": "a" * 64,
    "markdown_byte_length": 1234,
    "sections": [],
    "artefacts": {},
    "outputs": [
        {"key": "markdown", "filename": "prospect_brief.md"},
        {"key": "manifest", "filename": "document_manifest.json"},
    ],
    "critic_verdict": None,
    "warnings": [],
}


def test_route_passes_v1_manifest_through_unchanged(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """The route must NOT add ``exports: []`` when the manifest is v1."""
    payload = {**_V1_MANIFEST, "job_id": created_job}
    write_document_manifest(created_job, payload)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manifest"] == payload
    assert "exports" not in body["manifest"]


def test_route_passes_v2_manifest_through_unchanged(
    authed_client: TestClient,
    created_job: str,
) -> None:
    """A v2 manifest with ``exports: []`` must round-trip verbatim."""
    payload = {
        **_V1_MANIFEST,
        "job_id": created_job,
        "schema_version": 2,
        "exports": [],
    }
    write_document_manifest(created_job, payload)
    r = authed_client.get(f"/api/jobs/{created_job}/manifest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manifest"]["schema_version"] == 2
    assert body["manifest"]["exports"] == []
