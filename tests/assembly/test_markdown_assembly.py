"""Tests for the Step 29 Markdown assembler.

The assembler under test is :func:`backend.assembly.markdown.assemble_job`.
It reads every on-disk artefact via the storage helpers and writes two
terminal outputs:

* ``prospect_brief.md``      — human-readable Markdown rollup.
* ``document_manifest.json`` — provenance/integrity record.

The 22 proofs in this file mirror the Step 29 direction exactly:

1.  Successful assembly writes ``prospect_brief.md``.
2.  Successful assembly writes ``document_manifest.json``.
3.  Markdown includes all required section headings in order.
4.  Markdown includes company name and URL.
5.  Markdown includes product mapping content.
6.  Markdown includes benefits content.
7.  Markdown includes FAQ content.
8.  Markdown includes objections content.
9.  Markdown includes critic verdict.
10. Manifest records all six input artefacts.
11. Manifest records both output files.
12. ``READY_WITH_WARNINGS`` assembles and marks warnings.
13. ``NEEDS_REVISION`` assembles but marks internal draft / do not release.
14. Missing non-briefing artefact renders stub and records warning.
15. Malformed non-briefing artefact renders stub and records warning.
16. Missing briefing raises and writes no output.
17. Malformed briefing raises and writes no output.
18. Source artefacts remain byte-identical after assembly.
19. No job status change.
20. No transition appended.
21. No DOCX/PDF/email/M365/delivery artefacts created.
22. Static fence — no Anthropic, Keychain, credentials, CloudClient,
    delivery, M365, frontend, routes, orchestrator, agents (non-schema),
    live model, or network references introduced.

Per the Step 29 direction, fixtures and factories are inlined here:
no imports from other test modules, no shared conftest in
``tests/agents/`` or elsewhere is borrowed for production code. The
only cross-test concern delegated to ``conftest.py`` is the
``ANS_JOBS_ROOT`` env-var isolation, which we re-implement locally so
the assembly suite is self-contained even if a future refactor moves
``tests/jobs/conftest.py`` around.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.agents.benefits_models import (
    Audience,
    BenefitClaim,
    BenefitsBrief,
    BenefitsSection,
)
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
from backend.agents.critic_models import (
    CitedArtefact,
    CriticIssue,
    IssueCategory,
    Severity,
    Stage2CriticReport,
    Verdict,
)
from backend.agents.faq_models import FAQCategory, FAQDocument, FAQEntry
from backend.agents.mapping_models import (
    KnowledgeExcerptRef,
    KnowledgeSource,
    NeedProductMatch,
    ProductLine,
    ProductMapping,
    ProductReference,
    UnmatchedNeed,
)
from backend.agents.needs_models import LabMaturity
from backend.agents.objections_models import (
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
)
from backend.agents.research_models import Confidence
from backend.assembly.markdown import (
    BriefingRequiredError,
    assemble_job,
)
from backend.jobs.storage import (
    job_folder,
    read_state,
    write_benefits,
    write_briefing,
    write_critic_report,
    write_faq,
    write_initial_state,
    write_objections,
    write_product_mapping,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory.

    Identical contract to ``tests/jobs/conftest.py``: no test writes
    under the real ``~/.ans-tool/jobs/``. Reimplemented inline so this
    suite remains self-contained.
    """
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root


@pytest.fixture()
def frozen_now() -> datetime:
    """A fixed wall-clock used by every test that calls
    :func:`assemble_job`. Determinism is contract — see proof #18."""
    return datetime(2026, 5, 28, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Local factories (per Step 29 correction: no imports from
# tests/agents or other test modules into production code; test
# factories live here)
# ---------------------------------------------------------------------------

def _new_job_id() -> str:
    return str(uuid.uuid4())


def _make_briefing() -> Briefing:
    """A small but complete briefing — one source, one of every claim
    type, one ranked need pointing at index 0."""
    sources = SourceRegister(
        entries=[
            SourceEntry(
                url="https://acme.example.com/news/cloud",  # type: ignore[arg-type]
                title="Acme migrates to cloud",
                retrieved_at=date(2026, 5, 27),
                confidence=Confidence.HIGH,
            )
        ],
        gaps=["limited public regulatory info"],
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
            watch_outs=["Watch for incumbent contract renewals."],
        ),
        sources=sources,
    )


def _make_product_mapping() -> ProductMapping:
    return ProductMapping(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        mapped_at=date(2026, 5, 28),
        matches=[
            NeedProductMatch(
                need_priority=1,
                need_summary="Modernise SD-WAN",
                products=[
                    ProductReference(
                        product_line=ProductLine.EMULATORS,
                        product_name="Netropy 100G",
                        knowledge_excerpt_refs=[0],
                    )
                ],
                use_case_framing=(
                    "Lab-validate the new SD-WAN topology end-to-end."
                ),
                why_this_fits=(
                    "Line-rate impairment covers the rollout's "
                    "aggregate bandwidth."
                ),
                confidence=Confidence.HIGH,
            )
        ],
        unmatched_needs=[
            UnmatchedNeed(
                need_priority=2,
                need_summary="Public-cloud cost optimisation",
                reason="Outside ANS's product line today.",
            )
        ],
        knowledge_excerpts=[
            KnowledgeExcerptRef(
                source_file=KnowledgeSource.PRODUCTS_EMULATORS,
                heading="Netropy 100G — capacity",
                rationale="Covers aggregate bandwidth at line rate.",
            )
        ],
        gaps=["No datasheet detail on Netropy 100G jitter floor."],
    )


def _benefit_claim(label: str) -> BenefitClaim:
    return BenefitClaim(
        claim=f"{label} — Netropy emulates realistic SD-WAN topologies.",
        detail=(
            "Line-rate impairment up to 100G covers the rollout's "
            "aggregate bandwidth without truncation."
        ),
        why_it_matters=(
            "Surfaces SLA-breaching jitter before cutover instead of "
            "in production."
        ),
        knowledge_excerpt_refs=[0],
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )


def _make_benefits() -> BenefitsBrief:
    return BenefitsBrief(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 28),
        executive_summary=BenefitsSection(
            heading="Executive summary",
            audience=Audience.CTO_CIO,
            summary="ANS lab-validates the SD-WAN rollout end-to-end.",
            body=[_benefit_claim("EX1")],
        ),
        technical_fit=BenefitsSection(
            heading="Technical fit",
            audience=Audience.ENGINEERS,
            summary="Netropy lines up with the planned topology.",
            body=[_benefit_claim("TF1")],
        ),
        business_case=BenefitsSection(
            heading="Business case",
            audience=Audience.IT_DIRECTOR,
            summary="Catches cutover risk before it hits revenue.",
            body=[_benefit_claim("BC1")],
        ),
        gaps=["No datasheet detail on Netropy 100G jitter floor."],
    )


def _make_faq() -> FAQDocument:
    categories = [
        FAQCategory.ABOUT_ANS,
        FAQCategory.PRODUCTS,
        FAQCategory.IMPLEMENTATION,
        FAQCategory.COMMERCIAL,
        FAQCategory.SUPPORT,
    ]
    entries = [
        FAQEntry(
            question=f"Question #{i}?",
            answer=(
                "ANS-side answer grounded in the briefing and the "
                "knowledge bundle."
            ),
            category=categories[i % len(categories)],
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            confidence=Confidence.HIGH,
        )
        for i in range(12)
    ]
    return FAQDocument(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 28),
        entries=entries,
        gaps=["Thin coverage on commercial discount tiers."],
    )


def _make_objections() -> ObjectionsRegister:
    rows = [
        ObjectionRow(
            category=ObjectionCategory.PRICE,
            objection=f"Stated objection #{i}.",
            underlying_concern=(
                "Worried that procurement won't approve the spend."
            ),
            response=(
                "ANS Netropy emulators are line-rate and "
                "impairment-accurate."
            ),
            knowledge_excerpt_refs=[0],
            briefing_source_refs=[0],
            escalation_path="Account owner brings in solutions architect.",
            confidence=Confidence.HIGH,
        )
        for i in range(15)
    ]
    return ObjectionsRegister(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        written_at=date(2026, 5, 28),
        rows=rows,
        gaps=[],
    )


def _make_critic_ready() -> Stage2CriticReport:
    """READY verdict — empty issues list."""
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 28),
        verdict=Verdict.READY,
        issues=[],
        summary="All three writer artefacts are well-grounded.",
        gaps=[],
    )


def _make_critic_ready_with_warnings() -> Stage2CriticReport:
    """READY_WITH_WARNINGS — at least one WARNING issue, no BLOCKING."""
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 28),
        verdict=Verdict.READY_WITH_WARNINGS,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.BENEFITS,
                locator="executive_summary.body[0].claim",
                severity=Severity.WARNING,
                category=IssueCategory.OFF_BRAND_TONE,
                description="Mildly off-brand phrasing.",
                suggested_fix="Soften the headline claim.",
            )
        ],
        summary="One tone warning; artefacts otherwise ready.",
        gaps=[],
    )


def _make_critic_needs_revision() -> Stage2CriticReport:
    """NEEDS_REVISION — at least one BLOCKING issue."""
    return Stage2CriticReport(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 28),
        verdict=Verdict.NEEDS_REVISION,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.FAQ,
                locator="entries[3].answer",
                severity=Severity.BLOCKING,
                category=IssueCategory.UNSUPPORTED_PRODUCT_CLAIM,
                description="Claim not supported by the knowledge base.",
                suggested_fix="Remove or re-ground the claim.",
            )
        ],
        summary="One blocking issue must be addressed before release.",
        gaps=[],
    )


# ---------------------------------------------------------------------------
# Setup helpers — write a full set of artefacts to the job folder
# ---------------------------------------------------------------------------

def _seed_all_artefacts(
    job_id: str, *, critic: Stage2CriticReport | None = None
) -> None:
    """Write every artefact the assembler can consume.

    ``critic`` defaults to a READY report. Pass an alternative verdict
    to exercise the banner paths.
    """
    write_briefing(job_id, _make_briefing())
    write_product_mapping(job_id, _make_product_mapping())
    write_benefits(job_id, _make_benefits())
    write_faq(job_id, _make_faq())
    write_objections(job_id, _make_objections())
    write_critic_report(
        job_id, critic if critic is not None else _make_critic_ready()
    )


def _seed_initial_state(job_id: str) -> None:
    """Write a baseline ``state.json`` so proofs #19/#20 can pin it."""
    write_initial_state(
        job_id=job_id,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        created_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Proof 1 — Successful assembly writes prospect_brief.md
# ---------------------------------------------------------------------------

def test_assembly_writes_prospect_brief_markdown(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    result = assemble_job(job_id, now=frozen_now)
    assert result.markdown_path == (
        isolated_jobs_root / job_id / "prospect_brief.md"
    )
    assert result.markdown_path.exists()
    body = result.markdown_path.read_text(encoding="utf-8")
    assert body.endswith("\n")
    assert len(body) > 0


# ---------------------------------------------------------------------------
# Proof 2 — Successful assembly writes document_manifest.json
# ---------------------------------------------------------------------------

def test_assembly_writes_document_manifest_json(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    result = assemble_job(job_id, now=frozen_now)
    assert result.manifest_path == (
        isolated_jobs_root / job_id / "document_manifest.json"
    )
    assert result.manifest_path.exists()
    manifest = json.loads(
        result.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["job_id"] == job_id
    assert manifest["generated_at"] == "2026-05-28T14:00:00Z"


# ---------------------------------------------------------------------------
# Proof 3 — Markdown includes all required section headings in order
# ---------------------------------------------------------------------------

def test_markdown_section_headings_appear_in_required_order(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    result = assemble_job(job_id, now=frozen_now)
    body = result.markdown_path.read_text(encoding="utf-8")

    # The "Company/title header" maps to the H1 — the Markdown opens
    # with the company name as an H1. The eight subsequent sections
    # are H2s in this exact order.
    h1_index = body.index("# Acme Ltd")
    expected_h2s = [
        "## Briefing snapshot",
        "## Opportunity summary",
        "## Product mapping",
        "## Benefits",
        "## FAQ",
        "## Objections",
        "## Critic report summary",
        "## Sources and gaps",
    ]
    last = h1_index
    for heading in expected_h2s:
        idx = body.index(heading, last)
        assert idx > last, (
            f"section {heading!r} must follow the previous section"
        )
        last = idx


# ---------------------------------------------------------------------------
# Proof 4 — Markdown includes company name and URL
# ---------------------------------------------------------------------------

def test_markdown_includes_company_name_and_url(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    assert "# Acme Ltd" in body
    assert "https://acme.example.com/" in body


# ---------------------------------------------------------------------------
# Proof 5 — Markdown includes product mapping content
# ---------------------------------------------------------------------------

def test_markdown_includes_product_mapping_content(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    assert "Netropy 100G" in body
    assert "Modernise SD-WAN" in body
    assert "Lab-validate the new SD-WAN topology end-to-end." in body


# ---------------------------------------------------------------------------
# Proof 6 — Markdown includes benefits content
# ---------------------------------------------------------------------------

def test_markdown_includes_benefits_content(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    assert "Executive summary" in body
    assert "Technical fit" in body
    assert "Business case" in body
    assert "EX1 — Netropy emulates realistic SD-WAN topologies." in body
    assert "Audience: CTO_CIO" in body
    assert "Audience: ENGINEERS" in body
    assert "Audience: IT_DIRECTOR" in body


# ---------------------------------------------------------------------------
# Proof 7 — Markdown includes FAQ content
# ---------------------------------------------------------------------------

def test_markdown_includes_faq_content(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    assert "Question #0?" in body
    assert "Question #11?" in body
    # FAQ category badges round-trip through the renderer.
    assert "ABOUT_ANS" in body
    assert "PRODUCTS" in body


# ---------------------------------------------------------------------------
# Proof 8 — Markdown includes objections content
# ---------------------------------------------------------------------------

def test_markdown_includes_objections_content(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    # All 15 rows render; the table header is the six-column shape.
    assert "| Category | Objection | Underlying concern" in body
    assert "Stated objection #0." in body
    assert "Stated objection #14." in body
    assert "Account owner brings in solutions architect." in body


# ---------------------------------------------------------------------------
# Proof 9 — Markdown includes critic verdict
# ---------------------------------------------------------------------------

def test_markdown_includes_critic_verdict(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    assert "## Critic report summary" in body
    assert "**Verdict:** READY" in body


# ---------------------------------------------------------------------------
# Proof 10 — Manifest records all six input artefacts
# ---------------------------------------------------------------------------

def test_manifest_records_all_six_input_artefacts(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    result = assemble_job(job_id, now=frozen_now)
    artefacts = result.manifest["artefacts"]
    expected = {
        "briefing": "briefing.json",
        "product_mapping": "product_mapping.json",
        "benefits": "benefits.json",
        "faq": "faq.json",
        "objections": "objections.json",
        "critic_report": "critic_report.json",
    }
    assert set(artefacts.keys()) == set(expected.keys())
    for key, filename in expected.items():
        assert artefacts[key]["filename"] == filename
        assert artefacts[key]["present"] is True
        assert artefacts[key]["status"] == "present"


# ---------------------------------------------------------------------------
# Proof 11 — Manifest records both output files
# ---------------------------------------------------------------------------

def test_manifest_records_both_output_files(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    result = assemble_job(job_id, now=frozen_now)
    outputs = result.manifest["outputs"]
    filenames = {entry["filename"] for entry in outputs}
    assert filenames == {"prospect_brief.md", "document_manifest.json"}
    assert result.manifest["markdown_filename"] == "prospect_brief.md"


# ---------------------------------------------------------------------------
# Proof 12 — READY_WITH_WARNINGS assembles and marks warnings
# ---------------------------------------------------------------------------

def test_ready_with_warnings_assembles_with_warning_banner(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(
        job_id, critic=_make_critic_ready_with_warnings()
    )
    result = assemble_job(job_id, now=frozen_now)
    body = result.markdown_path.read_text(encoding="utf-8")
    assert "Critic verdict: READY_WITH_WARNINGS" in body
    assert "review the warnings" in body
    assert result.manifest["critic_verdict"] == "READY_WITH_WARNINGS"


# ---------------------------------------------------------------------------
# Proof 13 — NEEDS_REVISION assembles but marks internal draft /
# do not release
# ---------------------------------------------------------------------------

def test_needs_revision_assembles_with_do_not_release_banner(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(
        job_id, critic=_make_critic_needs_revision()
    )
    result = assemble_job(job_id, now=frozen_now)
    body = result.markdown_path.read_text(encoding="utf-8")
    # INTERNAL DRAFT watermark is always there.
    assert "INTERNAL DRAFT" in body
    # Plus the verdict-specific banner.
    assert "Critic verdict: NEEDS_REVISION" in body
    assert "do not release" in body.lower()
    assert result.manifest["critic_verdict"] == "NEEDS_REVISION"


# ---------------------------------------------------------------------------
# Proof: INTERNAL DRAFT watermark on every output (CLAUDE.md hard
# rule #7). Belongs alongside proofs #12/#13 — pinned separately so
# a future cleanup that drops the watermark on the READY path is
# caught immediately.
# ---------------------------------------------------------------------------

def test_markdown_always_carries_internal_draft_watermark(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id, critic=_make_critic_ready())
    body = assemble_job(job_id, now=frozen_now).markdown_path.read_text(
        encoding="utf-8"
    )
    # First non-empty line must carry the watermark so a copy-paste of
    # any prefix still surfaces it.
    first_line = body.splitlines()[0]
    assert "INTERNAL DRAFT" in first_line


# ---------------------------------------------------------------------------
# Proof 14 — Missing non-briefing artefact renders stub and records
# warning
# ---------------------------------------------------------------------------

def test_missing_non_briefing_artefact_renders_stub_and_warning(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    # Remove the benefits artefact post-seed.
    (isolated_jobs_root / job_id / "benefits.json").unlink()

    result = assemble_job(job_id, now=frozen_now)
    body = result.markdown_path.read_text(encoding="utf-8")
    assert "## Benefits" in body
    assert "Section not rendered" in body
    assert "benefits artefact missing" in body

    # Manifest mirrors the skip.
    artefacts = result.manifest["artefacts"]
    assert artefacts["benefits"]["present"] is False
    assert artefacts["benefits"]["status"] == "missing"
    benefits_section = next(
        s for s in result.manifest["sections"]
        if s["key"] == "benefits"
    )
    assert benefits_section["rendered"] is False
    assert "missing" in benefits_section["reason"]
    assert any(
        "benefits" in w.lower() for w in result.manifest["warnings"]
    )


# ---------------------------------------------------------------------------
# Proof 15 — Malformed non-briefing artefact renders stub and warning
# ---------------------------------------------------------------------------

def test_malformed_non_briefing_artefact_renders_stub_and_warning(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    # Corrupt the FAQ artefact in-place — bytes that are neither
    # valid JSON nor a valid FAQDocument.
    (isolated_jobs_root / job_id / "faq.json").write_text(
        "this is not json", encoding="utf-8"
    )

    result = assemble_job(job_id, now=frozen_now)
    body = result.markdown_path.read_text(encoding="utf-8")
    assert "## FAQ" in body
    assert "Section not rendered" in body
    assert "malformed" in body

    artefacts = result.manifest["artefacts"]
    assert artefacts["faq"]["present"] is True  # file exists
    assert artefacts["faq"]["status"] == "malformed"
    faq_section = next(
        s for s in result.manifest["sections"] if s["key"] == "faq"
    )
    assert faq_section["rendered"] is False
    assert "malformed" in faq_section["reason"]
    assert any(
        "faq" in w.lower() for w in result.manifest["warnings"]
    )


# ---------------------------------------------------------------------------
# Proof 16 — Missing briefing raises and writes no output
# ---------------------------------------------------------------------------

def test_missing_briefing_raises_and_writes_no_output(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    # Seed only the optional artefacts (skip the briefing).
    write_product_mapping(job_id, _make_product_mapping())
    write_benefits(job_id, _make_benefits())
    with pytest.raises(BriefingRequiredError):
        assemble_job(job_id, now=frozen_now)
    folder = isolated_jobs_root / job_id
    assert not (folder / "prospect_brief.md").exists()
    assert not (folder / "document_manifest.json").exists()


# ---------------------------------------------------------------------------
# Proof 17 — Malformed briefing raises and writes no output
# ---------------------------------------------------------------------------

def test_malformed_briefing_raises_and_writes_no_output(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    (isolated_jobs_root / job_id / "briefing.json").write_text(
        "not even json", encoding="utf-8"
    )
    with pytest.raises(BriefingRequiredError):
        assemble_job(job_id, now=frozen_now)
    folder = isolated_jobs_root / job_id
    assert not (folder / "prospect_brief.md").exists()
    assert not (folder / "document_manifest.json").exists()


def test_schema_invalid_briefing_raises_and_writes_no_output(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    """Same fence, this time the JSON parses but the schema rejects it."""
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)
    (isolated_jobs_root / job_id / "briefing.json").write_text(
        json.dumps({"company_name": ""}), encoding="utf-8"
    )
    with pytest.raises(BriefingRequiredError):
        assemble_job(job_id, now=frozen_now)


# ---------------------------------------------------------------------------
# Proof 18 — Source artefacts remain byte-identical after assembly +
# Markdown is byte-identical across re-runs at the same frozen clock.
# ---------------------------------------------------------------------------

def test_source_artefacts_unchanged_and_assembly_deterministic(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_all_artefacts(job_id)

    folder = isolated_jobs_root / job_id
    inputs = [
        "briefing.json",
        "product_mapping.json",
        "benefits.json",
        "faq.json",
        "objections.json",
        "critic_report.json",
    ]
    before = {name: (folder / name).read_bytes() for name in inputs}

    first = assemble_job(job_id, now=frozen_now)
    md_first = first.markdown_path.read_bytes()
    manifest_first = first.manifest_path.read_bytes()

    after = {name: (folder / name).read_bytes() for name in inputs}
    assert before == after, "assembler must not mutate source artefacts"

    # Second run — same frozen clock, identical inputs → identical
    # output bytes. This is the load-bearing determinism contract.
    second = assemble_job(job_id, now=frozen_now)
    assert second.markdown_path.read_bytes() == md_first
    assert second.manifest_path.read_bytes() == manifest_first

    # No timestamp ever appears in the markdown body — the manifest is
    # the single carrier of ``generated_at``.
    md_text = first.markdown_path.read_text(encoding="utf-8")
    assert "2026-05-28T14:00:00Z" not in md_text


# ---------------------------------------------------------------------------
# Proof 19 — No job status change
# ---------------------------------------------------------------------------

def test_assembly_does_not_change_job_status(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_initial_state(job_id)
    _seed_all_artefacts(job_id)
    before = read_state(job_id)
    assemble_job(job_id, now=frozen_now)
    after = read_state(job_id)
    assert after.current_state == before.current_state


# ---------------------------------------------------------------------------
# Proof 20 — No transition appended
# ---------------------------------------------------------------------------

def test_assembly_does_not_append_transitions(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_initial_state(job_id)
    _seed_all_artefacts(job_id)
    before = read_state(job_id)
    assemble_job(job_id, now=frozen_now)
    after = read_state(job_id)
    assert after.transitions == before.transitions
    # last_error must also be untouched — assembly is not a failure
    # site for the state machine.
    assert after.last_error == before.last_error


# ---------------------------------------------------------------------------
# Proof 21 — No DOCX/PDF/email/M365/delivery artefacts created
# ---------------------------------------------------------------------------

def test_assembly_does_not_create_docx_pdf_email_or_delivery_artefacts(
    isolated_jobs_root: Path, frozen_now: datetime
) -> None:
    job_id = _new_job_id()
    _seed_initial_state(job_id)
    _seed_all_artefacts(job_id)

    folder = isolated_jobs_root / job_id
    before = sorted(p.name for p in folder.iterdir())

    assemble_job(job_id, now=frozen_now)

    after = sorted(p.name for p in folder.iterdir())

    # Exactly two new files: the Markdown and the manifest. Nothing
    # else (no DOCX, no PDF, no .eml, no Graph-API stub, no XLSX).
    new_files = set(after) - set(before)
    assert new_files == {"prospect_brief.md", "document_manifest.json"}

    # And explicitly: no banned extensions anywhere in the folder.
    banned_extensions = (
        ".docx", ".doc", ".pdf", ".eml", ".msg", ".xlsx", ".xls",
        ".pptx", ".ppt",
    )
    for name in after:
        suffix = Path(name).suffix.lower()
        assert suffix not in banned_extensions, (
            f"unexpected artefact extension on disk: {name}"
        )


# ---------------------------------------------------------------------------
# Proof 22 — Static fence: assembler must not reference any of the
# banned modules / names.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASSEMBLER_PATH = _REPO_ROOT / "backend" / "assembly" / "markdown.py"

_BANNED_IMPORT_ROOTS: frozenset[str] = frozenset({
    "anthropic",
    "keyring",
    "msal",
    "httpx",
    "requests",
    "smtplib",
    "urllib3",
})

_BANNED_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.credentials",
    "backend.cost_control",
    "backend.delivery",
    "backend.routes",
    "backend.main",
    "backend.orchestrator",
    "backend.jobs.orchestrator",
    "backend.agents.research_agent",
    "backend.agents.briefing_agent",
    "backend.agents.contact_agent",
    "backend.agents.needs_agent",
    "backend.agents.mapping_agent",
    "backend.agents.benefits_writer",
    "backend.agents.faq_writer",
    "backend.agents.objections_writer",
    "backend.agents.critic_agent",
    "backend.agents._base",
    "backend.agents._protocols",
)

# Name references that signal banned dependencies even if the import
# was masked (e.g. via importlib). The renderer should never reach
# for any of these.
_BANNED_NAME_REFERENCES: frozenset[str] = frozenset({
    "CloudClient",
    "Anthropic",
    "messages_create",
    "GraphClient",
    "send_drafts",
    "send_mail",
})


def _assembler_tree() -> ast.Module:
    source = _ASSEMBLER_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(_ASSEMBLER_PATH))


def test_assembler_has_no_banned_imports() -> None:
    tree = _assembler_tree()
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                root = name.split(".", 1)[0]
                if root in _BANNED_IMPORT_ROOTS:
                    offences.append(f"import {name}")
                for prefix in _BANNED_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(prefix + "."):
                        offences.append(f"import {name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in _BANNED_IMPORT_ROOTS:
                offences.append(f"from {module} import ...")
            for prefix in _BANNED_IMPORT_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offences.append(f"from {module} import ...")
    assert not offences, (
        f"backend/assembly/markdown.py contains forbidden imports: "
        f"{offences}"
    )


def test_assembler_has_no_banned_name_references() -> None:
    tree = _assembler_tree()
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BANNED_NAME_REFERENCES:
            offences.append(f"Name {node.id}")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _BANNED_NAME_REFERENCES
        ):
            offences.append(f"Attribute .{node.attr}")
    assert not offences, (
        f"backend/assembly/markdown.py references forbidden names: "
        f"{offences}"
    )


def test_assembler_has_no_http_urls_or_api_keys() -> None:
    """Defence-in-depth: the renderer is a local file producer.

    A literal ``http(s)://`` URL in the assembler source would be a
    sign that the renderer is reaching for a network resource. The
    module's own docstring mentions ``~/.ans-tool/`` paths only.
    """
    text = _ASSEMBLER_PATH.read_text(encoding="utf-8")
    assert "http://" not in text
    assert "https://" not in text
    # Anthropic prefix and "sk-" tokens never appear in production
    # source — the global guard in CLAUDE.md hard rule #9.
    assert "sk-ant" not in text
    assert re.search(r"sk-[a-zA-Z0-9]", text) is None
