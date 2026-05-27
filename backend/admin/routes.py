"""Admin routes for credential management.

Scope (step 4)
--------------
GET    /admin/credentials       — report whether an Anthropic key is configured
POST   /admin/credentials       — store an Anthropic API key in macOS Keychain
DELETE /admin/credentials       — remove the stored key
POST   /admin/credentials/test  — 501 placeholder; real validation lands in
                                  a later step (after the runaway-trap cost
                                  controls). The route shell exists now so
                                  the step-16 frontend has a stable contract.

Security posture
----------------
- All routes require an authenticated session (Depends(current_username)).
  Anonymous callers receive HTTP 401 via the session dependency.
- The single user of this app is the admin — there is no separate role.
- The raw API key is never returned, logged, included in error bodies, or
  echoed back. The GET response surfaces only existence and timestamp.
- The only piece of SQLite state written by this module is the timestamp
  `credentials.anthropic.configured_at` in the `settings` table. The key
  itself lives exclusively in macOS Keychain via backend.credentials.keychain.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from backend.auth.sessions import current_username
from backend.credentials import keychain
from backend.db.models import Setting
from backend.db.session import get_db


log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Settings keys written by step 4.
# Permitted-keys list in backend/db/models.py covers the auth.* family;
# the credentials.* family is meta-state about credentials and follows
# the same "no secret material" rule.
# ---------------------------------------------------------------------------

KEY_ANTHROPIC_CONFIGURED_AT = "credentials.anthropic.configured_at"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC now as an ISO-8601 string (no microseconds)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0, tzinfo=None)
        .isoformat()
    )


def _put_setting(db: DbSession, key: str, value: str) -> None:
    """Upsert a settings row. Caller commits."""
    row = db.get(Setting, key)
    now = (
        datetime.now(timezone.utc).replace(tzinfo=None)
    )
    if row is None:
        db.add(Setting(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now


def _delete_setting(db: DbSession, key: str) -> None:
    """Remove a settings row if present. Caller commits."""
    row = db.get(Setting, key)
    if row is not None:
        db.delete(row)


def _get_setting(db: DbSession, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CredentialsStatus(BaseModel):
    """GET /admin/credentials response.

    Deliberately minimal — no fingerprint, no length, no prefix, no
    suffix, no last-4. Just whether a key is configured and when it
    was set.
    """

    configured: bool
    configured_at: str | None = None


class SetCredentialsBody(BaseModel):
    """POST /admin/credentials body."""

    api_key: str


class GenericMessage(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/credentials", response_model=CredentialsStatus)
def get_credentials(
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> CredentialsStatus:
    """Report whether an Anthropic API key is configured.

    The response never includes the key, any portion of the key, or
    any derived value (hash, length, prefix). It includes only the
    presence boolean and the timestamp the key was last set.
    """
    configured = keychain.has_anthropic_key()
    configured_at = _get_setting(db, KEY_ANTHROPIC_CONFIGURED_AT)
    return CredentialsStatus(
        configured=configured,
        configured_at=configured_at if configured else None,
    )


@router.post("/credentials", response_model=GenericMessage)
def set_credentials(
    body: SetCredentialsBody,
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Persist the Anthropic API key to macOS Keychain.

    Validation is intentionally minimal at this layer:
      - non-empty after stripping leading/trailing whitespace
    Anthropic's exact key format may evolve; strict checks here would
    risk rejecting keys the SDK would accept. Real verification lands
    in a later step behind the trap-enforced cloud client.
    """
    api_key = (body.api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="api_key must be non-empty",
        )

    keychain.set_anthropic_key(api_key)
    _put_setting(db, KEY_ANTHROPIC_CONFIGURED_AT, _now_iso())
    db.commit()

    # Deliberately generic log line — no key, no length, no prefix.
    log.info("anthropic credential stored")
    return GenericMessage(message="Anthropic credential stored")


@router.delete("/credentials", response_model=GenericMessage)
def delete_credentials(
    _username: str = Depends(current_username),
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Remove the stored Anthropic API key.

    Always idempotent — calling DELETE when no key is stored still
    returns 200. The configured_at marker is removed in either case
    so GET reports a clean state.
    """
    keychain.delete_anthropic_key()
    _delete_setting(db, KEY_ANTHROPIC_CONFIGURED_AT)
    db.commit()
    log.info("anthropic credential removed")
    return GenericMessage(message="Anthropic credential removed")


@router.post("/credentials/test")
def test_credentials(
    _username: str = Depends(current_username),
) -> None:
    """Route shell — real validation arrives in a later step.

    The endpoint exists so the step-16 frontend can wire its "Test"
    button to a stable URL today without a contract change later.

    Per CLAUDE.md hard rule #4, no real Claude API call may run until
    the seven runaway traps are in place and tested. Until that work
    lands, this endpoint returns HTTP 501 unconditionally.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="credential testing arrives after cost controls are implemented",
    )
