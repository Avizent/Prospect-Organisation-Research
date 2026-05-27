# ANS Prospect Tool — Claude Code Setup Verification

> **Workflow position:** Paste this prompt into Claude Code **first**, before the build kickoff message. It reads the handover and build documents only to understand what dependencies are needed, then produces a GO / NO-GO readiness report. It installs nothing, modifies nothing, and explicitly does not start step 1 of the build.

---

## The prompt

Copy everything between the markers below and paste it as the opening message to Claude Code in your empty project directory (the directory should already contain `ans-prospect-tool-handover.md` and `ans-prospect-tool-BUILD-PROMPT.md`).

---

**---- PASTE FROM HERE ----**

You are in **verification mode only**. Do not write code, do not modify any file, do not install any package, do not run the build. Read `ans-prospect-tool-handover.md` and `ans-prospect-tool-BUILD-PROMPT.md` in this directory only to understand the dependencies the build will need. Explicitly ignore the "Begin with step 1" instruction at the end of the build prompt — you are not building yet.

Run the following checks and produce a single markdown readiness report with columns `| # | Item | Status | Detail / Suggested fix |`. Status values are PASS, WARN, or FAIL. After the table, output a one-paragraph summary stating **GO** or **NO-GO**, with NO-GO listing the blocking items.

Constraints:
- Read-only commands only. No `mkdir` that persists, no `touch`, no installs, no `git init`.
- One network call permitted: a `curl -I https://api.anthropic.com` to confirm reachability. Do not call the Anthropic API itself.
- Where a check requires the user to confirm something (e.g. "do you have an API key"), ask the user a direct yes/no question rather than searching the filesystem.

### Checklist

1. **Operating system** — `uname -a` confirms Darwin (macOS). The handover assumes macOS Keychain and a MacBook Pro M4. FAIL if not macOS.
2. **Shell, user, not-root** — print `$SHELL` and `whoami`. FAIL if running as root.
3. **Working directory and required documents** — `pwd` and `ls -la`. Confirm `ans-prospect-tool-handover.md` and `ans-prospect-tool-BUILD-PROMPT.md` are both present. FAIL if either is missing.
4. **Directory is otherwise empty** — list anything beyond the two `.md` files, this README, and any dotfiles. WARN if pre-existing project files are present (the build assumes a clean slate).
5. **Git** — `git --version` and `git status`. WARN if not yet initialised (the build will initialise it at step 1; do not initialise now).
6. **Python version** — `python3 --version` must be ≥ 3.11. FAIL if lower. If `pyenv` or `asdf` is present, note it in Detail.
7. **pip and venv** — `python3 -m pip --version` and `python3 -m venv --help` both succeed. FAIL if either fails.
8. **Ghostscript** — `gs --version`. Required for EPS, AI, and complex PDF logo decoding. FAIL if missing; suggested fix: `brew install ghostscript`.
9. **Poppler** — `pdftoppm -v` (writes version to stderr, that's fine). Required for `pdf2image`. FAIL if missing; suggested fix: `brew install poppler`.
10. **SQLite CLI** — `sqlite3 --version`. Helpful but not strictly required. WARN if missing; suggested fix: `brew install sqlite`.
11. **macOS Keychain access** — `security list-keychains` succeeds and lists the user's login keychain. FAIL if running over SSH without a GUI session, because Keychain needs a logged-in graphical session to unlock. Note this in Detail if relevant.
12. **App data directory writable** — verify `~/.ans-tool` either does not exist or is writable. Do **not** create it. Use `[ -w ~ ]` or check parent writability. FAIL if home directory is not writable.
13. **Network reachability** — `curl -sI https://api.anthropic.com -o /dev/null -w "%{http_code}\n"`. Any HTTP response (including 401 or 403) means reachable. FAIL only on connection refused / DNS failure.
14. **Disk space** — `df -h ~` shows at least 5 GB free. WARN below 5 GB, FAIL below 1 GB.
15. **Anthropic API key readiness** (user question) — ask the user: "Do you have an Anthropic API key ready to enter via the admin UI once the app is built? (yes/no/will-create-later)". Do not look for the key on disk or in env. Record their answer.
16. **M365 app registration readiness** (user question) — ask the user: "Do you have, or can you create, a Microsoft Entra ID app registration with Mail.Send and User.Read delegated permissions? (yes/no/will-create-later)". Record their answer.
17. **M365 recovery email decision** (user question) — ask the user: "Which email address should receive forgotten-password reset links? It must be reachable from the Microsoft tenant whose credentials you'll enter in /admin/credentials." Record their answer for reference at step 14 of the build.
18. **Claude Code model self-report** — state which model you (Claude Code) are currently running. The build prompt recommends specific models per step (see its build-order table); the user will switch via `/model` at step boundaries. WARN if currently on a model the user did not intend for step 1 (step 1 should be Haiku to keep boilerplate cheap).
19. **CLAUDE.md presence** — check whether the project root contains a `CLAUDE.md` file. WARN if absent; note in Detail that the user may want one created after verification (as a separate task) to capture the hard rules from section 2 of the build prompt as persistent project memory.

### After the report

Stop and wait. Do not proceed to any build step. If status is NO-GO, list the install commands the user should run (e.g. `brew install ghostscript poppler`) but do not run them yourself. If status is GO, simply say so and await the user's go-ahead.

**---- PASTE TO HERE ----**

---

## After the verification report

If the report comes back **GO**:

1. **Optional but recommended:** ask Claude Code to draft a minimal `CLAUDE.md` for the project root, taking the hard rules from section 2 of the build prompt (no source-embedded credentials, recipient-locked delivery, seven traps before any real Claude call, no TAPs or packet brokers, no SMTP) and the model-switching guidance from the build-order table. This becomes persistent project memory — Claude Code reads it automatically on every session. Phrase the request as: *"Draft a `CLAUDE.md` for this project that captures the hard rules from section 2 of the build prompt and the model-switching guidance from the build-order table. Do not include the full build steps — those live in the build prompt. Keep it under 80 lines. Show me the draft before writing the file."*

2. Switch to Haiku for step 1: type `/model` and choose Haiku 4.5.

3. Kick off the build with the message from the previous turn: *"Read `ans-prospect-tool-handover.md` and `ans-prospect-tool-BUILD-PROMPT.md`. The handover is the source of truth for decisions and reasoning; the build prompt is the source of truth for execution order. Confirm you've parsed both by enumerating the seven runaway traps, then begin with step 1 of the build order."*

If the report comes back **NO-GO**:

1. Run the suggested fixes (typically `brew install ghostscript poppler` covers most cases).
2. Re-paste the verification prompt and confirm GO before proceeding.
