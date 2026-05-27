"""Shared fixtures for ``tests/orchestrator/``.

Everything an orchestrator test needs that isn't already in
``tests/jobs/conftest.py`` lives here. We deliberately re-export
:class:`FakeCloudClient` and :func:`make_response` from
``tests.agents.conftest`` rather than re-implementing them — the
fake is the same surface in both packages, and divergence would
mean the orchestrator tests started disagreeing with the agent
tests about what a "well-formed cloud response" looks like.

Three concerns are isolated per test:

* **Jobs root / DB** — re-used from ``tests/jobs/conftest.py`` via
  pytest's discovery (placed at a sibling package, those fixtures
  do not auto-import; we re-declare the ones we need here).
* **Job semaphore** — reset to a known cap before every test so
  one test's pending acquires never leak into the next.
* **Fake CloudClient** — borrowed from the agent tests.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.orm import Session, sessionmaker

from backend.agents.research_models import (
    Confidence,
    Finding,
    ResearchDossier,
    Source,
)
from backend.cost_control import concurrency as _concurrency
from backend.cost_control.config_loader import Models
from tests.agents.conftest import FakeCloudClient, make_response  # re-used


# ---------------------------------------------------------------------------
# Re-exports — keep the names available to test modules under
# ``tests.orchestrator``. ``FakeCloudClient`` and ``make_response`` are the
# same objects as in ``tests.agents.conftest`` so the contract is single-
# sourced.
# ---------------------------------------------------------------------------

__all__ = [
    "FakeCloudClient",
    "make_response",
]


# ---------------------------------------------------------------------------
# Jobs root
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``ANS_JOBS_ROOT`` at a per-test tmp directory.

    Autouse: the orchestrator writes ``state.json`` and
    ``research_dossier.json`` to disk on every test; we refuse to
    have a forgotten fixture leak into the user's real
    ``~/.ans-tool/jobs/``.
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
    db_path = tmp_path / "orchestrator_test.db"
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


@pytest.fixture()
def session_factory(db_engine) -> Callable[[], Session]:
    """A zero-arg callable returning fresh sessions.

    The orchestrator opens a session per state-update transaction so
    its long-running agent call doesn't hold a connection. Tests use
    the same factory.
    """
    Factory = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    return Factory


# ---------------------------------------------------------------------------
# Fake CloudClient + Models
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_cloud_client() -> FakeCloudClient:
    return FakeCloudClient()


@pytest.fixture()
def test_models() -> Models:
    """Distinct values per role so tests can assert on identity."""
    return Models(
        writer_model="test-writer-model",
        research_model="test-research-model",
        critic_model="test-critic-model",
    )


# ---------------------------------------------------------------------------
# Job semaphore — explicit reset per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_job_semaphore() -> Iterator[None]:
    """Reset the module-global semaphore before and after every test.

    The semaphore is process-global; if a test acquires it and a
    later test doesn't release it (because it never ran the body),
    we'd carry stale state across tests. Resetting both sides keeps
    the contract local.

    Most tests want the production cap; the concurrency tests
    override it by calling ``_reset_for_tests(max_jobs=1)`` inside
    the test body.
    """
    _concurrency._reset_for_tests()
    yield
    _concurrency._reset_for_tests()


# ---------------------------------------------------------------------------
# Sample ResearchDossier — same shape as tests/jobs/conftest.py
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_dossier() -> ResearchDossier:
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
# Seeded job (CREATED state) ready to be picked up
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_job(
    db_session: Session,
    isolated_jobs_root: Path,
) -> str:
    """Create one job at CREATED via the real intake module.

    Returns the job_id. Uses the real intake so the test exercises
    the same row shape and on-disk state.json the orchestrator
    expects.
    """
    from backend.jobs.intake import create_job

    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        now=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    return result.job_id
