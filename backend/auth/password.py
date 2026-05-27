"""Password hashing, verification, and strength validation.

Algorithm: argon2id via argon2-cffi default parameters.
Used for: initial setup, login verification, password change, reset flow.

Hard rules:
  - Passwords are NEVER logged.
  - Only hashes are stored (settings.auth.password_hash).
  - Plaintext is held in memory only for the duration of a single request.
"""

from __future__ import annotations

import string

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)


# Single shared hasher: argon2-cffi defaults
# (time_cost=2, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16).
# Defaults are appropriate for interactive login on a developer-class machine.
_HASHER = PasswordHasher()


# ---------------------------------------------------------------------------
# Hashing / verification
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return an argon2id encoded hash for *password*.

    The returned string includes the algorithm identifier, parameters,
    salt, and digest — everything needed to verify later without storing
    parameters separately.
    """
    if not isinstance(password, str):
        raise TypeError("password must be str")
    return _HASHER.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Return True iff *password* verifies against *stored_hash*.

    Returns False on mismatch, malformed hash, or empty stored_hash.
    Never raises for predictable failures so callers can branch cleanly.
    """
    if not stored_hash:
        return False
    try:
        return _HASHER.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Return True if *stored_hash* uses outdated parameters.

    Callers should re-hash on successful login when this returns True so
    that parameter upgrades take effect transparently.
    """
    if not stored_hash:
        return False
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


# ---------------------------------------------------------------------------
# Strength validation
# ---------------------------------------------------------------------------
#
# Policy:
#   - Minimum 12 characters
#   - At least one uppercase letter
#   - At least one lowercase letter
#   - At least one digit
#
# Symbols are not required; long passphrases score well without them and
# requiring symbols pushes users toward predictable substitutions.
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 12


def validate_password_strength(password: str) -> list[str]:
    """Return a list of human-readable error messages.

    Empty list means the password meets policy. Messages are safe to
    show to the user; they do not echo the password back.
    """
    errors: list[str] = []

    if not isinstance(password, str):
        return ["Password must be a string."]

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if not any(c in string.ascii_uppercase for c in password):
        errors.append("Password must contain at least one uppercase letter.")
    if not any(c in string.ascii_lowercase for c in password):
        errors.append("Password must contain at least one lowercase letter.")
    if not any(c in string.digits for c in password):
        errors.append("Password must contain at least one digit.")

    return errors
