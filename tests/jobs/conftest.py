"""Shared fixtures for ``tests/jobs/``.

Three concerns are isolated per test:

* **Jobs root** — ``ANS_JOBS_ROOT`` is set to a ``tmp_path`` subfolder,
  so no test ever writes under the user's real ``~/.ans-tool/jobs/``.
* **Database** — a fresh migrated SQLite database under ``tmp_path``,
  with a Session bound to it. Tests that touch the DB use the
  ``db_session`` fixture.
* **Sample dossier** — a :class:`ResearchDossier` built from a literal
  dict, used for storage round-trip tests. Step 7a forbids
  ResearchAgent execution, so this is the only dossier source.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
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
    EvidencePointer,
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


# ---------------------------------------------------------------------------
# Jobs root
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory.

    ``autouse=True`` so no test accidentally writes under the real
    user's ``~/.ans-tool/jobs/`` — the cost of a forgotten fixture
    here is a leaked test artefact in the user's home dir, which we
    refuse to accept.
    """
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _alembic_cfg(db_path: Path) -> Config:
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "jobs_test.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    factory = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Sample ResearchDossier
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_dossier() -> ResearchDossier:
    """A populated dossier built from literals.

    Includes one finding with one source so the round-trip test
    exercises nested Pydantic types (HttpUrl, date, Confidence
    enum). No ResearchAgent involved.
    """
    return ResearchDossier(
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",  # type: ignore[arg-type]
        retrieved_at=date(2026, 5, 27),
        senior_hires=[
            Finding(
                summary="New CIO appointed April 2026",
                detail="Press release dated 2026-04-01 confirms hire.",
                confidence=Confidence.HIGH,
                sources=[
                    Source(
                        url="https://acme.example.com/news/cio",  # type: ignore[arg-type]
                        title="Acme Ltd appoints new CIO",
                        retrieved_at=date(2026, 5, 27),
                        confidence=Confidence.HIGH,
                    )
                ],
            )
        ],
        gaps=["no public regulatory information found"],
    )


# ---------------------------------------------------------------------------
# Sample ContactExtractionResult
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_contacts() -> ContactExtractionResult:
    """A populated :class:`ContactExtractionResult` built from literals.

    One contact with a corporate email so the personal-email validator
    passes; one source URL; ``Confidence.HIGH``. No agent involved —
    Step 9a is storage-only.
    """
    return ContactExtractionResult(
        company_name="Acme Ltd",
        contacts=[
            ContactCandidate(
                name="Alice Example",
                job_title="Chief Technology Officer",
                country="United Kingdom",
                linkedin_url="https://www.linkedin.com/in/alice-example/",  # type: ignore[arg-type]
                email="alice@acme.example.com",
                seniority=Seniority.C_LEVEL,
                function=Function.CTO,
                source_url="https://acme.example.com/about",  # type: ignore[arg-type]
                source_title="Acme Ltd — leadership",
                retrieved_at=date(2026, 5, 27),
                confidence=Confidence.HIGH,
            )
        ],
        gaps=["no security-executive contact found"],
    )


# ---------------------------------------------------------------------------
# Sample NeedsAssessment
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_needs_assessment() -> NeedsAssessment:
    """A populated :class:`NeedsAssessment` built from literals.

    ``lab_maturity = MATURE`` plus one :class:`IdentifiedNeed` with
    one :class:`EvidencePointer` — enough to exercise the nested
    Pydantic types (HttpUrl, date, Confidence enum) on round-trip.
    """
    pointer = EvidencePointer(
        summary="Job posting for Head of SD-WAN dated 2026-04-01.",
        source_url="https://acme.example.com/jobs/sdwan",  # type: ignore[arg-type]
        source_title="Head of SD-WAN — Acme Ltd",
        retrieved_at=date(2026, 5, 27),
        confidence=Confidence.HIGH,
    )
    return NeedsAssessment(
        company_name="Acme Ltd",
        lab_maturity=LabMaturity.MATURE,
        lab_maturity_reasoning=(
            "Named CTO, stable vendor footprint, no transformation "
            "programme visible."
        ),
        lab_maturity_evidence=[pointer],
        lab_maturity_confidence=Confidence.MEDIUM,
        needs=[
            IdentifiedNeed(
                summary="Modernise SD-WAN footprint",
                detail=(
                    "Press releases and job postings indicate a stalled "
                    "SD-WAN rollout that ANS could accelerate."
                ),
                priority=1,
                evidence=[pointer],
                confidence=Confidence.HIGH,
            )
        ],
        gaps=[],
    )


# ---------------------------------------------------------------------------
# Sample Briefing
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_briefing() -> Briefing:
    """A populated :class:`Briefing` with one source entry and one
    claim/contact/need each citing index 0.

    Built from literals so storage round-trip tests can exercise the
    nested types (HttpUrl, date, enums) and the top-level
    ``source_indices`` range validator.
    """
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
