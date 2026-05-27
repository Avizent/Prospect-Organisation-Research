"""Daily and monthly budget state — Trap 4.

Atomic-write JSON state at ~/.ans-tool/budget_state.json using fcntl locking.
Caps:
  daily_soft  $10  — warn, continue
  daily_hard  $25  — block (--force override available)
  monthly_hard $200 — block (config edit override only)

Implemented in step 5 (Opus).
"""
# TODO: implement in step 5
