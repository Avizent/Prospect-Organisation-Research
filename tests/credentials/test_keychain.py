"""Unit tests for backend.credentials.keychain.

These tests never touch the real macOS Keychain. A tiny in-memory
KeyringBackend is installed for the duration of each test, so the
suite is deterministic, prompt-free, and safe to run anywhere.

A "real key" never appears in these tests. The fixture strings are
distinctively non-Anthropic so the CLAUDE.md grep guard cannot trip on
them (no "sk-ant-" prefix), and so that any accidental log leak in
future code is unambiguous to spot.
"""

from __future__ import annotations

from typing import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from backend.credentials import keychain


# ---------------------------------------------------------------------------
# Synthetic test keys — deliberately NOT in sk-ant-* shape.
# ---------------------------------------------------------------------------

FAKE_KEY_A = "test-anthropic-aaaa" + "X" * 40
FAKE_KEY_B = "test-anthropic-bbbb" + "Y" * 40


# ---------------------------------------------------------------------------
# In-memory keyring backend for tests.
# ---------------------------------------------------------------------------

class _InMemoryKeyring(KeyringBackend):
    """A KeyringBackend that lives entirely in a dict.

    Implements the minimal protocol the wrapper needs:
    set_password, get_password, delete_password. Anything else
    raises NotImplementedError so accidental real-keychain usage
    fails loudly.
    """

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def _fake_keyring() -> Iterator[_InMemoryKeyring]:
    """Swap the active keyring for an in-memory one, per test."""
    original = keyring.get_keyring()
    backend = _InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)


# ---------------------------------------------------------------------------
# Storage contract — DO NOT change these without a migration plan.
# ---------------------------------------------------------------------------

def test_storage_constants_are_stable() -> None:
    """Renaming these would orphan real users' previously-stored keys.

    If you have a good reason to change them, write a migration step
    that reads from the old service/account names, writes to the new,
    and deletes the old — then update this test.
    """
    assert keychain.KEYCHAIN_SERVICE == "com.avizent.ans-prospect-tool"
    assert keychain.ACCOUNT_ANTHROPIC == "anthropic.api_key"


# ---------------------------------------------------------------------------
# set / get round-trip
# ---------------------------------------------------------------------------

def test_get_returns_none_when_unset() -> None:
    assert keychain.get_anthropic_key() is None
    assert keychain.has_anthropic_key() is False


def test_set_then_get_round_trips() -> None:
    keychain.set_anthropic_key(FAKE_KEY_A)
    assert keychain.get_anthropic_key() == FAKE_KEY_A
    assert keychain.has_anthropic_key() is True


def test_set_overwrites_previous_value() -> None:
    keychain.set_anthropic_key(FAKE_KEY_A)
    keychain.set_anthropic_key(FAKE_KEY_B)
    assert keychain.get_anthropic_key() == FAKE_KEY_B


# ---------------------------------------------------------------------------
# set validation
# ---------------------------------------------------------------------------

def test_set_rejects_empty_string() -> None:
    with pytest.raises(ValueError):
        keychain.set_anthropic_key("")
    assert keychain.has_anthropic_key() is False


def test_set_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        keychain.set_anthropic_key(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_returns_true_when_present() -> None:
    keychain.set_anthropic_key(FAKE_KEY_A)
    assert keychain.delete_anthropic_key() is True
    assert keychain.has_anthropic_key() is False


def test_delete_returns_false_when_absent() -> None:
    assert keychain.delete_anthropic_key() is False


def test_delete_is_idempotent() -> None:
    keychain.set_anthropic_key(FAKE_KEY_A)
    assert keychain.delete_anthropic_key() is True
    # Second call must not raise.
    assert keychain.delete_anthropic_key() is False
