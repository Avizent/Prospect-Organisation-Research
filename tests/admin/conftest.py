"""Shared fixtures for admin route tests.

Each test gets:
  - a fresh migrated SQLite DB (under tmp_path)
  - get_db dependency overridden onto that DB
  - the active keyring swapped for an in-memory backend
  - an authenticated TestClient (setup completed + logged in)

No test touches the real macOS Keychain or ~/.ans-tool/data.db.
"""

from __future__ import annotations

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

from backend.db.session import get_db
from backend.main import app


# ---------------------------------------------------------------------------
# Convenience constants — mirror tests/auth/conftest.py so the admin
# suite's authenticated client uses identical setup parameters.
# ---------------------------------------------------------------------------

VALID_PASSWORD = "CorrectHorse123!"
VALID_USERNAME = "ans-admin"
VALID_RECOVERY = "rcp@example.com"


# ---------------------------------------------------------------------------
# In-memory keyring backend (same shape as tests/credentials/test_keychain.py)
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
    """Swap the active keyring for an in-memory one, per test."""
    original = keyring.get_keyring()
    backend = _InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


# ---------------------------------------------------------------------------
# DB + client fixtures
# ---------------------------------------------------------------------------

def _alembic_cfg(db_path: Path) -> Config:
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "admin_test.db"
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
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(db_engine) -> Iterator[TestClient]:
    factory = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False
    )

    def _override_get_db() -> Iterator[Session]:
        s = factory()
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
    """A client that has completed setup and is logged in.

    Returns the same TestClient with the session cookie attached;
    every subsequent request carries it automatically.
    """
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
