"""Auth route handlers: /setup, /login, /logout, /forgot, /reset.

/setup   — first-run wizard; returns 404 after setup_completed_at is set
/login   — argon2id verification, session cookie issuance, rate limiting
/forgot  — timing-safe response, token issuance, recovery email via M365
/reset   — token validation (hash lookup, expiry, single-use), password update

Session cookie: HttpOnly, SameSite=Strict, Secure (except localhost), 4h expiry.
Rate limit: 5 failures/IP/15min → 15min lockout.

Implemented in step 3 (Opus — security-critical).
"""
# TODO: implement in step 3
