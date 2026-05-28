"""Step 39 — tests for the shared export-orchestration internals.

The Step 39 consolidation pulled three helpers out of the per-format
PDF/DOCX route handlers:

  * :func:`backend.jobs.export_routes._generate_export`
  * :func:`backend.jobs.export_routes._retrieve_export`
  * :func:`backend.jobs.export_routes._compute_stale_headers`

…all dispatched via :data:`backend.exporters.registry.EXPORT_RENDERERS`.
Behavioural coverage of the public routes is unchanged
(``tests/jobs_routes/test_export_pdf_routes.py`` and
``test_export_docx_routes.py`` exercise both formats through the HTTP
surface), but this module pins a few helper-level invariants the
public tests don't directly check:

  * The amendment-1 invariant: the format set is the SAME frozenset
    across the registry, the lifecycle taxonomy, the storage
    constants, and the route module. A future renderer that lands in
    only some of those places fails fast here.
  * The registry's ``RendererSpec`` shape is honoured for each format.
  * ``_compute_stale_headers`` returns the three documented header
    keys on a clean manifest, a drifted manifest, and a missing
    manifest.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.exporters.lifecycle import _KNOWN_EXPORT_FORMATS
from backend.exporters.registry import EXPORT_RENDERERS, RendererSpec
from backend.jobs.export_routes import (
    ROUTE_FORMATS,
    _compute_stale_headers,
)
from backend.jobs.storage import (
    _EXPORT_FILENAMES,
    _EXPORT_FORMATS,
    write_document_manifest,
    write_prospect_brief_markdown,
)


# ---------------------------------------------------------------------------
# Amendment invariant — the four format sets MUST agree.
# ---------------------------------------------------------------------------

def test_registry_formats_match_route_formats() -> None:
    """The closed format taxonomy lives in four places. They MUST agree.

    Any future renderer (or removal of an existing one) must touch all
    four sites. If they drift, the operator gets undefined behaviour
    somewhere along the chain — e.g. a renderer the lifecycle doesn't
    recognise, or a storage helper that refuses writes for a registered
    renderer. This test is the mechanical enforcement of the
    "single-source-of-truth" architecture rule.
    """
    expected = frozenset({"pdf", "docx"})
    assert expected == frozenset(EXPORT_RENDERERS.keys())
    assert expected == _KNOWN_EXPORT_FORMATS
    assert expected == _EXPORT_FORMATS
    assert expected == ROUTE_FORMATS


# ---------------------------------------------------------------------------
# RendererSpec — shape per format.
# ---------------------------------------------------------------------------

def test_registry_pdf_spec_shape() -> None:
    spec = EXPORT_RENDERERS["pdf"]
    assert isinstance(spec, RendererSpec)
    assert callable(spec.render)
    assert callable(spec.renderer_provenance)
    assert callable(spec.markdown_renderer_provenance)
    # PDF has no on-disk binary template.
    assert spec.template_sha256 is None
    assert spec.filename == "prospect_brief.pdf"
    assert spec.filename == _EXPORT_FILENAMES["pdf"]
    assert spec.media_type == "application/pdf"


def test_registry_docx_spec_shape() -> None:
    spec = EXPORT_RENDERERS["docx"]
    assert isinstance(spec, RendererSpec)
    assert callable(spec.render)
    assert callable(spec.renderer_provenance)
    assert callable(spec.markdown_renderer_provenance)
    # DOCX carries a callable returning the on-disk base.docx SHA-256.
    assert callable(spec.template_sha256)
    sha = spec.template_sha256()
    assert isinstance(sha, str)
    assert len(sha) == 64
    assert spec.filename == "prospect_brief.docx"
    assert spec.filename == _EXPORT_FILENAMES["docx"]
    assert spec.media_type == (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )


def test_registry_renderer_provenance_returns_dict() -> None:
    """Both renderer-provenance projectors return a {name, version} dict."""
    for fmt in ("pdf", "docx"):
        prov = EXPORT_RENDERERS[fmt].renderer_provenance()
        assert isinstance(prov, dict)
        assert "name" in prov and "version" in prov
        assert isinstance(prov["name"], str) and isinstance(prov["version"], str)


def test_registry_markdown_renderer_provenance_aligned() -> None:
    """Both formats use the same pinned ``markdown`` renderer."""
    pdf_md = EXPORT_RENDERERS["pdf"].markdown_renderer_provenance()
    docx_md = EXPORT_RENDERERS["docx"].markdown_renderer_provenance()
    assert pdf_md["name"] == "markdown"
    assert docx_md["name"] == "markdown"
    # And the same version — the renderers share the same pinned dep.
    assert pdf_md["version"] == docx_md["version"]


# ---------------------------------------------------------------------------
# _compute_stale_headers — degrades to "false" when manifest absent.
# ---------------------------------------------------------------------------

_MD_TEXT = "# Acme\n\nBody.\n"


def _seed_manifest(job_id: str, *, markdown_sha: str | None) -> None:
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": markdown_sha or "",
        "markdown_byte_length": len(_MD_TEXT.encode("utf-8")),
        "sections": [],
        "artefacts": {},
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": None,
        "warnings": [],
        "exports": [],
    }
    write_document_manifest(job_id, manifest)


def test_compute_stale_headers_returns_three_keys_on_clean_manifest(
    authed_client: TestClient,  # noqa: ARG001 — used to bootstrap storage
    approved_job: str,
) -> None:
    import hashlib

    write_prospect_brief_markdown(approved_job, _MD_TEXT)
    md_sha = hashlib.sha256(_MD_TEXT.encode("utf-8")).hexdigest()
    _seed_manifest(approved_job, markdown_sha=md_sha)

    headers = _compute_stale_headers(job_id=approved_job, fmt="pdf")
    assert set(headers.keys()) == {
        "X-Export-Stale",
        "X-Export-Stale-Reasons",
        "X-Export-Source-Markdown-SHA256",
    }
    assert headers["X-Export-Stale"] == "false"
    assert headers["X-Export-Stale-Reasons"] == ""
    assert headers["X-Export-Source-Markdown-SHA256"] == md_sha


def test_compute_stale_headers_surfaces_drift_when_markdown_tampered(
    authed_client: TestClient,  # noqa: ARG001
    approved_job: str,
) -> None:
    import hashlib

    write_prospect_brief_markdown(approved_job, _MD_TEXT)
    md_sha = hashlib.sha256(_MD_TEXT.encode("utf-8")).hexdigest()
    _seed_manifest(approved_job, markdown_sha=md_sha)

    # Tamper with the Markdown on disk so the manifest's recorded sha
    # no longer matches.
    write_prospect_brief_markdown(approved_job, _MD_TEXT + "edit\n")
    headers = _compute_stale_headers(job_id=approved_job, fmt="pdf")
    assert headers["X-Export-Stale"] == "true"
    assert "source_markdown_drift" in headers["X-Export-Stale-Reasons"]
    assert headers["X-Export-Source-Markdown-SHA256"] == md_sha


def test_compute_stale_headers_degrades_when_manifest_absent(
    authed_client: TestClient,  # noqa: ARG001
    approved_job: str,
) -> None:
    """No manifest yet — staleness defaults to ``false`` rather than 500."""
    headers = _compute_stale_headers(job_id=approved_job, fmt="pdf")
    assert headers["X-Export-Stale"] == "false"
    assert headers["X-Export-Stale-Reasons"] == ""
    assert headers["X-Export-Source-Markdown-SHA256"] == ""
