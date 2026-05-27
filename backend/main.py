"""FastAPI application entry point.

Currently mounts:
  - /auth/* — setup, login, logout, me, forgot, reset (step 3)

On startup, runs `alembic upgrade head` against ~/.ans-tool/data.db so a
fresh install reaches a usable state without manual CLI steps.

Later steps will add:
  - /admin/credentials (step 4)
  - /jobs/* (step 7+)
  - static UI mounts (step 16)
  - Ghostscript/Poppler probes and concurrent-job semaphore
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from fastapi import FastAPI

from backend.auth.routes import router as auth_router


def _run_migrations() -> None:
    """Apply Alembic migrations to ~/.ans-tool/data.db."""
    db_path = Path.home() / ".ans-tool" / "data.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini_path))
    alembic_command.upgrade(cfg, "head")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _run_migrations()
    yield


app = FastAPI(
    title="ANS Prospect Tool",
    version="0.1.0",
    lifespan=_lifespan,
)

app.include_router(auth_router, prefix="/auth")


@app.get("/health")
def health() -> dict:
    """Liveness probe. No DB hit on purpose."""
    return {"status": "ok"}
