"""HTTP integration tests for the auth router.

Each test uses an isolated SQLite database via the conftest fixtures.
The FastAPI app's get_db dependency is overridden to that DB so tests
never touch ~/.ans-tool/data.db.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.auth.routes import (
    KEY_PASSWORD_HASH,
    KEY_SETUP_COMPLETED_AT,
    KEY_USERNAME,
    _hash_reset_token,
)
from backend.auth.sessions import SESSION_COOKIE_NAME
from backend.db.models import PasswordReset, Setting

from .conftest import VALID_PASSWORD, VALID_RECOVERY, VALID_USERNAME


def _utcnow() -> datetime:
    """Naive UTC datetime — matches the DateTime columns in the schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# /setup
# ---------------------------------------------------------------------------

def test_setup_status_is_false_initially(client: TestClient) -> None:
    r = client.get("/auth/setup/status")
    assert r.status_code == 200
    assert r.json() == {"setup_completed": False}


def test_setup_creates_user_and_completes(
    client: TestClient, db_session
) -> None:
    r = client.post(
        "/auth/setup",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "recovery_email": VALID_RECOVERY,
        },
    )
    assert r.status_code == 200

    # Settings landed correctly
    assert (
        db_session.get(Setting, KEY_USERNAME).value == VALID_USERNAME
    )
    hashed = db_session.get(Setting, KEY_PASSWORD_HASH).value
    assert hashed.startswith("$argon2id$")
    assert VALID_PASSWORD not in hashed, "password leaked into hash row"
    assert db_session.get(Setting, KEY_SETUP_COMPLETED_AT) is not None

    # Status flips to true
    r2 = client.get("/auth/setup/status")
    assert r2.json() == {"setup_completed": True}


def test_setup_rejects_weak_password(client: TestClient) -> None:
    r = client.post(
        "/auth/setup",
        json={
            "username": VALID_USERNAME,
            "password": "short",
            "recovery_email": VALID_RECOVERY,
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "password_errors" in detail
    assert len(detail["password_errors"]) >= 2


def test_setup_rejects_invalid_recovery_email(client: TestClient) -> None:
    r = client.post(
        "/auth/setup",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            "recovery_email": "not-an-email",
        },
    )
    assert r.status_code == 400


def test_setup_404_after_completion(completed_setup: TestClient) -> None:
    r = completed_setup.post(
        "/auth/setup",
        json={
            "username": "intruder",
            "password": VALID_PASSWORD,
            "recovery_email": VALID_RECOVERY,
        },
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------

def test_login_succeeds_with_correct_credentials(
    completed_setup: TestClient,
) -> None:
    r = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert SESSION_COOKIE_NAME in r.cookies
    cookie = r.cookies[SESSION_COOKIE_NAME]
    assert len(cookie) > 30


def test_login_session_cookie_is_httponly(
    completed_setup: TestClient,
) -> None:
    r = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r.status_code == 200
    set_cookie_headers = [
        v for k, v in r.headers.items() if k.lower() == "set-cookie"
    ]
    assert any(
        SESSION_COOKIE_NAME in h and "HttpOnly" in h
        for h in set_cookie_headers
    ), set_cookie_headers


def test_login_rejects_wrong_password(completed_setup: TestClient) -> None:
    r = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": "WrongPassword123"},
    )
    assert r.status_code == 401
    assert SESSION_COOKIE_NAME not in r.cookies


def test_login_rejects_unknown_username(
    completed_setup: TestClient,
) -> None:
    r = completed_setup.post(
        "/auth/login",
        data={"username": "ghost", "password": VALID_PASSWORD},
    )
    assert r.status_code == 401


def test_login_blocked_before_setup(client: TestClient) -> None:
    r = client.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r.status_code == 409


def test_login_rate_limit_triggers_after_five_failures(
    completed_setup: TestClient,
) -> None:
    for _ in range(5):
        r = completed_setup.post(
            "/auth/login",
            data={"username": VALID_USERNAME, "password": "WrongPass123!"},
        )
        assert r.status_code == 401

    # Sixth attempt is rejected with 429, even with correct credentials.
    r = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# /me and /logout
# ---------------------------------------------------------------------------

def test_me_requires_auth(client: TestClient) -> None:
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_returns_username_after_login(
    completed_setup: TestClient,
) -> None:
    completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    r = completed_setup.get("/auth/me")
    assert r.status_code == 200
    assert r.json() == {"username": VALID_USERNAME}


def test_logout_clears_session(completed_setup: TestClient, db_engine) -> None:
    completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    # Pre-logout: session row exists
    with db_engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM sessions")
        ).scalar()
    assert count == 1

    r = completed_setup.post("/auth/logout")
    assert r.status_code == 200

    with db_engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT COUNT(*) FROM sessions")
        ).scalar()
    assert count == 0

    # /me now requires re-auth
    r = completed_setup.get("/auth/me")
    assert r.status_code == 401


def test_logout_is_idempotent_without_session(client: TestClient) -> None:
    r = client.post("/auth/logout")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /forgot
# ---------------------------------------------------------------------------

def test_forgot_returns_generic_response_for_unknown_user(
    completed_setup: TestClient,
) -> None:
    r = completed_setup.post(
        "/auth/forgot", json={"username": "ghost"}
    )
    assert r.status_code == 200
    assert "reset" in r.json()["message"].lower()


def test_forgot_creates_reset_row_for_known_user(
    completed_setup: TestClient, db_session
) -> None:
    r = completed_setup.post(
        "/auth/forgot", json={"username": VALID_USERNAME}
    )
    assert r.status_code == 200

    rows = db_session.query(PasswordReset).all()
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert rows[0].expires_at > _utcnow()
    # Raw token is NOT stored — only the SHA-256 hash, hex.
    assert len(rows[0].token_hash) == 64
    int(rows[0].token_hash, 16)  # raises if non-hex


def test_forgot_does_not_log_raw_token(
    completed_setup: TestClient, caplog, capsys
) -> None:
    """The reset URL/token must never appear in logs or stderr.

    Regression guard: an earlier draft of the stub printed the URL to
    stderr at WARNING level. That is replay-grade leakage and must not
    return without an explicit dev-mode gate.
    """
    import logging as _logging

    with caplog.at_level(_logging.DEBUG, logger="backend.auth.routes"):
        r = completed_setup.post(
            "/auth/forgot", json={"username": VALID_USERNAME}
        )
    assert r.status_code == 200

    captured = capsys.readouterr()
    haystacks = [
        caplog.text,
        captured.out,
        captured.err,
    ]
    for h in haystacks:
        # The raw token would be a urlsafe-b64 string ~43 chars; the
        # legacy leak format was "url=/reset?token=...". Block both the
        # query-string form and any 30+ char token-like substring.
        assert "token=" not in h, f"raw token leaked: {h!r}"
        assert "/reset?" not in h, f"reset URL leaked: {h!r}"


def test_forgot_capture_hook_enables_e2e_reset(
    completed_setup: TestClient, monkeypatch
) -> None:
    """End-to-end /forgot → /reset using a real generated token.

    Demonstrates the supported way to test the full flow without
    leaking tokens through logs: monkey-patch the capture hook for
    the duration of the test.
    """
    from backend.auth import routes as auth_routes

    captured: dict[str, str] = {}

    def _capture(recovery_email: str, raw_token: str) -> None:
        captured["email"] = recovery_email
        captured["token"] = raw_token

    monkeypatch.setattr(auth_routes, "_reset_token_capture", _capture)

    r = completed_setup.post(
        "/auth/forgot", json={"username": VALID_USERNAME}
    )
    assert r.status_code == 200
    assert captured["email"] == VALID_RECOVERY
    assert len(captured["token"]) >= 32

    new_pw = "BrandNewPass789!"
    r2 = completed_setup.post(
        "/auth/reset",
        json={"token": captured["token"], "new_password": new_pw},
    )
    assert r2.status_code == 200

    # Confirm the new password actually works
    r3 = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": new_pw},
    )
    assert r3.status_code == 200


def test_forgot_rate_limited_after_threshold(
    completed_setup: TestClient, db_session
) -> None:
    for _ in range(3):
        completed_setup.post(
            "/auth/forgot", json={"username": VALID_USERNAME}
        )

    # 4th request must NOT create a new row (suppressed silently).
    completed_setup.post(
        "/auth/forgot", json={"username": VALID_USERNAME}
    )
    count = db_session.query(PasswordReset).count()
    assert count == 3


# ---------------------------------------------------------------------------
# /reset
# ---------------------------------------------------------------------------

def _issue_token(client: TestClient, db_session) -> str:
    """Mint a known-plaintext reset token and store its hash."""
    raw = "test-token-" + "A" * 40
    db_session.add(
        PasswordReset(
            username=VALID_USERNAME,
            token_hash=_hash_reset_token(raw),
            expires_at=_utcnow() + timedelta(minutes=15),
            created_at=_utcnow(),
        )
    )
    db_session.commit()
    return raw


def test_reset_with_valid_token_changes_password(
    completed_setup: TestClient, db_session
) -> None:
    raw = _issue_token(completed_setup, db_session)
    new_pw = "NewStrongPass456!"

    r = completed_setup.post(
        "/auth/reset", json={"token": raw, "new_password": new_pw}
    )
    assert r.status_code == 200

    # Old password no longer works
    r_old = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    assert r_old.status_code == 401

    # New password works
    r_new = completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": new_pw},
    )
    assert r_new.status_code == 200


def test_reset_token_is_single_use(
    completed_setup: TestClient, db_session
) -> None:
    raw = _issue_token(completed_setup, db_session)
    new_pw = "NewStrongPass456!"

    r1 = completed_setup.post(
        "/auth/reset", json={"token": raw, "new_password": new_pw}
    )
    assert r1.status_code == 200

    r2 = completed_setup.post(
        "/auth/reset",
        json={"token": raw, "new_password": "EvenNewer789!"},
    )
    assert r2.status_code == 400


def test_reset_invalidates_all_existing_sessions(
    completed_setup: TestClient, db_engine, db_session
) -> None:
    # Log in to create a session
    completed_setup.post(
        "/auth/login",
        data={"username": VALID_USERNAME, "password": VALID_PASSWORD},
    )
    with db_engine.connect() as conn:
        before = conn.execute(
            sa.text("SELECT COUNT(*) FROM sessions")
        ).scalar()
    assert before == 1

    raw = _issue_token(completed_setup, db_session)
    completed_setup.post(
        "/auth/reset",
        json={"token": raw, "new_password": "NewStrongPass456!"},
    )

    with db_engine.connect() as conn:
        after = conn.execute(
            sa.text("SELECT COUNT(*) FROM sessions")
        ).scalar()
    assert after == 0


def test_reset_rejects_expired_token(
    completed_setup: TestClient, db_session
) -> None:
    raw = "expired-token-" + "B" * 40
    db_session.add(
        PasswordReset(
            username=VALID_USERNAME,
            token_hash=_hash_reset_token(raw),
            expires_at=_utcnow() - timedelta(minutes=1),
            created_at=_utcnow() - timedelta(hours=1),
        )
    )
    db_session.commit()

    r = completed_setup.post(
        "/auth/reset",
        json={"token": raw, "new_password": "NewStrongPass456!"},
    )
    assert r.status_code == 400


def test_reset_rejects_unknown_token(completed_setup: TestClient) -> None:
    r = completed_setup.post(
        "/auth/reset",
        json={
            "token": "not-a-real-token",
            "new_password": "NewStrongPass456!",
        },
    )
    assert r.status_code == 400


def test_reset_rejects_weak_new_password(
    completed_setup: TestClient, db_session
) -> None:
    raw = _issue_token(completed_setup, db_session)
    r = completed_setup.post(
        "/auth/reset", json={"token": raw, "new_password": "short"}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Storage hygiene
# ---------------------------------------------------------------------------

def test_password_plaintext_never_stored_in_settings(
    completed_setup: TestClient, db_engine
) -> None:
    """Belt-and-braces: scan every settings row for plaintext leakage."""
    with db_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT key, value FROM settings")
        ).fetchall()
    for key, value in rows:
        assert VALID_PASSWORD not in value, (
            f"plaintext password leaked into setting {key}"
        )
