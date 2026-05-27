# ANS Prospect Tool

A single-user, locally hosted AI-powered sales intelligence tool for Avizent Network Solutions. Researches target enterprise companies (≥1000 staff) and produces co-branded internal sales documents promoting ANS network emulators and traffic generators.

**⚠️ Internal use only. All outputs are watermarked drafts delivered to your own M365 mailbox.**

---

## What it does

1. You enter a company name, website URL, and upload their logo.
2. The app researches the company using Claude AI (web search, job postings, press, regulatory filings).
3. You review and edit a structured briefing, then approve it.
4. Three documents are generated in parallel: a benefits PDF, an FAQ PDF, and an objections XLSX.
5. All four files (including the briefing PDF) land in your M365 inbox as drafts.

---

## Prerequisites

- macOS (Keychain required)
- Python 3.11+
- Ghostscript: `brew install ghostscript`
- Poppler: `brew install poppler`
- An Anthropic API key ([get one here](https://console.anthropic.com/settings/keys))
- A Microsoft 365 account with an Entra ID app registration (see `/admin/credentials` setup guide)

---

## Quick start

```bash
# 1. Clone and enter the repo
git clone <your-repo-url>
cd ans-prospect-tool

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run database migrations
ans-tool db upgrade

# 5. Start the app
uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload

# 6. Open http://127.0.0.1:8765/setup in your browser
#    Complete the first-run wizard (username, password, recovery email)

# 7. Log in, go to /admin/credentials, enter your Anthropic API key and M365 credentials
```

---

## Project documents (read before asking Claude Code to build)

| File | Purpose |
|------|---------|
| `ans-prospect-tool-handover.md` | All decisions and rationale — the source of truth for *why* |
| `ans-prospect-tool-BUILD-PROMPT.md` | Execution order and step specs — the source of truth for *what* |
| `ans-prospect-tool-SETUP-CHECK.md` | Pre-build environment verification prompt for Claude Code |
| `ans-prospect-tool-CREDENTIAL-CHECKPOINTS.md` | Human-in-the-loop pause points during the build |
| `CLAUDE.md` | Persistent project memory (read automatically by Claude Code) |

---

## Knowledge base (`ans_knowledge/`)

The agents read these files on every job run. **You must populate them before the tool produces useful output.**

| File | What to add |
|------|------------|
| `products/emulators.md` | Pre-populated from ANS docs ✓ |
| `products/traffic_generators.md` | Pre-populated from ANS docs ✓ |
| `company.md` | Pre-populated from ANS docs ✓ |
| `case_studies.md` | Pre-populated with examples ✓ |
| `competitive_landscape.md` | **You must fill this** — competitor strengths/weaknesses |
| `pricing_tiers.md` | **You must fill this** — pricing guidance for sales context |
| `known_objections.md` | **You must fill this** — objections you encounter repeatedly |
| `brand/ans_logo.png` | **You must add this** — the ANS logo file |
| `brand/brand.json` | Pre-populated with defaults (update colours/font if needed) |

---

## CLI commands

```bash
ans-tool db upgrade                  # Run Alembic migrations
ans-tool audit week                  # Spend + trap summary, exports CSV
ans-tool delete-contact <id>         # GDPR contact deletion (audit-logged)
ans-tool delete-company <id>         # GDPR company cascade delete
ans-tool export-person <name>        # SAR export for a named individual
```

---

## Cost controls

Default limits (edit `config.yaml` to change):

| Cap | Default |
|-----|---------|
| Per job | $2.00 |
| Per job (UI max) | $10.00 |
| Daily soft | $10.00 |
| Daily hard | $25.00 |
| Monthly hard | $200.00 |

Seven independent runaway traps are enforced before any API call. Every trap trigger fires a macOS desktop notification and writes to `~/.ans-tool/incidents.jsonl`.

---

## Security notes

- All secrets stored in macOS Keychain. No credentials in source code, `.env` files, or the database.
- Email delivery is recipient-locked to your authenticated UPN. There is no way to send to another address.
- Sessions: HttpOnly cookies, SameSite=Strict, 4-hour sliding expiry.
- Passwords: argon2id hashing.
- Contacts: GDPR-compliant. DNC flag, deletion and SAR-export CLI commands included.
