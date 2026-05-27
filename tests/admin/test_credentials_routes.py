"""HTTP integration tests for /admin/credentials.

Covers:
  - Auth gating (anonymous → 401)
  - GET status before/after a key is stored
  - POST validation (empty → 400)
  - POST round-trip with macOS-keychain calls intercepted by an
    in-memory backend (see tests/admin/conftest.py)
  - DELETE removes both the keychain entry and the settings timestamp
  - POST /credentials/test returns the agreed 501 shape
  - Response leakage guard: no fake key value ever appears in a body

No test touches the real macOS Keychain or the real ~/.ans-tool/data.db.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.admin.routes import KEY_ANTHROPIC_CONFIGURED_AT
from backend.credentials import keychain
from backend.db.models import Setting


# Distinctively non-Anthropic strings so the CLAUDE.md grep guard
# cannot mistake them for a real key, and so any accidental leak in a
# response body is unmistakable.
FAKE_KEY = "test-anthropic-zzzz" + "Q" * 40


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------

def test_get_credentials_requires_auth(client: TestClient) -> None:
    r = client.get("/admin/credentials")
    assert r.status_code == 401


def test_post_credentials_requires_auth(client: TestClient) -> None:
    r = client.post("/admin/credentials", json={"api_key": FAKE_KEY})
    assert r.status_code == 401


def test_delete_credentials_requires_auth(client: TestClient) -> None:
    r = client.delete("/admin/credentials")
    assert r.status_code == 401


def test_test_credentials_requires_auth(client: TestClient) -> None:
    r = client.post("/admin/credentials/test")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET status
# ---------------------------------------------------------------------------

def test_get_credentials_initial_state(authed_client: TestClient) -> None:
    r = authed_client.get("/admin/credentials")
    assert r.status_code == 200
    body = r.json()
    assert body == {"configured": False, "configured_at": None}


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------

def test_post_credentials_stores_key_in_keychain(
    authed_client: TestClient, db_session
) -> None:
    r = authed_client.post(
        "/admin/credentials", json={"api_key": FAKE_KEY}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"message": "Anthropic credential stored"}

    # Key landed in the (fake) keychain
    assert keychain.get_anthropic_key() == FAKE_KEY

    # Settings timestamp set, value is an ISO string, no key material
    row = db_session.get(Setting, KEY_ANTHROPIC_CONFIGURED_AT)
    assert row is not None
    assert FAKE_KEY not in row.value
    # ISO-8601 form (YYYY-MM-DDTHH:MM:SS)
    assert "T" in row.value


def test_post_credentials_rejects_empty_string(
    authed_client: TestClient,
) -> None:
    r = authed_client.post("/admin/credentials", json={"api_key": ""})
    assert r.status_code == 400
    assert keychain.has_anthropic_key() is False


def test_post_credentials_rejects_whitespace_only(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        "/admin/credentials", json={"api_key": "   \t \n  "}
    )
    assert r.status_code == 400
    assert keychain.has_anthropic_key() is False


def test_post_credentials_strips_surrounding_whitespace(
    authed_client: TestClient,
) -> None:
    r = authed_client.post(
        "/admin/credentials", json={"api_key": f"   {FAKE_KEY}   "}
    )
    assert r.status_code == 200
    assert keychain.get_anthropic_key() == FAKE_KEY


def test_post_credentials_overwrites_existing(
    authed_client: TestClient, db_engine
) -> None:
    authed_client.post(
        "/admin/credentials", json={"api_key": FAKE_KEY}
    )
    new_key = "test-anthropic-overw" + "Z" * 40
    r = authed_client.post(
        "/admin/credentials", json={"api_key": new_key}
    )
    assert r.status_code == 200
    assert keychain.get_anthropic_key() == new_key

    # Still exactly one settings row for configured_at.
    with db_engine.connect() as conn:
        count = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM settings WHERE key = :k"
            ),
            {"k": KEY_ANTHROPIC_CONFIGURED_AT},
        ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# GET after POST
# ---------------------------------------------------------------------------

def test_get_after_post_reports_configured(
    authed_client: TestClient,
) -> None:
    authed_client.post(
        "/admin/credentials", json={"api_key": FAKE_KEY}
    )
    r = authed_client.get("/admin/credentials")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert isinstance(body["configured_at"], str)
    # Belt-and-braces: the response must never echo the key in any field.
    assert FAKE_KEY not in r.text


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def test_delete_credentials_removes_key_and_marker(
    authed_client: TestClient, db_session
) -> None:
    authed_client.post(
        "/admin/credentials", json={"api_key": FAKE_KEY}
    )
    assert keychain.has_anthropic_key() is True

    r = authed_client.delete("/admin/credentials")
    assert r.status_code == 200
    assert keychain.has_anthropic_key() is False

    db_session.expire_all()
    assert db_session.get(Setting, KEY_ANTHROPIC_CONFIGURED_AT) is None

    # GET reports clean state
    r2 = authed_client.get("/admin/credentials")
    assert r2.json() == {"configured": False, "configured_at": None}


def test_delete_credentials_is_idempotent(authed_client: TestClient) -> None:
    """Calling DELETE on a clean slate returns 200, not an error."""
    r = authed_client.delete("/admin/credentials")
    assert r.status_code == 200
    # And again.
    r2 = authed_client.delete("/admin/credentials")
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# /credentials/test — 501 placeholder
# ---------------------------------------------------------------------------

def test_test_credentials_returns_501(authed_client: TestClient) -> None:
    r = authed_client.post("/admin/credentials/test")
    assert r.status_code == 501
    assert r.json() == {
        "detail": (
            "credential testing arrives after cost controls are implemented"
        )
    }


# ---------------------------------------------------------------------------
# Storage hygiene
# ---------------------------------------------------------------------------

def test_key_never_appears_in_any_settings_row(
    authed_client: TestClient, db_engine
) -> None:
    """Scan every settings value for the fake key. None must contain it."""
    authed_client.post(
        "/admin/credentials", json={"api_key": FAKE_KEY}
    )
    with db_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT key, value FROM settings")
        ).fetchall()
    for key, value in rows:
        assert FAKE_KEY not in value, (
            f"api key leaked into settings row {key!r}"
        )
