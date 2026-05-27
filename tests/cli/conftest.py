"""Shared fixtures for ``tests/cli/``.

Each test gets:
  * an isolated SQLite database under ``tmp_path`` with the
    production schema migrated to head
  * ``ANS_JOBS_ROOT`` redirected into ``tmp_path`` so on-disk job
    folders never touch ``~/.ans-tool/jobs/``
  * a CliRunner from Click for invoking the CLI in-process
  * a job pre-seeded at ``created`` via the real intake module,
    so the CLI exercises the same row + state.json shape the
    orchestrator expects
  * the sample-artefact Pydantic instances re-used from
    ``tests/orchestrator/conftest.py`` so the dry-run script's
    JSON text is a faithful representation of what a real model
    would produce

The conftest never touches the real Anthropic SDK, the real
Keychain, or the real home directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from click.testing import CliRunner
from sqlalchemy.orm import Session, sessionmaker

from backend.cost_control import concurrency as _concurrency

# Re-use the sample-artefact fixtures from the orchestrator suite — the
# dry-run script needs Pydantic-valid JSON for each stage-1 artefact,
# and the orchestrator conftest is where those fixtures already live.
from tests.orchestrator.conftest import (  # noqa: F401 — re-exported fixtures
    sample_briefing,
    sample_contacts,
    sample_dossier,
    sample_needs_assessment,
)


# ---------------------------------------------------------------------------
# Jobs root — autouse so a forgotten fixture cannot leak into ~/.ans-tool
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_jobs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
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
def db_path(tmp_path: Path) -> Path:
    """Per-test SQLite file with the production schema applied.

    The CLI accepts ``--db-path``, so we don't need to monkeypatch
    ``$HOME`` — passing the explicit override keeps the test
    independent of process-wide globals.
    """
    path = tmp_path / "cli_test.db"
    alembic_command.upgrade(_alembic_cfg(path), "head")
    return path


@pytest.fixture()
def db_session(db_path: Path) -> Iterator[Session]:
    """A session against ``db_path`` for fixture-side setup.

    Disposed automatically. Tests that need to read DB state after a
    CLI run should construct their own short-lived session — the
    CLI uses its own engine in-process so connection state is
    not shared.
    """
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Pre-seeded job at CREATED
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_job(
    db_session: Session,
    isolated_jobs_root: Path,
) -> str:
    """Create one job at CREATED via the real intake module.

    Returns the job_id. Mirrors the orchestrator suite's fixture so the
    CLI exercises the same row + state.json shape end-to-end.
    """
    from backend.jobs.intake import create_job

    result = create_job(
        db=db_session,
        company_name="Acme Ltd",
        company_url="https://acme.example.com/",
        now=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    return result.job_id


# ---------------------------------------------------------------------------
# Click runner
# ---------------------------------------------------------------------------

@pytest.fixture()
def runner() -> CliRunner:
    """Click's in-process CLI runner.

    Click >=8.2 reports stderr separately on ``result.stderr`` by
    default; older releases needed ``mix_stderr=False``. We use the
    new behaviour — failure-path tests rely on inspecting stderr
    distinct from ``result.output``.
    """
    return CliRunner()


# ---------------------------------------------------------------------------
# Concurrency semaphore reset — the orchestrator takes the slot inside
# ``run_stage1``; sharing a process-global semaphore across tests would
# cause spurious flakes if one test left the cap exhausted.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_job_semaphore() -> Iterator[None]:
    _concurrency._reset_for_tests()
    yield
    _concurrency._reset_for_tests()
