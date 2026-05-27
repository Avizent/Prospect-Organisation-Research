"""Shared fixtures for auth route tests.

Each test gets its own SQLite database under tmp_path, with migrations
applied. The FastAPI app's get_db dependency is overridden to bind to
that database, so tests are fully isolated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import get_db
from backend.main import app


def _alembic_cfg(db_path: Path) -> Config:
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    """A fresh migrated SQLite database for each test."""
    db_path = tmp_path / "auth_test.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    engine = sa.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    """A Session bound to the test engine."""
    factory = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_engine) -> Iterator[TestClient]:
    """TestClient with get_db overridden to use the test engine."""
    factory = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False
    )

    def _override_get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # Also override main's startup migration: it's already done by the
    # db_engine fixture, and we don't want it to clobber ~/.ans-tool.
    # The lifespan is bypassed by TestClient unless entered as a context;
    # we instantiate without `with` so startup doesn't fire.
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

VALID_PASSWORD = "CorrectHorse123!"
VALID_USERNAME = "ans-admin"
VALID_RECOVERY = "rcp@example.com"


@pytest.fixture()
def completed_setup(client: TestClient) -> TestClient:
    """A client whose database has been through /setup."""
    r = client.post(
        "/auth/setup",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "recovery_email": VALID_RECOVERY,
        },
    )
    assert r.status_code == 200, r.text
    return client
