"""Shared fixtures for ``tests/jobs_routes/``.

Each test gets:
  - a fresh migrated SQLite DB under ``tmp_path``
  - ``get_db`` overridden onto that DB
  - the active keyring swapped for an in-memory backend (defence —
    no route in this package may touch the real macOS Keychain)
  - ``ANS_JOBS_ROOT`` pointed at a per-test ``tmp_path`` (defence —
    no route may write into the user's real ``~/.ans-tool/jobs/``)
  - an authenticated TestClient (setup completed + logged in)

State-specific job factory fixtures mirror those in
``tests/approval/conftest.py``: ``created_job``, ``researching_job``,
``briefing_ready_job``, ``editing_job``, ``regenerating_job``,
``approved_job``. The approval suite already proves these
state-walking helpers compose cleanly; we deliberately re-declare
them here rather than import across suites so the two test packages
remain decoupled (the imports would be a one-way coupling that
would haunt future refactors).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import keyring
import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from sqlalchemy.orm import Session, sessionmaker

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
from backend.agents.contact_models import (
    ContactCandidate,
    ContactExtractionResult,
    Function,
    Seniority,
)
from backend.agents.critic_models import (
    CitedArtefact,
    CriticIssue,
    IssueCategory,
    Severity,
    Stage2CriticReport,
    Verdict,
)
from backend.agents.faq_models import (
    FAQCategory,
    FAQDocument,
    FAQEntry,
)
from backend.agents.mapping_models import (
    KnowledgeExcerptRef,
    KnowledgeSource,
    NeedProductMatch,
    ProductLine,
    ProductMapping,
    ProductReference,
    UnmatchedNeed,
)
from backend.agents.needs_models import (
    IdentifiedNeed,
    LabMaturity,
    NeedsAssessment,
)
from backend.agents.objections_models import (
    ObjectionCategory,
    ObjectionRow,
    ObjectionsRegister,
)
from backend.agents.research_models import (
    Confidence,
    Finding,
    ResearchDossier,
    Source,
)
from backend.db.session import get_db
from backend.jobs.state import JobState, apply_transition
from backend.jobs.storage import (
    append_transition,
    write_benefits,
    write_briefing,
    write_contacts,
    write_critic_report,
    write_document_manifest,
    write_dossier,
    write_faq,
    write_needs_assessment,
    write_objections,
    write_product_mapping,
    write_prospect_brief_markdown,
)
from backend.main import app


# ---------------------------------------------------------------------------
# Setup constants — mirror tests/admin/conftest.py
# ---------------------------------------------------------------------------

VALID_PASSWORD = "CorrectHorse123!"
VALID_USERNAME = "ans-admin"
VALID_RECOVERY = "rcp@example.com"


# ---------------------------------------------------------------------------
# Jobs root — every test gets an isolated folder
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory."""
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# In-memory keyring (defence — no real keyring access from any route)
# ---------------------------------------------------------------------------

class _InMemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def _fake_keyring() -> Iterator[_InMemoryKeyring]:
    original = keyring.get_keyring()
    backend = _InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


# ---------------------------------------------------------------------------
# DB engine + sessionmaker
# ---------------------------------------------------------------------------

def _alembic_cfg(db_path: Path) -> Config:
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "jobs_routes_test.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db_session(session_factory) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    def _override_get_db() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def authed_client(client: TestClient) -> TestClient:
    """A logged-in TestClient that has completed first-run setup."""
    r = client.post(
        "/auth/setup",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "recovery_email": VALID_RECOVERY,
        },
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def anon_client(client: TestClient) -> TestClient:
    """A TestClient with no authenticated session — used for 401 checks."""
    return client


# ---------------------------------------------------------------------------
# Sample artefacts — minimal valid fixtures for the four readers
# ---------------------------------------------------------------------------

_COMPANY_NAME = "Acme Ltd"
_COMPANY_URL = "https://acme.example.com/"


@pytest.fixture()
def sample_briefing() -> Briefing:
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
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
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


@pytest.fixture()
def sample_dossier() -> ResearchDossier:
    return ResearchDossier(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        retrieved_at=date(2026, 5, 27),
        job_postings=[
            Finding(
                summary="Head of SD-WAN role open",
                detail="Acme is hiring for a Head of SD-WAN.",
                confidence=Confidence.HIGH,
                sources=[
                    Source(
                        url="https://acme.example.com/careers/sdwan",  # type: ignore[arg-type]
                        title="Head of SD-WAN",
                        retrieved_at=date(2026, 5, 27),
                        confidence=Confidence.HIGH,
                    )
                ],
            )
        ],
        gaps=[],
    )


@pytest.fixture()
def sample_contacts() -> ContactExtractionResult:
    return ContactExtractionResult(
        company_name=_COMPANY_NAME,
        contacts=[
            ContactCandidate(
                name="Alice Example",
                job_title="CTO",
                function=Function.CTO,
                seniority=Seniority.C_LEVEL,
                source_url="https://acme.example.com/about/leadership",  # type: ignore[arg-type]
                retrieved_at=date(2026, 5, 27),
                confidence=Confidence.HIGH,
            )
        ],
        gaps=[],
    )


@pytest.fixture()
def sample_needs() -> NeedsAssessment:
    return NeedsAssessment(
        company_name=_COMPANY_NAME,
        lab_maturity=LabMaturity.MODERNISATION_IN_PROGRESS,
        lab_maturity_reasoning="Open SD-WAN role plus vendor case study.",
        lab_maturity_evidence=[],
        lab_maturity_confidence=Confidence.HIGH,
        needs=[
            IdentifiedNeed(
                summary="Modernise SD-WAN",
                detail="In-flight SD-WAN refresh implied by hiring.",
                priority=1,
                evidence=[],
                confidence=Confidence.HIGH,
            )
        ],
        gaps=[],
    )


# ---------------------------------------------------------------------------
# Sample Stage 2 artefacts — minimal valid fixtures for the five readers
# ---------------------------------------------------------------------------
#
# These mirror the sample-model builders in
# ``tests/jobs/test_storage_product_mapping.py``,
# ``tests/jobs/test_storage_writers.py``, and
# ``tests/jobs/test_storage_critic.py``. Kept minimal and re-used as
# fixtures so individual route tests can seed exactly the files they
# need (e.g. an "objections present, everything else absent" job for
# per-artefact AvailableArtefacts assertions).

@pytest.fixture()
def sample_product_mapping() -> ProductMapping:
    return ProductMapping(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        mapped_at=date(2026, 5, 27),
        matches=[
            NeedProductMatch(
                need_priority=1,
                need_summary="De-risk SD-WAN rollout",
                products=[
                    ProductReference(
                        product_line=ProductLine.EMULATORS,
                        product_name="Netropy 100G",
                        knowledge_excerpt_refs=[0],
                    )
                ],
                use_case_framing=(
                    "Lab-emulate the planned SD-WAN topology before "
                    "cutover."
                ),
                why_this_fits=(
                    "Briefing flags an active SD-WAN programme; "
                    "Netropy covers the bandwidth and impairment range."
                ),
                confidence=Confidence.HIGH,
            )
        ],
        unmatched_needs=[
            UnmatchedNeed(
                need_priority=2,
                need_summary="Refresh SIEM tooling",
                reason="Outside ANS lab/network testing portfolio.",
            )
        ],
        knowledge_excerpts=[
            KnowledgeExcerptRef(
                source_file=KnowledgeSource.PRODUCTS_EMULATORS,
                heading="Netropy Network Emulators",
                rationale="Bandwidth and impairment range for SD-WAN.",
            ),
        ],
        gaps=[],
    )


def _benefit_claim() -> BenefitClaim:
    return BenefitClaim(
        claim="Netropy emulates realistic SD-WAN topologies.",
        detail=(
            "Line-rate impairment up to 100G covers the rollout's "
            "aggregate bandwidth without truncation."
        ),
        why_it_matters=(
            "Surfaces SLA-breaching jitter before cutover instead "
            "of in production."
        ),
        knowledge_excerpt_refs=[0],
        briefing_source_refs=[0],
        confidence=Confidence.HIGH,
    )


@pytest.fixture()
def sample_benefits() -> BenefitsBrief:
    return BenefitsBrief(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        executive_summary=BenefitsSection(
            heading="Executive summary",
            audience=Audience.CTO_CIO,
            summary="ANS lab-validates the SD-WAN rollout end-to-end.",
            body=[_benefit_claim()],
        ),
        technical_fit=BenefitsSection(
            heading="Technical fit",
            audience=Audience.ENGINEERS,
            summary="Netropy lines up with the planned topology.",
            body=[_benefit_claim()],
        ),
        business_case=BenefitsSection(
            heading="Business case",
            audience=Audience.IT_DIRECTOR,
            summary="Catches cutover risk before it hits revenue.",
            body=[_benefit_claim()],
        ),
        gaps=[],
    )


@pytest.fixture()
def sample_faq() -> FAQDocument:
    """Populated FAQ — 12 entries (the schema floor)."""
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
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        entries=entries,
        gaps=[],
    )


@pytest.fixture()
def sample_objections() -> ObjectionsRegister:
    """Populated register — 15 rows (the schema floor)."""
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
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        written_at=date(2026, 5, 27),
        rows=rows,
        gaps=[],
    )


@pytest.fixture()
def sample_critic_report() -> Stage2CriticReport:
    """One WARNING issue → READY_WITH_WARNINGS verdict.

    The verdict/severity cross-field validator on
    :class:`Stage2CriticReport` accepts this pairing; a tampered
    report that pairs (e.g.) ``verdict = READY`` with a ``BLOCKING``
    issue would be rejected at read time — see
    :mod:`tests.jobs_routes.test_stage2_artefact_reads`.
    """
    return Stage2CriticReport(
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,  # type: ignore[arg-type]
        reviewed_at=date(2026, 5, 27),
        verdict=Verdict.READY_WITH_WARNINGS,
        issues=[
            CriticIssue(
                artefact=CitedArtefact.FAQ,
                locator="entries[3].answer",
                severity=Severity.WARNING,
                category=IssueCategory.OFF_BRAND_TONE,
                description=(
                    "Answer veers into marketing-speak; tighten to a "
                    "senior pre-sales register."
                ),
                suggested_fix=(
                    "Replace 'world-class' with a concrete capability "
                    "claim grounded in the datasheet."
                ),
            ),
        ],
        summary=(
            "Artefacts hang together; one tone slip in the FAQ that "
            "an operator should clean up before sending."
        ),
        gaps=[],
    )


# ---------------------------------------------------------------------------
# Job-state factory — go through real intake, then walk legal edges
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _create_job(db: Session) -> str:
    from backend.jobs.intake import create_job

    return create_job(
        db=db,
        company_name=_COMPANY_NAME,
        company_url=_COMPANY_URL,
        now=_FIXED_NOW,
    ).job_id


def _advance(db: Session, job_id: str, *, to: JobState) -> None:
    """Walk the job from CREATED → ``to`` via legal edges."""
    from backend.db.models import Job

    paths: dict[JobState, list[JobState]] = {
        JobState.CREATED: [JobState.CREATED],
        JobState.RESEARCHING: [JobState.CREATED, JobState.RESEARCHING],
        JobState.BRIEFING_READY: [
            JobState.CREATED,
            JobState.RESEARCHING,
            JobState.BRIEFING_READY,
        ],
        JobState.USER_EDITING: [
            JobState.CREATED,
            JobState.RESEARCHING,
            JobState.BRIEFING_READY,
            JobState.USER_EDITING,
        ],
        JobState.REGENERATING_SECTION: [
            JobState.CREATED,
            JobState.RESEARCHING,
            JobState.BRIEFING_READY,
            JobState.USER_EDITING,
            JobState.REGENERATING_SECTION,
        ],
        JobState.APPROVED: [
            JobState.CREATED,
            JobState.RESEARCHING,
            JobState.BRIEFING_READY,
            JobState.APPROVED,
        ],
        JobState.FAILED: [
            JobState.CREATED,
            JobState.RESEARCHING,
            JobState.FAILED,
        ],
    }
    seq = paths[to]
    for prev, nxt in zip(seq, seq[1:]):
        record = apply_transition(
            from_state=prev,
            to_state=nxt,
            reason=f"fixture: {prev.value}→{nxt.value}",
            now=_FIXED_NOW,
        )
        append_transition(job_id, record)

    job = db.execute(sa.select(Job).where(Job.id == job_id)).scalar_one()
    job.status = to.value
    db.commit()


@pytest.fixture()
def created_job(db_session: Session) -> str:
    return _create_job(db_session)


@pytest.fixture()
def researching_job(db_session: Session) -> str:
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.RESEARCHING)
    return job_id


@pytest.fixture()
def briefing_ready_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.BRIEFING_READY)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def editing_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.USER_EDITING)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def regenerating_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.REGENERATING_SECTION)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def approved_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.APPROVED)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def job_with_all_artefacts(
    db_session: Session,
    sample_briefing: Briefing,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs: NeedsAssessment,
) -> str:
    """A briefing_ready job that has all four artefacts seeded on disk."""
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.BRIEFING_READY)
    write_dossier(job_id, sample_dossier)
    write_contacts(job_id, sample_contacts)
    write_needs_assessment(job_id, sample_needs)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def job_with_all_stage2_artefacts(
    db_session: Session,
    sample_briefing: Briefing,
    sample_dossier: ResearchDossier,
    sample_contacts: ContactExtractionResult,
    sample_needs: NeedsAssessment,
    sample_product_mapping: ProductMapping,
    sample_benefits: BenefitsBrief,
    sample_faq: FAQDocument,
    sample_objections: ObjectionsRegister,
    sample_critic_report: Stage2CriticReport,
) -> str:
    """An approved job with every Stage 1 and Stage 2 artefact on disk.

    Step 21 reads Stage 2 artefacts produced *after* approval, so we
    walk the job to :attr:`JobState.APPROVED` and seed all nine
    files — four Stage 1 plus product_mapping, benefits, faq,
    objections, and critic_report. Used by the route happy-path tests
    and the read-only fence (which snapshots upstream artefact bytes
    before/after hitting each GET to prove nothing is rewritten).
    """
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.APPROVED)
    write_dossier(job_id, sample_dossier)
    write_contacts(job_id, sample_contacts)
    write_needs_assessment(job_id, sample_needs)
    write_briefing(job_id, sample_briefing)
    write_product_mapping(job_id, sample_product_mapping)
    write_benefits(job_id, sample_benefits)
    write_faq(job_id, sample_faq)
    write_objections(job_id, sample_objections)
    write_critic_report(job_id, sample_critic_report)
    # Step 31: also seed the assembly output so the "all booleans True"
    # happy-path asserts the full eleven-key shape rather than nine. The
    # Markdown body's content is irrelevant to the route tests; the
    # presence of the file is what flips ``prospect_brief`` to ``True``.
    write_prospect_brief_markdown(job_id, "# fixture brief\n")
    # Step 34: seed a minimal manifest companion so the
    # ``document_manifest`` boolean also flips True in the all-artefacts
    # happy path. The shape mirrors the documented Step 29 manifest;
    # tests that exercise the manifest body shape use the real assembler
    # instead.
    write_document_manifest(job_id, {
        "schema_version": 1,
        "job_id": job_id,
        "company_name": _COMPANY_NAME,
        "company_url": _COMPANY_URL,
        "generated_at": "2026-05-28T00:00:00Z",
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": "0" * 64,
        "markdown_byte_length": 16,
        "sections": [],
        "artefacts": {},
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": None,
        "warnings": [],
    })
    return job_id


@pytest.fixture()
def expected_company_name() -> str:
    return _COMPANY_NAME


@pytest.fixture()
def expected_company_url() -> str:
    return _COMPANY_URL
