"""Unit tests for backend.auth.password.

Covers:
  - argon2id hash format and properties
  - Verification of correct passwords
  - Rejection of wrong passwords, empty/malformed hashes
  - Strength policy: length, uppercase, lowercase, digit
"""

from __future__ import annotations

from backend.auth import password as pw


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------

def test_hash_password_returns_argon2id_string() -> None:
    h = pw.hash_password("CorrectHorse123Battery")
    assert h.startswith("$argon2id$"), f"unexpected algorithm prefix: {h[:20]}"
    # Argon2 encoded hashes are long; defaults yield well over 60 chars.
    assert len(h) > 60


def test_hash_password_is_salted_and_nondeterministic() -> None:
    a = pw.hash_password("CorrectHorse123Battery")
    b = pw.hash_password("CorrectHorse123Battery")
    assert a != b, "argon2 must include random salt; identical hashes is a bug"


def test_verify_password_accepts_correct_password() -> None:
    h = pw.hash_password("CorrectHorse123Battery")
    assert pw.verify_password("CorrectHorse123Battery", h) is True


def test_verify_password_rejects_wrong_password() -> None:
    h = pw.hash_password("CorrectHorse123Battery")
    assert pw.verify_password("WrongHorse123Battery", h) is False


def test_verify_password_rejects_empty_hash() -> None:
    assert pw.verify_password("anything", "") is False


def test_verify_password_rejects_malformed_hash() -> None:
    assert pw.verify_password("anything", "not-a-real-hash") is False


def test_needs_rehash_false_for_fresh_hash() -> None:
    h = pw.hash_password("CorrectHorse123Battery")
    assert pw.needs_rehash(h) is False


# ---------------------------------------------------------------------------
# validate_password_strength
# ---------------------------------------------------------------------------

def test_strength_accepts_valid_password() -> None:
    assert pw.validate_password_strength("CorrectHorse123") == []


def test_strength_rejects_short_password() -> None:
    errors = pw.validate_password_strength("Ab1xyz")
    assert any("12 characters" in e for e in errors)


def test_strength_rejects_missing_uppercase() -> None:
    errors = pw.validate_password_strength("correcthorse123")
    assert any("uppercase" in e for e in errors)


def test_strength_rejects_missing_lowercase() -> None:
    errors = pw.validate_password_strength("CORRECTHORSE123")
    assert any("lowercase" in e for e in errors)


def test_strength_rejects_missing_digit() -> None:
    errors = pw.validate_password_strength("CorrectHorseBattery")
    assert any("digit" in e for e in errors)


def test_strength_accumulates_all_errors() -> None:
    # "short" → fails length, uppercase, digit
    errors = pw.validate_password_strength("short")
    assert len(errors) >= 3
