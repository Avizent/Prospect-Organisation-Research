"""macOS Keychain wrapper for ANS-tool credentials.

Single point of truth for credential I/O. No other module in the
application calls `keyring` directly — so any future audit of how
secrets enter or leave the process starts and ends here.

Storage layout
--------------
Service identifier:   "com.avizent.ans-prospect-tool"
Account name(s):      "anthropic.api_key"   — the raw Anthropic API key
                      (more accounts will be added in later steps;
                       e.g. M365 refresh token in step 13)

The service identifier is part of the public storage contract: renaming
it would orphan keys in any user's Keychain. It's pinned by a
regression test (tests/credentials/test_keychain.py) and must not change
without a coordinated migration.

Threat model in scope
---------------------
- The key never lives in environment variables, .env, source, or the
  SQLite database. It is held in memory only inside set_/get_ calls.
- Callers must not log the return value of get_anthropic_key().
- The single bit of public meta-state about the credential
  (`credentials.anthropic.configured_at`) lives in the SQLite settings
  table and is written by the admin route, not by this module.

Out of scope
------------
- Anthropic API key *validation* — that arrives in a later step once
  the runaway-trap cost controls land. This module only stores bytes.
"""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError


# ---------------------------------------------------------------------------
# Storage identifiers (public storage contract — do not rename).
# ---------------------------------------------------------------------------

KEYCHAIN_SERVICE: str = "com.avizent.ans-prospect-tool"
"""macOS Keychain `service` attribute used for every secret in this app."""

ACCOUNT_ANTHROPIC: str = "anthropic.api_key"
"""macOS Keychain `account` attribute for the Anthropic API key."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_anthropic_key(api_key: str) -> None:
    """Persist *api_key* to the macOS Keychain.

    On the first call from a given binary, macOS may prompt the user
    once to authorise the write. Subsequent writes are silent because
    the user's login session unlocks the keychain.

    The function does not validate the key format; that's deliberate —
    Anthropic's prefix rules may change, and a strict validator here
    would risk locking a user out of a key the SDK would have accepted.
    The caller (admin route) enforces a non-empty/non-whitespace check.
    """
    if not isinstance(api_key, str):
        raise TypeError("api_key must be str")
    if not api_key:
        raise ValueError("api_key must be non-empty")
    keyring.set_password(KEYCHAIN_SERVICE, ACCOUNT_ANTHROPIC, api_key)


def get_anthropic_key() -> str | None:
    """Return the stored Anthropic API key, or None if absent.

    Callers MUST NOT log or echo the return value. Treat the result as
    opaque write-once-into-Anthropic-client material.
    """
    return keyring.get_password(KEYCHAIN_SERVICE, ACCOUNT_ANTHROPIC)


def delete_anthropic_key() -> bool:
    """Remove the stored Anthropic API key.

    Returns True if a key was present and removed; False if nothing
    was stored. Always idempotent — no exception on absent entries.
    """
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, ACCOUNT_ANTHROPIC)
        return True
    except PasswordDeleteError:
        return False


def has_anthropic_key() -> bool:
    """Cheap presence check. Does not return any of the key material."""
    return get_anthropic_key() is not None
