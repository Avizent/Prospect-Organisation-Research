"""Microsoft 365 Graph API delivery module.

Critical invariant: send_drafts() takes NO recipient parameter.
Recipients are always and only the authenticated user's UPN from /me.
Any attempt to override is an ExternalRecipientBlocked exception.

Auth: MSAL delegated permissions (Mail.Send, User.Read).
Credentials from macOS Keychain (never from env or source).

Recipient lock (defence in depth):
  1. Cache UPN at session start via GET /me
  2. Set recipient = self.upn only
  3. Re-validate immediately before POST /me/sendMail
  4. Capitalisation normalised before comparison

Failure handling: 401/403 refresh, >25MB split into two emails,
network failure 3 retries with exponential backoff.

Implemented in step 13 (Opus — recipient-lock is the entire compliance story).
Post-step-13: Opus audit pass required over this file for bypass paths.
"""
# TODO: implement in step 13
