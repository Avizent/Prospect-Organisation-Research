"""JobBudgetGuard — Trap 3: per-job budget enforcement.

Wraps every Claude API call. Before the call, estimates cost against the
per-job cap (default $2.00, UI max $10.00, above $10 requires config edit).
Raises JobBudgetExceeded if the estimate would breach the cap.

Implemented in step 5 (Opus — most important defensive infrastructure).
"""
# TODO: implement in step 5
