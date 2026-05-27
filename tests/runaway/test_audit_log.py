"""Audit JSONL — schema, file mode, prompt hashing, no key material."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.cost_control import (
    BudgetState,
    CloudClient,
    JobBudgetGuard,
)
from backend.cost_control import audit as audit_mod


def _audit_lines() -> list[dict]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    path = audit_mod.ANS_HOME / "audit" / f"audit-{stamp}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_write_audit_appends_expected_schema():
    audit_mod.write_audit(
        job_id="jid-1",
        agent="research",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.0033,
        prompt_hash="sha256:deadbeef",
        budget_remaining_job=1.85,
        budget_remaining_day=9.50,
    )
    rows = _audit_lines()
    assert len(rows) == 1
    row = rows[0]
    expected_keys = {
        "ts", "job_id", "agent", "model", "input_tokens", "output_tokens",
        "cost_usd", "prompt_hash", "approved_by",
        "budget_remaining_job", "budget_remaining_day",
    }
    assert set(row.keys()) == expected_keys
    assert row["agent"] == "research"
    assert row["approved_by"] == "user"


def test_audit_file_mode_is_0o600():
    audit_mod.write_audit(
        job_id="jid", agent="needs", model="claude-haiku-4-5",
        input_tokens=10, output_tokens=10, cost_usd=0.0001,
        prompt_hash="sha256:abc",
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    path = audit_mod.ANS_HOME / "audit" / f"audit-{stamp}.jsonl"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_hash_prompt_is_deterministic():
    a = audit_mod.hash_prompt([{"role": "user", "content": "x"}])
    b = audit_mod.hash_prompt([{"role": "user", "content": "x"}])
    c = audit_mod.hash_prompt([{"role": "user", "content": "y"}])
    assert a == b
    assert a != c
    assert a.startswith("sha256:")
    # 7 char prefix + 64 hex digest
    assert len(a) == len("sha256:") + 64


def test_cloud_client_audit_row_includes_prompt_hash_not_content(
    fake_sdk, budget_state: BudgetState, db_session, job_id
):
    guard = JobBudgetGuard(job_id=job_id, cap_usd=2.00, db=db_session)
    client = CloudClient(
        sdk_client=fake_sdk, budget_state=budget_state, job_guard=guard
    )
    secret = "test-api-key-zzzz-" + "X" * 40  # synthetic, never real
    fake_sdk.set_next_response(input_tokens=50, output_tokens=80)
    client.messages_create(
        agent="critic",
        model="claude-haiku-4-5",
        job_id=job_id,
        messages=[{"role": "user", "content": secret}],
        max_tokens=100,
        input_tokens_estimate=50,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m")
    path = audit_mod.ANS_HOME / "audit" / f"audit-{stamp}.jsonl"
    contents = path.read_text()
    # Secret string never appears in the audit log; only the hash does.
    assert secret not in contents
    rows = [json.loads(line) for line in contents.splitlines() if line]
    assert rows[0]["prompt_hash"].startswith("sha256:")


def test_write_audit_never_raises_when_filesystem_unwritable(monkeypatch, tmp_path):
    """If the audit path can't be written, callers must not see an error.

    Simulated by pointing ``ANS_HOME`` at a file (not a directory), so
    ``mkdir`` and ``open`` both fail. The internal ``_append_jsonl``
    swallows the ``OSError`` and emits a stderr warning.
    """
    blocker = tmp_path / "blocker-file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(audit_mod, "ANS_HOME", blocker)
    # Should swallow without raising.
    audit_mod.write_audit(
        job_id="jid", agent="needs", model="claude-haiku-4-5",
        input_tokens=10, output_tokens=10, cost_usd=0.0001,
        prompt_hash="sha256:abc",
    )
