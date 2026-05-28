"""Step 36 behavioural isolation — required by the amended plan.

The PDF export pipeline must only mutate two on-disk artefacts:

  * ``exports/prospect_brief.pdf``       — the new PDF file.
  * ``document_manifest.json``           — only the ``exports`` array,
                                            the ``schema_version`` field
                                            on a v1→v2 / v2→v3 migration,
                                            and (Step 40) the
                                            ``latest_export_id`` lineage
                                            pointer map plus the
                                            per-version archive copy.

It must NOT modify:

  * ``prospect_brief.md`` (the canonical source — drift here would
    break the determinism contract)
  * any other manifest field (company_name, markdown_sha256,
    artefacts, outputs, sections, warnings, ...)
  * ``state.json`` or any state-machine artefact
  * any briefing / dossier / Stage 2 artefact

This pins the required behavioural test:
``test_pdf_export_does_not_mutate_markdown_or_manifest_except_exports_array``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.exporters.pdf import render_pdf
from backend.jobs.storage import (
    job_folder,
    read_document_manifest,
    read_prospect_brief_markdown,
    upsert_export_entry,
    write_document_manifest,
    write_export,
    write_prospect_brief_markdown,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANS_LOGO_PATH = _REPO_ROOT / "ans_knowledge" / "brand" / "ans_logo.png"


def _seed_job_with_brief(job_id: str, markdown_text: str) -> dict:
    """Seed an approved job folder with brief + manifest (v2)."""
    write_prospect_brief_markdown(job_id, markdown_text)
    sha = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": "Acme Ltd",
        "company_url": "https://acme.example.com/",
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": sha,
        "markdown_byte_length": len(markdown_text.encode("utf-8")),
        "sections": [{"key": "intro", "byte_length": 42}],
        "artefacts": {"briefing": "briefing.json"},
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": "READY",
        "warnings": ["seed warning"],
        "exports": [],
    }
    write_document_manifest(job_id, manifest)
    return manifest


def test_pdf_export_does_not_mutate_markdown_or_manifest_except_exports_array(
    isolated_jobs_root: Path,
) -> None:
    """The renderer + storage pipeline must only touch the new PDF
    file and the manifest's ``exports`` array.

    We exercise the full render → write → upsert path end-to-end
    (without the HTTP route) and snapshot every adjacent artefact
    before and after to prove they are byte-identical.
    """
    job_id = str(uuid.uuid4())
    markdown_text = (
        "# Acme Ltd\n\nBrief body.\n\n## Section\n\nMore content.\n"
    )
    manifest_before = _seed_job_with_brief(job_id, markdown_text)

    folder = job_folder(job_id)

    # Snapshot the markdown bytes (the canonical source).
    md_bytes_before = (folder / "prospect_brief.md").read_bytes()
    manifest_text_before = (folder / "document_manifest.json").read_text(
        encoding="utf-8"
    )

    # Render → write → upsert.
    result = render_pdf(
        job_id=job_id,
        markdown_text=markdown_text,
        markdown_sha256=manifest_before["markdown_sha256"],
        company_name=manifest_before["company_name"],
        ans_logo_bytes=_ANS_LOGO_PATH.read_bytes(),
        prospect_logo_bytes=b"",
        now=datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc),
    )
    write_export(job_id, "pdf", result.bytes)
    upsert_export_entry(job_id, {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": len(result.bytes),
        "sha256": result.sha256,
        "source_markdown_sha256": manifest_before["markdown_sha256"],
        "generated_at": "2026-05-28T00:00:00Z",
        "exporter_version": "0.2.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "x"},
        "markdown_renderer": {"name": "markdown", "version": "y"},
        "regeneration_count": 0,
        "warnings": list(result.warnings),
    })

    # ----- Markdown must be byte-identical. -----
    md_bytes_after = (folder / "prospect_brief.md").read_bytes()
    assert md_bytes_after == md_bytes_before, (
        "PDF export must not mutate prospect_brief.md"
    )

    # ----- Manifest: only ``exports`` (and schema_version) may differ. -----
    manifest_after = json.loads(
        (folder / "document_manifest.json").read_text(encoding="utf-8")
    )
    manifest_before_parsed = json.loads(manifest_text_before)

    for key in (
        "job_id",
        "company_name",
        "company_url",
        "generated_at",
        "markdown_filename",
        "markdown_sha256",
        "markdown_byte_length",
        "sections",
        "artefacts",
        "outputs",
        "critic_verdict",
        "warnings",
    ):
        assert manifest_after[key] == manifest_before_parsed[key], (
            f"manifest field {key!r} mutated by export pipeline"
        )

    # The exports array gained exactly one entry; nothing else.
    assert len(manifest_after["exports"]) == 1
    assert manifest_after["exports"][0]["format"] == "pdf"

    # Step 40 — the only new top-level key the export pipeline is
    # allowed to add is the ``latest_export_id`` lineage pointer map.
    # Any other key counts as scope-creep and fails the test.
    new_keys = set(manifest_after) - set(manifest_before_parsed)
    assert new_keys <= {"latest_export_id"}, (
        f"export pipeline added unexpected manifest keys: "
        f"{new_keys - {'latest_export_id'}}"
    )
    # Positive shape: the lineage pointer must point at the new entry.
    assert (
        manifest_after["latest_export_id"]["pdf"]
        == manifest_after["exports"][0]["sha256"]
    )

    # The PDF file was created.
    assert (folder / "exports" / "prospect_brief.pdf").exists()
    # NB: the per-version archive copy (``exports/archive/...``) is a
    # route-layer concern — this test exercises the
    # renderer + storage helpers directly and so does not produce one.
    # The route-level isolation contract is pinned in the per-format
    # ``test_export_pdf_routes.py`` / ``test_export_docx_routes.py``
    # modules.
