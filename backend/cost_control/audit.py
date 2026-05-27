"""Audit logging for cost control.

Append-only JSONL at ~/.ans-tool/audit/audit-YYYY-MM.jsonl.
Trap triggers written to ~/.ans-tool/incidents.jsonl.
Fires macOS desktop notifications on trap trigger.

Schema per entry: ts, job_id, agent, model, input_tokens, output_tokens,
cost_usd, prompt_hash, approved_by, budget_remaining_job, budget_remaining_day.

Implemented in step 5 (Opus).
"""
# TODO: implement in step 5
