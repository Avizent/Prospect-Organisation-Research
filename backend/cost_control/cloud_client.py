"""Anthropic SDK wrapper that enforces all seven runaway traps.

All Claude API calls in this application go through this wrapper.
No agent or other module calls the Anthropic SDK directly.

Traps enforced here:
  Trap 1 — max_tokens ceiling per agent (from agents/limits.py)
  Trap 2 — tool-call ceiling per agent (from agents/limits.py)
  Trap 3 — per-job budget guard (job_budget.py)
  Trap 4 — daily/monthly caps (budget_state.py)
  Trap 5 — wall-clock timeouts: 90s per call, 5min per agent, 20min per job
  Trap 6 — critic-revise loop bound: max one revision pass
  Trap 7 — concurrent job semaphore: max 3 jobs (enforced in main.py)

Credentials: Anthropic API key fetched from macOS Keychain via keyring.
Never reads from environment variables or source code.

Implemented in step 5 (Opus) with stub for steps 1-4.
"""
# TODO: implement in step 5 — stub client used until traps pass
