"""Shared Claude API client wrapper for all agents.

Responsibilities:
- Enforces per-agent max_tokens ceiling (from limits.py) on every call
- Enforces per-agent tool-call ceiling (from limits.py)
- Retries on transient API errors (up to 3 attempts, exponential backoff)
- Parses and validates JSON responses
- Surfaces partial errors cleanly
- Delegates cost accounting to cost_control.cloud_client

Implemented in step 6 (Opus — core infrastructure for every later agent).
"""
# TODO: implement in step 6
