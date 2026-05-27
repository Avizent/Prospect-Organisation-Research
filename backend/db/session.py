"""Database engine and session factory.

Public API:
    get_engine() -> Engine   — returns a configured SQLite engine.
    get_session() -> Session — returns a bound session; caller must close it.

Database path: ~/.ans-tool/data.db
The ~/.ans-tool/ directory is created on first call if absent.

FastAPI dependency injection (get_db) will be wired in a later step
once route handlers are in place.
"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import Session, sessionmaker


def _db_path() -> Path:
    path = Path.home() / ".ans-tool" / "data.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_engine() -> Engine:
    """Return a SQLite engine for ~/.ans-tool/data.db.

    SQLite-specific settings applied:
      - check_same_thread=False  — required for FastAPI's threadpool usage.
      - detect_types=PARSE_DECLTYPES  — required for Python 3.12+ datetime
        handling; avoids the deprecated default datetime adapter warning.
      - The PRAGMA foreign_keys=ON listener is registered via models.py.
    """
    url = f"sqlite:///{_db_path()}"
    return create_engine(
        url,
        connect_args={
            "check_same_thread": False,
            "detect_types": sqlite3.PARSE_DECLTYPES,
        },
    )


def get_session() -> Session:
    """Return a new Session bound to the production engine.

    Caller is responsible for closing:
        session = get_session()
        try:
            ...
        finally:
            session.close()

    Or use as a context manager:
        with get_session() as session:
            ...
    """
    engine = get_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return factory()
