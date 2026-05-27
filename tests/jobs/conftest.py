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
