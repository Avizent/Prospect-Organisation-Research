"""End-to-end "no key in logs" check.

Stores a synthetic Anthropic key in the (fake, in-memory) keyring,
runs a successful call through :class:`CloudClient`, then triggers a
trap. After both, scans every disk artefact the cost-control layer
might have written — the audit log, the incident log, the budget
state file — for the synthetic key. None must contain it.

The key string is deliberately non-Anthropic so the CLAUDE.md
``sk-ant``/``client_secret``/``password.*=`` grep guard cannot trip
on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetGuard,
    MaxTokensExceeded,
)
from backend.cost_control import audit as audit_mod
from backend.credentials import keychain


# Synthetic, intentionally not in sk-ant-* shape.
FAKE_KEY = "test-anthropic-leakcheck-" + "Z" * 40


def _all_files_under(path: Path) -> list[Path]:
    return [p for p in path.rglob("*") if p.is_file()]


def test_fake_key_never_appears_on_disk_after_round_trip(
    fake_sdk, budget_state: BudgetState, db_session, job_id, capsys
):
    keychain.set_anthropic_key(FAKE_KEY)
    assert keychain.get_anthropic_key() == FAKE_KEY  # sanity

    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )

    # One successful call (writes audit + budget state).
    fake_sdk.set_next_response(input_tokens=100, output_tokens=50)
    client.messages_create(
        agent="needs",
        model="claude-haiku-4-5",
        job_id=job_id,
        messages=[{"role": "user", "content": "harmless prompt"}],
        max_tokens=1000,
        input_tokens_estimate=100,
    )

    # One trap firing (writes incident log).
    with pytest.raises(MaxTokensExceeded):
        client.messages_create(
            agent="critic",
            model="claude-haiku-4-5",
            job_id=job_id,
            messages=[{"role": "user", "content": "leaky"}],
            max_tokens=99_999,
            input_tokens_estimate=10,
        )

    # Scan every file under ANS_HOME.
    for file_path in _all_files_under(audit_mod.ANS_HOME):
        text = file_path.read_text(encoding="utf-8", errors="replace")
        assert FAKE_KEY not in text, (
            f"FAKE_KEY leaked into {file_path}"
        )

    # Scan stdout/stderr captured during the test.
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err

    # Belt-and-braces: the audit log this month must exist and not be empty.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    audit_path = audit_mod.ANS_HOME / "audit" / f"audit-{stamp}.jsonl"
    assert audit_path.exists()
    assert audit_path.stat().st_size > 0
