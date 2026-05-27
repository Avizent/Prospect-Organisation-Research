"""Shared fixtures for ``tests/frontend/``.

Only the static-mount tests construct a TestClient. The other tests
in this package are pure file-content scans (HTML, JS, CSS) and
deliberately have no runtime dependencies — they exist to lock the
frontend's static surface so a future refactor cannot quietly
re-introduce the very integrations Step 12 forbids.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.main import app


_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def frontend_dir() -> Path:
    return _REPO_ROOT / "frontend"


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A bare TestClient — no auth, no DB overrides.

    The static-mount tests don't hit any DB-backed route. We
    deliberately don't enter the lifespan context (``with
    TestClient(app) as ...``), so the startup alembic-migration
    side-effect never fires against the user's real
    ``~/.ans-tool/data.db``.
    """
    yield TestClient(app)
