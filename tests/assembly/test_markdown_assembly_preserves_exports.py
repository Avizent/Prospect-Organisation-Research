"""Step 37 — re-assembly must preserve a prior manifest's ``exports[]``.

The Step 35 assembler always wrote an empty ``exports`` list because the
export layer had not yet shipped. Step 37 changes the contract: if a
prior ``document_manifest.json`` already records one or more
``exports[]`` entries, a re-run of :func:`assemble_job` must carry
those entries forward verbatim. Discarding them would silently lose
provenance for an on-disk PDF that survived the re-assembly.

The staleness of any preserved entry is the lifecycle layer's concern
— this assembler change is purely "do not delete what the operator
already produced".
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.agents.briefing_models import (
    Briefing,
    BriefingClaim,
    BriefingContact,
    BusinessContext,
    ItLandscape,
    KeyPeople,
    Opportunity,
    RankedNeed,
    Snapshot,
    SourceEntry,
    SourceRegister,
)
from backend.agents.needs_models import LabMaturity
from backend.agents.research_models import Confidence
from backend.assembly.markdown import assemble_job
from backend.jobs.storage import (
    read_document_manifest,
    write_briefing,
    write_document_manifest,
)


# ---------------------------------------------------------------------------
# Local fixtures — mirror tests/assembly/test_markdown_assembly.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_jobs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root


@pytest.fixture()
def frozen_now() -> datetime:
    return datetime(2026, 5, 28, 14, 0, 0, tzinfo=timezone.utc)


def _make_briefing() -> Briefing:
    sources = SourceRegister(
        entries=[
            SourceEntry(
                url="https://acme.example.com/news/cloud",  # type: ignore[arg-type]
                title="Acme migrates to cloud",
                retrieved_at=date(2026, 5, 27),
                confidence=Confidence.HIGH,
            )
        ],
        gaps=[],
    )
    return Briefing(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        compiled_at=date(2026, 5, 27),
        snapshot=Snapshot(
            headline="Acme Ltd — modernising network",
            one_line_desc="Industrial controls manufacturer.",
            sector="Industrial manufacturing",
            headcount_band="1000-5000",
            hq_country="United Kingdom",
            ownership="Private",
            lab_maturity=LabMaturity.MODERNISATION_IN_PROGRESS,
            why_interesting_to_ans=(
                "Active SD-WAN job postings align with ANS's network "
                "refresh proposition."
            ),
            confidence=Confidence.HIGH,
        ),
        business_context=BusinessContext(
            news=[
                BriefingClaim(
                    summary="Acme announces cloud migration.",
                    detail="24-month migration programme.",
                    confidence=Confidence.HIGH,
                    source_indices=[0],
                )
            ],
        ),
        it_landscape=ItLandscape(
            lab_maturity_reasoning=(
                "Active SD-WAN job postings plus zero-trust vendor "
                "case study."
            ),
        ),
        key_people=KeyPeople(
            contacts=[
                BriefingContact(
                    name="Alice Example",
                    job_title="CTO",
                    function="CTO",
                    seniority="C_LEVEL",
                    confidence=Confidence.HIGH,
                    source_index=0,
                )
            ],
        ),
        opportunity=Opportunity(
            ranked_needs=[
                RankedNeed(
                    priority=1,
                    summary="Modernise SD-WAN",
                    detail=(
                        "Open Head of SD-WAN posting indicates an "
                        "in-flight programme ANS could accelerate."
                    ),
                    suggested_products=["SD-WAN replacement"],
                    entry_angle="Lead with SD-WAN refresh.",
                    watch_outs=["Existing incumbent contract."],
                    confidence=Confidence.HIGH,
                    source_indices=[0],
                )
            ],
            buying_cycle_stage="Evaluating",
            recommended_angle="Lead with the SD-WAN refresh angle.",
            watch_outs=[],
        ),
        sources=sources,
    )


def _prior_export_entry() -> dict:
    """An export entry shaped like the Step 36 writer's output."""
    return {
        "format": "pdf",
        "filename": "prospect_brief.pdf",
        "byte_length": 12345,
        "sha256": "a" * 64,
        "source_markdown_sha256": "b" * 64,
        "generated_at": "2026-05-27T10:00:00Z",
        "exporter_version": "0.1.0",
        "template_version": "0.1.0",
        "renderer": {"name": "weasyprint", "version": "68.1"},
        "markdown_renderer": {"name": "markdown", "version": "3.10.2"},
        "regeneration_count": 0,
        "warnings": [],
    }


def _seed_briefing(job_id: str) -> None:
    write_briefing(job_id, _make_briefing())


# ---------------------------------------------------------------------------
# Step 37 contract
# ---------------------------------------------------------------------------


def test_first_assembly_writes_empty_exports_list(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    """Baseline — without a prior manifest, ``exports`` is ``[]``.

    Step 35 already pins this; the test is repeated here so a future
    regression that *always* preserves (and inherits stale data from a
    deleted manifest) is caught.
    """
    job_id = "11111111-1111-4111-8111-111111111111"
    _seed_briefing(job_id)

    assemble_job(job_id, now=frozen_now)
    manifest = read_document_manifest(job_id)
    assert manifest["exports"] == []


def test_re_assembly_preserves_prior_exports_array(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    """A prior manifest's ``exports[]`` survives a re-assembly verbatim."""
    job_id = "22222222-2222-4222-8222-222222222222"
    _seed_briefing(job_id)

    # First assembly — writes the manifest with empty exports.
    assemble_job(job_id, now=frozen_now)

    # Hand-edit the manifest to inject a prior export entry, simulating
    # the Step 36 export-writer's effect.
    prior = read_document_manifest(job_id)
    prior["exports"] = [_prior_export_entry()]
    write_document_manifest(job_id, prior)

    # Re-assemble — exports[] must round-trip.
    assemble_job(job_id, now=frozen_now)
    after = read_document_manifest(job_id)
    assert isinstance(after["exports"], list)
    assert len(after["exports"]) == 1
    assert after["exports"][0] == _prior_export_entry()


def test_re_assembly_preserves_multiple_entries(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    """Multiple prior exports[] entries all survive re-assembly."""
    job_id = "33333333-3333-4333-8333-333333333333"
    _seed_briefing(job_id)
    assemble_job(job_id, now=frozen_now)

    prior = read_document_manifest(job_id)
    entry_pdf = _prior_export_entry()
    # A second entry shaped like a different (hypothetical) future
    # exporter — the assembler must not filter by format.
    entry_future = dict(entry_pdf)
    entry_future["format"] = "docx"
    entry_future["filename"] = "prospect_brief.docx"
    prior["exports"] = [entry_pdf, entry_future]
    write_document_manifest(job_id, prior)

    assemble_job(job_id, now=frozen_now)
    after = read_document_manifest(job_id)
    assert after["exports"] == [entry_pdf, entry_future]


def test_re_assembly_with_non_list_exports_falls_back_to_empty(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    """A malformed prior ``exports`` field is treated as absent."""
    job_id = "44444444-4444-4444-8444-444444444444"
    _seed_briefing(job_id)
    assemble_job(job_id, now=frozen_now)

    prior = read_document_manifest(job_id)
    prior["exports"] = "not-a-list"
    write_document_manifest(job_id, prior)

    assemble_job(job_id, now=frozen_now)
    after = read_document_manifest(job_id)
    assert after["exports"] == []
