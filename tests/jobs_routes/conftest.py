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
from backend.agents.needs_models import (
    IdentifiedNeed,
    LabMaturity,
    NeedsAssessment,
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
    write_briefing,
    write_contacts,
    write_dossier,
    write_needs_assessment,
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
def expected_company_name() -> str:
    return _COMPANY_NAME


@pytest.fixture()
def expected_company_url() -> str:
    return _COMPANY_URL
