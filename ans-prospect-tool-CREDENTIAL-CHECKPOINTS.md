# ANS Prospect Tool — Credential & Secret Checkpoints

> **Purpose:** Tell Claude Code exactly when to pause the build and prompt the human for a credential, a browser action, or a spend approval. Without this, Claude Code will execute the build steps continuously and overshoot the natural human-in-the-loop moments.
>
> **How to use:** Two equally good options.
>
> **Option 1 (separate file, simple):** Keep this file in the project root next to the build prompt and handover. In your build kickoff message, add: *"Also read `ans-prospect-tool-CREDENTIAL-CHECKPOINTS.md` and treat every checkpoint as a hard stop — do not proceed past a checkpoint until I explicitly confirm."*
>
> **Option 2 (merged, more robust):** When you ask Claude Code to draft `CLAUDE.md`, ask it to incorporate the rules below into that file. `CLAUDE.md` is read automatically on every Claude Code session, so the checkpoints survive context resets.

---

## The rule

Before proceeding past any checkpoint listed below, Claude Code must:
1. Stop work.
2. Print the checkpoint's name and the action the human must take.
3. Wait for an explicit "ready" or "go" message from the human.
4. Do not assume credentials exist. Do not attempt to read them from the filesystem or environment. Do not write any credential to a source file.

If a checkpoint's preconditions are not met, do not attempt a workaround — surface the blocker and stop.

---

## Checkpoint A — After step 3 (auth) completes

**Trigger:** all auth tests pass and the app can boot.

**Claude Code says:**
> Step 3 (auth) is complete. To proceed I need you to run the app and complete the first-run setup wizard in your browser. In a separate terminal:
> ```
> uvicorn backend.main:app --host 127.0.0.1 --port 8765 --reload
> ```
> Then visit http://127.0.0.1:8765/setup and complete the wizard (username, password ≥12 chars with mixed character classes, recovery email). Confirm here when done so I can begin step 4.

**Human action:** complete the wizard in the browser. Reply "wizard done".

**Why this can't be automated:** the setup wizard writes the argon2id-hashed password directly to the SQLite settings table. Claude Code should not see your password.

---

## Checkpoint B — During step 4, after `/admin/credentials` is built

**Trigger:** `/admin/credentials` page renders and the Keychain wrapper is implemented.

**Claude Code says:**
> The `/admin/credentials` page is now live. To validate the Keychain integration end-to-end I need you to enter your Anthropic API key:
> 1. Visit http://127.0.0.1:8765/admin/credentials in your browser (logged in).
> 2. Enter your Anthropic API key in the field.
> 3. Click "Test" — it should perform a minimal `messages.create` call and report success.
>
> If you do not have an Anthropic API key yet, get one from https://console.anthropic.com/settings/keys. Confirm here when the Test button reports success so I can finish step 4.

**Human action:** enter API key, run test, reply "key entered, test passed".

**Why this can't be automated:** only you have the key. Claude Code should never see it directly.

---

## Checkpoint C — Start of step 6 (first real Claude call)

**Trigger:** about to wire the agent base wrapper to a real Claude API call for the first time (research agent dry-run).

**Claude Code says:**
> About to make the first real Claude API call (research agent against a test company). This is the first step that costs money. Before I proceed, confirm:
> 1. The Anthropic API key was entered and tested at Checkpoint B (yes/no).
> 2. Current `config.yaml` budgets — per-job $2.00, daily soft $10, daily hard $25, monthly hard $200. Adjust now if you want different test-phase caps.
> 3. Pick a test company for the research agent. Suggest a real ≥1000-staff enterprise you have legitimate prospecting interest in.
>
> Reply "ready, test on [Company Name], [URL]" when you've verified all three.

**Human action:** verify caps, pick a test company, reply with the company name and URL.

**Why this matters:** this is the spend gate. Trap 4 will protect you from runaway, but the *first* real call is psychologically the important one — you want to be deliberate, not surprised.

---

## Checkpoint D — Start of step 13 (M365 delivery)

**Trigger:** about to implement `backend/delivery/m365.py`.

**Claude Code says:**
> Step 13 needs Microsoft 365 credentials. To proceed I need an Entra ID app registration with the right permissions. If you haven't created one yet:
>
> 1. Go to https://portal.azure.com → Microsoft Entra ID → App registrations → New registration.
> 2. Name: "ANS Prospect Tool (local)". Supported account types: "Accounts in this organizational directory only".
> 3. Redirect URI: leave blank (the app uses device-code or local-port flow).
> 4. After creation, go to API permissions → Add a permission → Microsoft Graph → Delegated permissions → add `Mail.Send` and `User.Read`.
> 5. Click "Grant admin consent" (you'll need tenant admin rights, or ask your admin).
> 6. Go to Certificates & secrets → New client secret. Copy the **Value** (not the ID).
> 7. From the Overview page, copy the Application (client) ID and the Directory (tenant) ID.
>
> When done, paste the three values (client_id, client_secret, tenant_id) into http://127.0.0.1:8765/admin/credentials and click the M365 Test button. The test calls `/me` via Graph and should return your UPN.
>
> Confirm here when the M365 Test button reports success, including the UPN it returned, so I can implement the recipient-locking logic against that specific UPN.

**Human action:** create app registration, enter credentials, run test, reply "M365 test passed, UPN is [your-email@tenant]".

**Why this matters:** the recipient-lock is the entire compliance story for this app. Claude Code needs to know the exact UPN to bake into the recipient-lock tests.

---

## Checkpoint E — During step 14 (forgotten password)

**Trigger:** the password reset email-send code path is ready to test.

**Claude Code says:**
> The forgotten-password flow is built. To verify end-to-end I need to send a real reset email to the recovery address you set at Checkpoint A. Confirm:
> 1. Is the recovery email address reachable from the M365 tenant you configured at Checkpoint D? It must be — outbound mail from this app only goes through that tenant.
> 2. Are you ready for me to trigger a reset for the username you set at Checkpoint A?
>
> Reply "go" to send. The email should arrive within 60 seconds. Confirm receipt before I mark step 14 complete.

**Human action:** confirm reachability, reply "go", confirm receipt.

**Why this matters:** silent failures here are bad — they only surface when you've actually forgotten your password, which is the worst time to discover the recovery flow doesn't work.

---

## Checkpoint F — Start of step 18 (end-to-end test on real companies)

**Trigger:** every prior step complete and tests passing.

**Claude Code says:**
> Ready for end-to-end test on three real companies. Each full run uses both stages of the pipeline and the M365 delivery. Estimated cost per company: $1–3 depending on depth.
>
> Before I run:
> 1. Pick three real ≥1000-staff enterprises with legitimate prospecting interest. Provide name + URL for each.
> 2. Choose depth: Quick / Standard / Deep. Standard is recommended for first test.
> 3. Confirm you want the four drafts (briefing PDF, benefits PDF, FAQ PDF, objections XLSX) delivered to your M365 inbox for each.
> 4. Confirm current daily budget headroom is at least $10 (three runs at Standard depth).
>
> Reply with company list, depth, and "go for end-to-end" when ready.

**Human action:** pick companies, confirm depth and budget, reply with full instruction.

**Why this matters:** this is the validation moment for the whole build. You want the test cases to be ones you actually care about, so the output is useful regardless of bugs found.

---

## What Claude Code must never do

- Never write an API key, client secret, tenant ID, password, or any other credential to a source file.
- Never `cat` or `echo` a credential into the terminal.
- Never commit a `.env` file containing real values to git. The `.env.example` file lists only non-sensitive runtime toggles.
- Never read from `~/.anthropic`, `~/.config/anthropic`, or similar in an attempt to find a key the user hasn't entered through the admin UI.
- Never bypass the Keychain by writing secrets to `config.yaml`, the SQLite database (other than the argon2id-hashed app password), or any cache file.
- Never skip a checkpoint to "save time" — the checkpoints are the build, not friction.

---

## Quick reference

| Checkpoint | After step | What the human enters | Where |
|------------|-----------|----------------------|-------|
| A | 3 | App username, password, recovery email | Browser `/setup` |
| B | 4 (during) | Anthropic API key | Browser `/admin/credentials` |
| C | Start of 6 | "Ready" + test company | Claude Code chat |
| D | Start of 13 | M365 client_id, secret, tenant_id | Browser `/admin/credentials` |
| E | During 14 | "Go" + receipt confirmation | Claude Code chat |
| F | Start of 18 | Three companies + depth + "go" | Claude Code chat |
