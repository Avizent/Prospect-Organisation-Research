"""FastAPI application entry point.

Currently mounts:
  - /auth/*     — setup, login, logout, me, forgot, reset    (step 3)
  - /admin/*    — Anthropic credential management            (step 4)
  - /api/jobs/* — job intake, artefact reads, approval gate  (step 11)
  - /            — minimal frontend inspector SPA            (step 12)

On startup, runs `alembic upgrade head` against ~/.ans-tool/data.db so a
fresh install reaches a usable state without manual CLI steps.

Static frontend mount notes
---------------------------
The frontend lives in ``./frontend`` next to ``backend/``. We mount
it last so that the API routers above always win path resolution
for ``/auth``, ``/admin``, ``/api`` and ``/health`` — the static
mount only sees URLs that did not match an API route. ``html=True``
makes the mount serve ``index.html`` for ``/`` and any unmatched
sub-path so the SPA hash router can take over inside the browser.

Later steps will add:
  - Stage 1 run / dry-run endpoints (deferred — Step 11 is fake-safe only)
  - HIG-polished UI                                          (step 15-17)
  - Ghostscript/Poppler probes and concurrent-job semaphore
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.admin.routes import router as admin_router
from backend.auth.routes import router as auth_router
from backend.jobs.routes import router as jobs_router
from backend.jobs.stage2_routes import router as stage2_router


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
app.include_router(admin_router, prefix="/admin")
app.include_router(jobs_router, prefix="/api/jobs")
app.include_router(stage2_router, prefix="/api/jobs")


@app.get("/health")
def health() -> dict:
    """Liveness probe. No DB hit on purpose."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Static frontend mount — must be LAST so the API routers above win.
# ---------------------------------------------------------------------------
_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=_FRONTEND_DIR, html=True),
        name="frontend",
    )
