"""Server-side session management.

Sessions live in the `sessions` SQLite table. The cookie value is a
32-byte cryptographically random token; only the session row carries
identity. There is no client-side state beyond the opaque token.

Cookie policy (set in routes.py):
    name=ans_session, HttpOnly, SameSite=Strict, Secure on non-loopback,
    Max-Age = SESSION_DURATION_SECONDS, Path=/

Sliding expiry: every authenticated request that resolves a session
extends `expires_at` and updates `last_activity`. If `expires_at` has
already passed at lookup time, the session is treated as anonymous and
deleted opportunistically.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from backend.db.models import Session as SessionRow
from backend.db.session import get_db


# 4 hours, per handover spec.
SESSION_DURATION_SECONDS = 4 * 60 * 60
SESSION_COOKIE_NAME = "ans_session"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """UTC now, naive — the schema uses naive DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_token() -> str:
    """Return a 32-byte urlsafe random session token (~43 chars)."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_session(
    db: DbSession,
    username: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SessionRow:
    """Create and persist a new session row. Returns it (committed)."""
    now = _now()
    row = SessionRow(
        id=_new_token(),
        username=username,
        created_at=now,
        expires_at=now + timedelta(seconds=SESSION_DURATION_SECONDS),
        last_activity=now,
        ip=ip,
        user_agent=user_agent,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db: DbSession, token: str | None) -> SessionRow | None:
    """Return a live session row for *token*, or None.

    Treats expired sessions as None and deletes the row.
    Slides expiry on every successful lookup.
    """
    if not token:
        return None
    row = db.get(SessionRow, token)
    if row is None:
        return None
    now = _now()
    if row.expires_at <= now:
        db.delete(row)
        db.commit()
        return None
    # Sliding window
    row.last_activity = now
    row.expires_at = now + timedelta(seconds=SESSION_DURATION_SECONDS)
    db.commit()
    db.refresh(row)
    return row


def delete_session(db: DbSession, token: str | None) -> None:
    """Delete a single session by token. Idempotent."""
    if not token:
        return
    row = db.get(SessionRow, token)
    if row is not None:
        db.delete(row)
        db.commit()


def delete_sessions_for_user(db: DbSession, username: str) -> int:
    """Delete every session belonging to *username*. Returns count.

    Used after a successful password reset so every existing cookie
    becomes invalid immediately.
    """
    rows = (
        db.query(SessionRow).filter(SessionRow.username == username).all()
    )
    count = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return count


def purge_expired(db: DbSession) -> int:
    """Best-effort cleanup of expired sessions. Returns rows deleted."""
    now = _now()
    rows = (
        db.query(SessionRow).filter(SessionRow.expires_at <= now).all()
    )
    count = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return count


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def current_session(
    request: Request,
    db: DbSession = Depends(get_db),
    ans_session: str | None = Cookie(default=None),
) -> SessionRow:
    """Resolve and return the live session, or raise 401.

    Use this on every authenticated route. The dependency slides expiry
    as a side effect of `get_session`.
    """
    _ = request  # reserved for future audit logging
    row = get_session(db, ans_session)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return row


def current_username(
    session: SessionRow = Depends(current_session),
) -> str:
    """Convenience dependency for routes that only need the username."""
    return session.username
