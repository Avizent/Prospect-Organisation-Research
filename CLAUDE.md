# ANS Prospect Tool — Project Memory

Read `ans-prospect-tool-handover.md` (decisions/rationale) and `ans-prospect-tool-BUILD-PROMPT.md` (execution order) before starting any work. The handover wins if documents conflict.

## Hard rules — never violate

1. No source-embedded credentials. All secrets go through macOS Keychain via the `keyring` library.
2. No SMTP. M365 Graph API only for all email.
3. Recipient-locked delivery. `send_drafts()` resolves the UPN from `/me` and sends only there. No recipient parameter exposed anywhere.
4. Seven runaway traps must be present and tested before any real Claude API call is made. Stub the client until `tests/runaway/` all pass.
5. Cloud Claude API only. No local LLMs, no fallback, no hybrid.
6. No localStorage or sessionStorage for sensitive data. Sessions are server-side; cookies are HttpOnly.
7. All generated documents watermarked as INTERNAL DRAFT.
8. Never build: TAPs/packet brokers, SMTP, multi-user accounts, TOTP/2FA, "Remember me", SF Symbols, arbitrary recipient field.
9. `git grep -E "(sk-ant|client_secret|password.*=.*\")"` must return nothing from production code at all times.
10. GDPR: source attribution on every contact, DNC flag honoured, deletion and SAR-export CLIs exist.

## Model switching (use /model at step boundaries)

| Steps | Model | Reason |
|-------|-------|--------|
| 1 | Haiku 4.5 | Pure boilerplate scaffold |
| 2 | Sonnet 4.6 | Schema care needed |
| 3, 4 (Keychain only) | **Opus 4.7** | Security-critical auth |
| 4 (routes/UI), 7, 8, 11, 12, 15, 16 | Sonnet 4.6 | Well-specified implementation |
| 5 | **Opus 4.7** | Runaway traps — most important defensive code |
| 6 (base wrapper only) | **Opus 4.7** | Core agent infrastructure |
| 6 (research agent), 7 | Sonnet 4.6 | Well-specified on solid base |
| 9 (state machine only) | **Opus 4.7** | State machine bugs = stuck jobs |
| 10 (mapping + critic) | **Opus 4.7** | Subtle reasoning required |
| 13, 14 | **Opus 4.7** | Recipient-lock + auth-adjacent |
| 17, 18 | Sonnet baseline; Opus for hard problems | Polish + E2E |

After step 13: ask Opus to audit `backend/delivery/m365.py` for recipient-lock bypass paths.
After step 5: ask Opus to audit `backend/cost_control/` for trap-evasion paths.

## Credential checkpoints — hard stops

Do not proceed past these without explicit user confirmation:

- **Checkpoint A** (after step 3): user must visit `/setup` in browser and complete the setup wizard.
- **Checkpoint B** (during step 4): user must enter Anthropic API key in `/admin/credentials` and confirm the Test button passes.
- **Checkpoint C** (start of step 6): user must confirm "ready to spend" before first real Claude API call.
- **Checkpoint D** (start of step 13): user must have an Entra ID app registration with `Mail.Send` and `User.Read` delegated permissions ready.
- **Checkpoint E** (during step 14): user must confirm recovery email is reachable from the M365 tenant.
- **Checkpoint F** (start of step 18): user must name three real ≥1000-staff companies and confirm budget headroom.

See `ans-prospect-tool-CREDENTIAL-CHECKPOINTS.md` for full scripts for each checkpoint.

## Key architecture facts

- Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite at `~/.ans-tool/data.db`
- Frontend: vanilla HTML/CSS/JS, Phosphor Icons (MIT). No framework, no build step.
- Models in config.yaml: `claude-sonnet-4-5` for writers/research, `claude-haiku-4-5` for critic.
- Job state machine: `created → researching → briefing_ready → user_editing → regenerating_section → approved → generating_documents → complete | failed`
- All outputs light-themed regardless of app theme.
- Approval gate is the most important screen — Stage 2 reads the *approved* briefing.json, never the raw dossier.
