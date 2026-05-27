"""Shared fixtures for ``tests/approval/``.

The approval-gate boundary depends on three live pieces of state:

* ``state.json`` for the job — its ``current_state`` field is the
  guard for every boundary call.
* ``briefing.json`` for the job — :func:`approve` and
  :func:`apply_briefing_edit` both reload it.
* The DB ``Job`` row — every transition bumps ``Job.status``.

This conftest provides per-state factory fixtures that build each
of those three projections to a known shape. Tests pick the
fixture whose starting state matches what they want to exercise.

We re-declare a small ``isolated_jobs_root`` autouse fixture (no
test in this package may write under the user's real
``~/.ans-tool/jobs/``) and a migrated SQLite database per test.
The :class:`Briefing` fixture comes straight from
``tests.jobs.conftest`` — same shape as everywhere else in the
suite.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
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
from backend.agents.needs_models import LabMaturity
from backend.agents.research_models import Confidence
from backend.jobs.state import JobState, apply_transition
from backend.jobs.storage import (
    append_transition,
    write_briefing,
)


# ---------------------------------------------------------------------------
# Jobs root
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory.

    Autouse: every boundary function writes to ``state.json`` and
    (for edits) ``briefing.json``. We refuse to risk leaking those
    writes into the user's real ``~/.ans-tool/jobs/``.
    """
    root = tmp_path / "jobs"
    monkeypatch.setenv("ANS_JOBS_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# Database — migrated SQLite per test
# ---------------------------------------------------------------------------

def _alembic_cfg(db_path: Path) -> Config:
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "approval_test.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Sample Briefing
# ---------------------------------------------------------------------------
#
# A literal-built briefing with one source entry and one claim /
# contact / need each citing index 0. The shape mirrors
# tests/jobs/conftest.py so any cross-test regression in the
# fixture surfaces in both packages at once.

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


# ---------------------------------------------------------------------------
# Seeded jobs — CREATED, then advanced to whichever state the test wants
# ---------------------------------------------------------------------------
#
# All approval-boundary tests need a job whose state.json + DB row +
# briefing.json are aligned to a specific start state. ``intake.create_job``
# is the only sanctioned way to mint a row + folder + state.json, so we
# go through it; the rest is appending transitions manually (the
# transitions we're appending are *legal in production*, so we can
# use apply_transition directly).

_FIXED_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _create_job(db: Session) -> str:
    from backend.jobs.intake import create_job

    result = create_job(
        db=db,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        now=_FIXED_NOW,
    )
    return result.job_id


def _advance(db: Session, job_id: str, *, to: JobState) -> None:
    """Walk the job from its current state to ``to`` via legal edges.

    Each step calls :func:`apply_transition` (for legality) and
    :func:`append_transition` (for persistence) plus a DB status
    bump — exactly the same sequence the production code uses.
    Anything that breaks here breaks production too.
    """
    from backend.db.models import Job

    # Map of (current → target) → ordered legal path
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
    """Job at :attr:`JobState.CREATED`. No briefing on disk."""
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
    """Job at :attr:`JobState.BRIEFING_READY` with ``briefing.json`` on disk.

    This is the most useful starting state for approval tests — it
    permits ``open_for_editing`` and ``approve`` (the approve-as-is
    path).
    """
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.BRIEFING_READY)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def editing_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    """Job at :attr:`JobState.USER_EDITING` with ``briefing.json`` on disk.

    The starting state for :func:`apply_briefing_edit` and the
    approve-after-edit path.
    """
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.USER_EDITING)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def regenerating_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    """Job at :attr:`JobState.REGENERATING_SECTION`.

    The starting state for :func:`complete_regeneration` and
    :func:`fail_regeneration`.
    """
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.REGENERATING_SECTION)
    write_briefing(job_id, sample_briefing)
    return job_id


@pytest.fixture()
def approved_job(
    db_session: Session, sample_briefing: Briefing
) -> str:
    """Job already at :attr:`JobState.APPROVED`.

    Used by guard tests that check approval boundaries refuse to
    fire on terminal-side states.
    """
    job_id = _create_job(db_session)
    _advance(db_session, job_id, to=JobState.APPROVED)
    write_briefing(job_id, sample_briefing)
    return job_id


# ---------------------------------------------------------------------------
# Convenience constants
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixed_now() -> datetime:
    """A deterministic UTC datetime for transition assertions."""
    return _FIXED_NOW
