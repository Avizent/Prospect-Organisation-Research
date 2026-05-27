"""Auth routes: /setup, /login, /logout, /forgot, /reset, /me.

Security posture:
  - argon2id password hashing via backend.auth.password
  - Server-side sessions (HttpOnly, SameSite=Strict, Secure off loopback)
  - 5-failure/IP/15-min rate limit on /login
  - 3-request/username/hour rate limit on /forgot
  - Single-use reset tokens, 30-min expiry, SHA-256 hashed at rest
  - Successful reset deletes every existing session for the user
  - /setup returns 404 once setup_completed_at is set
  - M365 send is stubbed here; replaced in step 14 (recipient-locked)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from backend.auth.password import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from backend.auth.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_SECONDS,
    create_session,
    current_username,
    delete_session,
    delete_sessions_for_user,
)
from backend.db.models import LoginAttempt, PasswordReset, Setting
from backend.db.session import get_db


log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Settings keys used by auth.
KEY_USERNAME = "auth.username"
KEY_PASSWORD_HASH = "auth.password_hash"
KEY_RECOVERY_EMAIL = "auth.recovery_email"
KEY_SETUP_COMPLETED_AT = "auth.setup_completed_at"

# Rate limits.
LOGIN_FAILURE_WINDOW_MINUTES = 15
LOGIN_FAILURE_THRESHOLD = 5
FORGOT_WINDOW_MINUTES = 60
FORGOT_THRESHOLD = 3

# Reset token policy.
RESET_TOKEN_TTL_MINUTES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """UTC now, naive — matches the DateTime columns in the schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _client_ip(request: Request) -> str:
    """Return the request client IP, or '0.0.0.0' if unknown.

    Single-user local app — no proxy header parsing needed. If the user
    later puts this behind a reverse proxy, X-Forwarded-For handling
    belongs in middleware, not here.
    """
    if request.client is None:
        return "0.0.0.0"
    return request.client.host or "0.0.0.0"


def _cookie_secure(request: Request) -> bool:
    """Return True iff the cookie should carry the Secure flag.

    The Secure flag only makes sense over HTTPS — if we set it on an
    http://127.0.0.1 request the browser drops the cookie entirely.
    Match the request scheme: https → Secure, otherwise omit.
    """
    return request.url.scheme == "https"


def _get_setting(db: DbSession, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row else None


def _put_setting(db: DbSession, key: str, value: str) -> None:
    """Upsert a settings row. Caller commits."""
    row = db.get(Setting, key)
    now = _now()
    if row is None:
        db.add(Setting(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now


def _setup_completed(db: DbSession) -> bool:
    return _get_setting(db, KEY_SETUP_COMPLETED_AT) is not None


def _hash_reset_token(token: str) -> str:
    """SHA-256 hex of a reset token. Used both at issue and verify time."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_session_cookie(
    response: Response, token: str, *, secure: bool
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
    )


def _record_login_attempt(
    db: DbSession, *, username: str | None, ip: str, success: bool
) -> None:
    db.add(
        LoginAttempt(
            username=username,
            ip=ip,
            success=success,
            attempted_at=_now(),
        )
    )
    db.commit()


def _login_is_rate_limited(db: DbSession, ip: str) -> bool:
    """True if *ip* has hit LOGIN_FAILURE_THRESHOLD failures in the window."""
    since = _now() - timedelta(minutes=LOGIN_FAILURE_WINDOW_MINUTES)
    failures = (
        db.query(LoginAttempt)
        .filter(
            LoginAttempt.ip == ip,
            LoginAttempt.success.is_(False),
            LoginAttempt.attempted_at >= since,
        )
        .count()
    )
    return failures >= LOGIN_FAILURE_THRESHOLD


def _forgot_is_rate_limited(db: DbSession, username: str) -> bool:
    """True if *username* has requested FORGOT_THRESHOLD resets in the window."""
    since = _now() - timedelta(minutes=FORGOT_WINDOW_MINUTES)
    count = (
        db.query(PasswordReset)
        .filter(
            PasswordReset.username == username,
            PasswordReset.created_at >= since,
        )
        .count()
    )
    return count >= FORGOT_THRESHOLD


# ---------------------------------------------------------------------------
# Reset-token test capture hook
#
# Raw reset tokens MUST NOT appear in logs, stderr, exceptions, or any
# other observability channel. The only legitimate egress is the
# eventual M365 Graph send (step 14).
#
# To let tests exercise the full /forgot → /reset flow with a real
# generated token, we expose a single nullable module-level hook. It is
# None in production and never set by application code. Tests may
# monkey-patch it for the duration of a single test.
#
# Signature: (recovery_email: str, raw_token: str) -> None
# ---------------------------------------------------------------------------
_reset_token_capture: Callable[[str, str], None] | None = None


def _send_reset_email(recovery_email: str) -> None:
    """STUB: M365 Graph send. Replaced in step 14 (recipient-locked).

    Logs only that an email *would* be sent. The raw token and reset URL
    are deliberately omitted — leaking them via log files or stderr
    would re-introduce the threat the reset flow is designed to mitigate.

    Hard rules in play (see CLAUDE.md):
      - No SMTP.
      - When implemented in step 14, recipient is the user's UPN from
        /me; there is no recipient parameter exposed to callers.
    """
    log.info(
        "password reset email would be sent to %s (stub; M365 wires up "
        "in step 14)",
        recovery_email,
    )


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class SetupBody(BaseModel):
    username: str
    password: str
    recovery_email: str


class GenericMessage(BaseModel):
    message: str


class MeResponse(BaseModel):
    username: str


# ---------------------------------------------------------------------------
# /setup — first-run wizard
# ---------------------------------------------------------------------------

@router.get("/setup/status", response_model=dict)
def setup_status(db: DbSession = Depends(get_db)) -> dict:
    """Tell the UI whether the first-run wizard still needs to run."""
    return {"setup_completed": _setup_completed(db)}


@router.post("/setup", response_model=GenericMessage)
def setup(
    body: SetupBody,
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Create the single admin user. Refuses after first success."""
    if _setup_completed(db):
        # Per spec: setup endpoint must 404 after completion.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Setup already completed",
        )

    username = body.username.strip()
    recovery_email = body.recovery_email.strip()

    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    if "@" not in recovery_email or "." not in recovery_email:
        raise HTTPException(
            status_code=400, detail="A valid recovery email is required"
        )

    strength_errors = validate_password_strength(body.password)
    if strength_errors:
        raise HTTPException(
            status_code=400,
            detail={"password_errors": strength_errors},
        )

    _put_setting(db, KEY_USERNAME, username)
    _put_setting(db, KEY_PASSWORD_HASH, hash_password(body.password))
    _put_setting(db, KEY_RECOVERY_EMAIL, recovery_email)
    _put_setting(db, KEY_SETUP_COMPLETED_AT, _now().isoformat())
    db.commit()

    return GenericMessage(message="Setup complete")


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=GenericMessage)
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Verify credentials and issue a session cookie."""
    if not _setup_completed(db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup has not been completed",
        )

    ip = _client_ip(request)

    if _login_is_rate_limited(db, ip):
        # Don't even attempt verification — preserve cost ceiling.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed attempts. Try again in "
                f"{LOGIN_FAILURE_WINDOW_MINUTES} minutes."
            ),
        )

    stored_username = _get_setting(db, KEY_USERNAME) or ""
    stored_hash = _get_setting(db, KEY_PASSWORD_HASH) or ""

    # Always run verify_password so the timing is constant regardless of
    # whether the username matched. We still gate the real comparison on
    # the username so a wrong user can't authenticate by matching the
    # hash by luck.
    submitted = username.strip()
    ok_user = secrets.compare_digest(submitted, stored_username)
    ok_pass = verify_password(password, stored_hash)
    ok = ok_user and ok_pass

    _record_login_attempt(
        db, username=submitted or None, ip=ip, success=ok
    )

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Re-hash on the fly if argon2 defaults have moved.
    if needs_rehash(stored_hash):
        _put_setting(db, KEY_PASSWORD_HASH, hash_password(password))
        db.commit()

    session_row = create_session(
        db,
        stored_username,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )

    _set_session_cookie(
        response,
        session_row.id,
        secure=_cookie_secure(request),
    )
    return GenericMessage(message="Logged in")


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------

@router.post("/logout", response_model=GenericMessage)
def logout(
    request: Request,
    response: Response,
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Delete the caller's session and clear the cookie. Idempotent."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    delete_session(db, token)
    _clear_session_cookie(response)
    return GenericMessage(message="Logged out")


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
def me(username: str = Depends(current_username)) -> MeResponse:
    """Return the logged-in username. Useful for UI bootstrap."""
    return MeResponse(username=username)


# ---------------------------------------------------------------------------
# /forgot
# ---------------------------------------------------------------------------

class ForgotBody(BaseModel):
    username: str


@router.post("/forgot", response_model=GenericMessage)
def forgot(
    request: Request,
    body: ForgotBody,
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Issue a single-use reset token and email it to the recovery address.

    Response is identical whether or not the username exists, to avoid
    confirming account existence. Rate limited by username.
    """
    submitted = body.username.strip()
    ip = _client_ip(request)
    stored_username = _get_setting(db, KEY_USERNAME) or ""
    recovery_email = _get_setting(db, KEY_RECOVERY_EMAIL) or ""

    generic = GenericMessage(
        message="If the account exists, a reset link has been sent."
    )

    # Only proceed if username matches and we have a recovery email.
    if not submitted or not stored_username or not recovery_email:
        return generic
    if not secrets.compare_digest(submitted, stored_username):
        return generic

    if _forgot_is_rate_limited(db, stored_username):
        # Silently suppress to keep the response shape constant.
        return generic

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(raw_token)
    now = _now()

    db.add(
        PasswordReset(
            username=stored_username,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
            created_at=now,
            created_ip=ip,
        )
    )
    db.commit()

    # Test-only capture hook. None in production; never set by app code.
    # See _reset_token_capture for the threat-model rationale.
    if _reset_token_capture is not None:
        _reset_token_capture(recovery_email, raw_token)

    _send_reset_email(recovery_email)

    return generic


# ---------------------------------------------------------------------------
# /reset
# ---------------------------------------------------------------------------

class ResetBody(BaseModel):
    token: str
    new_password: str


@router.post("/reset", response_model=GenericMessage)
def reset(
    body: ResetBody,
    db: DbSession = Depends(get_db),
) -> GenericMessage:
    """Consume a reset token and replace the user's password.

    Token rules: must exist, not be used, not be expired, single-use.
    On success, every existing session for the user is invalidated.
    """
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token required")

    strength_errors = validate_password_strength(body.new_password)
    if strength_errors:
        raise HTTPException(
            status_code=400,
            detail={"password_errors": strength_errors},
        )

    token_hash = _hash_reset_token(token)
    row = (
        db.query(PasswordReset)
        .filter(PasswordReset.token_hash == token_hash)
        .one_or_none()
    )
    now = _now()

    if row is None or row.used_at is not None or row.expires_at <= now:
        raise HTTPException(
            status_code=400, detail="Invalid or expired reset token"
        )

    # Mark the token used FIRST, then update the password.
    row.used_at = now
    _put_setting(
        db, KEY_PASSWORD_HASH, hash_password(body.new_password)
    )
    db.commit()

    # Invalidate every existing session for the user.
    delete_sessions_for_user(db, row.username)

    return GenericMessage(message="Password reset successful")
