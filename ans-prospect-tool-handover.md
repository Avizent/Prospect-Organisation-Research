# ANS Prospect Tool — Project Handover Document

## Purpose

A two-stage AI-powered application that researches target enterprise companies (>1000 staff) and produces co-branded sales documents promoting Avizent Network Solutions (ANS) network emulators and traffic generators. Outputs are internal drafts delivered to the user's M365 mailbox for review; nothing goes externally direct from the app.

## High-level workflow

```
1. User opens web app, logs in (single-user, password-protected)
2. User enters: company name, website URL, uploads target company logo
3. STAGE 1 runs (cloud, Claude API):
   - Research agent gathers web-based intelligence
   - Contact extraction agent identifies key people
   - Needs inference agent produces lab maturity hypothesis
   - Briefing compiler produces structured briefing
   - Briefing PDF generated
4. HUMAN APPROVAL GATE in browser:
   - User reviews briefing, can inline-edit
   - User reviews extracted contacts
   - User adds free-form context
   - User sees cost estimate for stage 2
   - User approves with depth selection, OR aborts
5. STAGE 2 runs (cloud, Claude API):
   - Product mapping agent matches needs to ANS products
   - Three writers in parallel: benefits, FAQ, objections
   - Critic agent reviews with max one revision pass
   - Documents assembled (PDFs, XLSX)
6. M365 delivery:
   - All four files emailed to user's own M365 mailbox only
   - Hard recipient-locking prevents sending elsewhere
7. Database updated: company, contacts, job metadata persisted
```

## Architecture overview

**Stack:**
- Backend: Python 3.11+, FastAPI, Anthropic SDK (`anthropic>=0.40`), SQLAlchemy + Alembic, MSAL for M365
- Frontend: Vanilla HTML/CSS/JS, no framework (Apple HIG aesthetic)
- Documents: WeasyPrint (PDF from HTML/CSS), openpyxl (XLSX), Pillow + cairosvg + pdf2image + Ghostscript (logos)
- Database: SQLite at `~/.ans-tool/data.db`
- Secrets: macOS Keychain via `keyring` library
- Icons: Phosphor Icons (MIT licensed; SF Symbols are not permitted for web use)
- LLMs: Claude API only (Sonnet 4.5 for writers/research; Haiku 4.5 for critic)
- Local dependencies: Ghostscript, Poppler (for logo processing)

**Project structure:**

```
ans-prospect-tool/
├── README.md
├── .env.example                    # No secrets in env; uses Keychain
├── requirements.txt
├── config.yaml                     # Budgets, model choices, non-secret config
├── ans_knowledge/                  # User-supplied content
│   ├── products/
│   │   ├── emulators.md
│   │   └── traffic_generators.md
│   ├── company.md
│   ├── case_studies.md
│   ├── competitive_landscape.md
│   ├── pricing_tiers.md
│   ├── known_objections.md
│   └── brand/
│       ├── ans_logo.png
│       └── brand.json              # Primary colour, secondary, font
├── backend/
│   ├── main.py                     # FastAPI app
│   ├── orchestrator.py             # Pipeline runner, state machine
│   ├── agents/
│   │   ├── base.py                 # Shared client wrapper, retries, JSON parsing
│   │   ├── limits.py               # Per-agent max_tokens table
│   │   ├── research.py
│   │   ├── contact_extractor.py
│   │   ├── needs.py
│   │   ├── briefing_compiler.py
│   │   ├── mapping.py
│   │   ├── benefits_writer.py
│   │   ├── faq_writer.py
│   │   ├── objections_writer.py
│   │   └── critic.py
│   ├── prompts/
│   │   └── (system prompts per agent, externalised)
│   ├── tools/
│   │   ├── logo_processor.py
│   │   └── knowledge_loader.py
│   ├── cost_control/
│   │   ├── job_budget.py
│   │   ├── budget_state.py         # Atomic file ops with fcntl
│   │   ├── audit.py
│   │   ├── pricing.py
│   │   ├── approval.py
│   │   └── cloud_client.py         # SDK wrapper enforcing all controls
│   ├── delivery/
│   │   └── m365.py                 # Graph API, recipient-locking
│   ├── auth/
│   │   ├── routes.py               # Setup, login, forgot, reset
│   │   ├── sessions.py
│   │   └── password.py             # argon2id hashing
│   ├── db/
│   │   ├── models.py               # SQLAlchemy models
│   │   ├── session.py
│   │   └── migrations/             # Alembic
│   └── assembly/
│       ├── pdf_builder.py
│       ├── xlsx_builder.py
│       └── templates/
│           ├── briefing.html
│           ├── benefits.html
│           └── faq.html
├── frontend/
│   ├── index.html                  # Single SPA shell
│   ├── styles/
│   │   ├── tokens.css              # HIG design tokens
│   │   ├── components.css          # HIG component primitives
│   │   └── screens/                # Per-screen layouts
│   ├── js/
│   │   ├── app.js
│   │   ├── api.js
│   │   ├── auth.js
│   │   ├── gestures.js             # Trackpad gesture detection
│   │   └── shortcuts.js            # Keyboard shortcuts
│   └── icons/                      # Phosphor SVGs
├── jobs/                           # Per-job artifacts on disk
│   └── {job_id}/
│       ├── state.json
│       ├── logo_source.{ext}
│       ├── logo_header.png
│       ├── logo_large.png
│       ├── research_dossier.json
│       ├── briefing.json
│       ├── briefing.pdf
│       ├── benefits.pdf
│       ├── faq.pdf
│       ├── objections.xlsx
│       └── delivery_receipt.json
└── tests/
    ├── runaway/
    ├── delivery/
    ├── auth/
    └── ...
```

## Decisions made (all confirmed)

1. **Cloud-only** — no local LLMs. Hybrid was discussed and skipped.
2. **Web app** — FastAPI backend, vanilla JS frontend, browser UI.
3. **Product scope:** network emulators and traffic generators only. NOT TAPs or packet brokers (despite original brief mentioning them).
4. **Inputs:** company name + website URL + user-uploaded logo.
5. **Outputs:** briefing PDF, benefits PDF, FAQ PDF, objections XLSX. All watermarked as internal drafts. Light-themed regardless of app theme.
6. **Logo upload:** accepts PNG, JPG, JPEG, SVG, WebP, GIF, BMP, TIFF, PDF, EPS, AI. Auto-processes: trim, normalise to RGBA, generate two sizes (header 120px, large 300px), resolution warnings.
7. **Target audience:** IT Director / CTO/CIO / Network engineers — tiered structure in benefits doc (exec summary → technical fit → business case).
8. **Research depth:** ≥1000 staff enterprises. Standard depth ~7-10 minutes with depth selector on approval gate (Quick/Standard/Deep).
9. **Two-stage pipeline with human approval gate** — briefing reviewed before paid stage 2 runs.
10. **Co-branding:** ANS logo (user supplies once via knowledge base) + target company logo (user uploads per job).
11. **Authentication:** First-run setup wizard sets fixed credentials (NOT hardcoded in source). One user account. Eye-toggle on password fields. Forgotten-password recovery via email through M365.
12. **Delivery:** Microsoft Graph API only (Option A). Credentials entered via admin UI, stored in Keychain. Recipient-locked to authenticated user's UPN — refuses to send anywhere else.
13. **Database:** SQLite. Tables: companies, contacts, jobs, settings, password_resets, login_attempts, sessions. Located at `~/.ans-tool/data.db`.
14. **Re-research behaviour:** Match by name+URL. If <30 days old, ask user — use cached, refresh-merge, or full re-research. Contact merge preserves manual notes and DNC flags.
15. **Compliance:** GDPR-aware contact extraction. DNC flag, deletion commands, export-for-SAR command. Only network/infra/IT-exec roles recorded. No personal email domains.
16. **UI design:** macOS Human Interface Guidelines, optimised for MacBook Pro M4 (Liquid Retina XDR, P3 gamut, ProMotion 120Hz). Full glass material system, layer architecture, trackpad gestures, keyboard shortcuts, density modes. Light/Dark/System theme.
17. **Runaway protection:** Seven independent traps. Mandatory.

## The seven runaway traps (mandatory)

1. **Per-call `max_tokens` ceiling** — every API call passes per-agent value from `backend/agents/limits.py`. Values: research 4000, needs 3000, briefing 8000, mapping 2000, benefits 6000, faq 4000, objections 4000, critic 2000. No defaults.
2. **Tool-call ceilings** — research agent web_search ≤20, web_fetch ≤10; all other agents ≤3 tool calls. Exceeding raises `ToolCallLimitExceeded`, fails job, no retry.
3. **Per-job budget guard** — `JobBudgetGuard` wrapper enforces pre-call cost estimate against cap. Defaults: $2/job, max $10 via UI, above $10 requires config edit.
4. **Daily/monthly caps** — atomic-write JSON state in `~/.ans-tool/budget_state.json` with fcntl locking. Daily soft $10 (warn), daily hard $25 (block, override via `--force`), monthly hard $200 (config edit override).
5. **Wall-clock timeouts** — per API call 90s, per agent 5min, per job 20min. Per-job timeout is separate asyncio task that cancels orchestrator.
6. **Critic-revise loop bound** — maximum one revision pass. Writer→Critic→(if issues)→Writer→Critic (final, no v3)→ship v2 with warning flag if rejected.
7. **Concurrent job limit** — semaphore in `main.py`, max 3 concurrent jobs. Excess queue with visible status, queue persisted in SQLite.

**Visibility:** Live spend in UI (2s polling), `~/.ans-tool/incidents.jsonl` append-only trap-trigger log, macOS desktop notifications on trap fire, `ans-tool audit week` CLI command.

**Tests required:** `tests/runaway/` verifies each trap actually fires.

## Two-stage pipeline detail

### Stage 1 — Research & Briefing

**Job states:** `created → researching → briefing_ready → user_editing → regenerating_section → approved → generating_documents → complete | failed`

Persisted in `jobs/{job_id}/state.json`. Frontend polls and renders appropriate UI per state.

**Agents (in order):**

1. **Intake** — validates company name + URL, normalises, ensures logo uploaded.
2. **Research agent** — uses Anthropic `web_search` tool, 15-20 queries. Priorities for ≥1000-staff enterprises:
   - Job postings (LinkedIn, careers pages, aggregators) — heaviest weight
   - Annual reports, investor materials, transformation programmes
   - Sector regulatory pressure (DORA, NIS2, PCI-DSS, HIPAA, FedRAMP)
   - Vendor footprint (case studies on vendor sites, conference talks)
   - M&A activity and integration projects
   - Public network incidents in last 24 months
   - Recent senior infrastructure hires (entry-point intelligence)

   Output: structured JSON dossier to `jobs/{job_id}/research_dossier.json`.

3. **Contact extraction agent** — reads research dossier, emits structured contacts written directly to database (not just briefing). Rules in system prompt:
   - Only business contexts, only network/infra/IT-exec/security-exec/CTO/CIO functions
   - No personal email domains (gmail/hotmail/yahoo)
   - No contacts under 21
   - Mark confidence: high/medium/low/inferred
   - Source URL and retrieval date required per record

4. **Needs inference agent** — no tools, reasons over dossier. Produces explicit **Lab maturity hypothesis**: `none_visible | basic | mature | modernisation_in_progress` with evidence pointers. Drives stage 2 writer priorities.

5. **Briefing compiler** — produces structured `briefing.json` with six sections:
   - Snapshot (company info, lab maturity, why interesting to ANS)
   - Business context (news, M&A, financials, regulatory, risks)
   - IT & network landscape (tech stack, architecture, vendors, transformation programmes, lab maturity reasoning, team shape, tooling)
   - Key people (named contacts, hiring signals)
   - ANS opportunity hypothesis (needs ranked, evidence, suggested products, buying cycle stage, entry angle, watch-outs)
   - Source register (every URL with retrieval date, confidence flags, "gaps" array)

   Each claim has confidence: `high | medium | low | inferred`.

6. **Briefing PDF render** — WeasyPrint from Jinja2 template with watermarks.

### Human approval gate (browser)

The most important page in the app. Renders briefing with:

- Segmented-control tabs across sections (Snapshot, Business, IT, People, Opportunity, Sources)
- Inline contenteditable text fields, debounced PATCH to `/api/briefing/{job_id}`
- "Contacts" tab showing extracted contacts; user can edit, delete, toggle DNC, mark for inclusion
- "Add context" textarea persisted to `briefing.user_context`
- Per-section "Regenerate" button → focused re-research with user's hint
- Confidence badges next to claims
- Cost estimate panel: per-agent breakdown, total, remaining budgets
- Depth selector: Quick / Standard / Deep
- Three actions: "Approve & Generate" / "Approve with modifications" / "Generate locally only" (the latter being a no-op placeholder in cloud-only build)
- "Download briefing PDF" available throughout

Stage 2 reads from the *approved* `briefing.json` (post-edit), not from raw research dossier.

### Stage 2 — Document Generation (only after approval)

7. **Product mapping agent** — reads approved briefing + ANS knowledge base. Produces need→product mappings with concrete use-case framing. Pre-filters which knowledge base excerpts each writer needs.

8. **Three writers in parallel** (`asyncio.gather`):
   - **Benefits writer** — tiered structure: Executive summary (1 page, CTO/CIO, business outcomes), Technical fit (2 pages, engineers, specs/integration), Business case (1 page, IT Director, ROI/risk). Receives dossier + briefing + needs + mapping + relevant ANS knowledge.
   - **FAQ writer** — 12-15 Q&A pairs grouped: About ANS, Products, Implementation, Commercial, Support.
   - **Objections writer** — 15-20 rows for XLSX. Columns: Category | Objection | Underlying concern | Response | Supporting evidence | Escalation path.

   Critical prompt instruction: if research/knowledge is thin on a topic, state so explicitly. Never fabricate.

9. **Critic agent** (Haiku 4.5) — reviews all three drafts against approved briefing + ANS knowledge. Flags:
   - Claims about target company not supported by briefing
   - Claims about ANS products not in knowledge base
   - Factual inconsistencies
   - Off-brand tone

   Max one revision pass (Trap 6).

10. **Assembly** — deterministic Python, no LLM. WeasyPrint→PDFs, openpyxl→XLSX. All documents watermarked.

## Watermarking (mandatory)

**PDFs (briefing, benefits, FAQ):**
- Every page footer via WeasyPrint CSS: `"INTERNAL DRAFT — {company} — {date} — Not for external distribution"` (8pt, grey #666)
- Diagonal background watermark: "DRAFT" repeating, low opacity, rotated -45deg, behind content

**Objections XLSX:**
- Row 1 merged across columns: red fill (#C00000), white bold text "INTERNAL DRAFT — Not for external distribution"
- Headers in row 2
- File-level "Status" property set to "Draft"

## M365 delivery

**Module:** `backend/delivery/m365.py`

**Auth:** Delegated permissions via MSAL. Credentials entered in admin UI (client_id, client_secret, tenant_id), stored in macOS Keychain. Required scopes: `Mail.Send`, `User.Read`. Refresh tokens persisted in Keychain. "Test M365" button in admin performs `/me` call to verify before any real send.

**Recipient locking (the critical safety check):**
- Cache authenticated user UPN at session start via `GET /me`
- Build email with `recipient = self.upn` only
- Re-validate immediately before `POST /me/sendMail` (defence in depth: validated twice from same cached UPN)
- Capitalisation normalised before comparison
- `ExternalRecipientBlocked` exception on any other address
- No config option, CLI flag, or env variable can override
- Test suite must specifically attempt to trick the lock

**Email content:**

```
Subject: [ANS Prospect Draft] {Company Name} — review ready

Body includes:
- Target, sector, headcount, generated timestamp, job ID
- Briefing summary (one-liner, lab maturity, recommended angle, confidence)
- Attachments list
- Cost actual + remaining budget
- Trap triggers (if any)
- Critic flags (if any)
- Internal-draft warning footer

Attachments: all four files
```

**Failure handling:**
- 401/403: refresh token; if still failing, prompt re-auth via UI banner
- Attachments >25MB total: split into two emails with part-1-of-2 / part-2-of-2
- Network failure: 3 retries with exponential backoff, then mark `delivery_failed`, keep files available via browser download
- Recipient lock violation: log to `incidents.jsonl` as high-severity, no retry

**No alternative delivery paths.** No SMTP, no raw mail composition, no CC/BCC, no arbitrary recipient field. Anywhere.

## Authentication

**First-run setup wizard:**
- Route `/setup`, triggered when `settings.setup_completed_at IS NULL`, 404 after completion
- Fields: username (3-32 chars), password (min 12 chars, mixed character classes), confirm password, recovery email
- Password hashed with argon2id (argon2-cffi default params)
- Returns 404 forever after setup

**Login (`/login`):**
- Username + password fields, eye-toggle on password
- Eye toggle: vanilla HTML/CSS/JS using Phosphor `eye` / `eye-slash` icons, accessible (`aria-pressed`, `aria-label` updates)
- Session cookie: HttpOnly, SameSite=Strict, Secure (except localhost), 4-hour expiry with sliding refresh
- Rate limit: 5 failures/IP/15min → 15min lockout
- Login attempts logged (IP, user-agent, no passwords ever)

**Forgot password (`/forgot`):**
- Single username field
- Response identical whether username exists (with 200-500ms random delay on miss path)
- If exists: 32-byte urlsafe token, SHA-256 hash stored in `password_resets` table, 30min expiry, email via M365 to recovery email
- Reset link `/reset?token=...`: validates by hash lookup, not expired, not used
- New-password form (two fields with eye-toggle), updates hash, marks `used_at`, invalidates all sessions
- Tokens single-use even within validity window
- Rate limit: 3 reset emails/hour/username
- Tokens never logged after creation

**Password change in admin:** Requires current password + new password (everyday path; forgot-password is for actually-forgotten cases).

**Settings keys:** `auth.username`, `auth.password_hash`, `auth.recovery_email`, `auth.setup_completed_at`.

**New tables:** `password_resets`, `login_attempts`, `sessions`.

## Database schema

Location: `~/.ans-tool/data.db`. ORM: SQLAlchemy with Alembic migrations. CLI command: `ans-tool db upgrade`.

```sql
CREATE TABLE companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    website_url TEXT,
    sector TEXT,
    sub_sector TEXT,
    headcount INTEGER,
    headcount_source TEXT,
    headcount_as_of DATE,
    revenue_band TEXT,
    hq_country TEXT,
    hq_city TEXT,
    ownership TEXT,
    parent_company TEXT,
    one_line_desc TEXT,
    lab_maturity TEXT,
    first_researched_at TIMESTAMP NOT NULL,
    last_researched_at TIMESTAMP NOT NULL,
    research_count INTEGER DEFAULT 1,
    last_job_id TEXT,
    notes TEXT,
    UNIQUE(name, website_url)
);
CREATE INDEX idx_companies_name ON companies(name COLLATE NOCASE);
CREATE INDEX idx_companies_last_researched ON companies(last_researched_at DESC);

CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    job_title TEXT,
    country TEXT,
    linkedin_url TEXT,
    email TEXT,
    phone TEXT,
    mobile TEXT,
    seniority TEXT,
    function TEXT,
    source TEXT,
    confidence TEXT,
    notes TEXT,
    do_not_contact BOOLEAN DEFAULT FALSE,
    dnc_set_at TIMESTAMP,
    dnc_reason TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE(company_id, name, job_title)
);
CREATE INDEX idx_contacts_company ON contacts(company_id);
CREATE INDEX idx_contacts_name ON contacts(name COLLATE NOCASE);
CREATE INDEX idx_contacts_linkedin ON contacts(linkedin_url) WHERE linkedin_url IS NOT NULL;

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    company_id INTEGER REFERENCES companies(id),
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT NOT NULL,
    depth TEXT,
    cost_usd REAL DEFAULT 0,
    trap_triggers TEXT,
    delivery_status TEXT,
    delivered_at TIMESTAMP,
    folder_path TEXT
);
CREATE INDEX idx_jobs_company ON jobs(company_id);
CREATE INDEX idx_jobs_started ON jobs(started_at DESC);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    created_ip TEXT
);
CREATE INDEX idx_password_resets_token ON password_resets(token_hash);
CREATE INDEX idx_password_resets_expires ON password_resets(expires_at);

CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    ip TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    attempted_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_login_attempts_ip_time ON login_attempts(ip, attempted_at);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    last_activity TIMESTAMP NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX idx_sessions_username ON sessions(username);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
```

## Compliance features (GDPR / UK GDPR)

- `do_not_contact` flag excludes contacts from all generated documents
- DNC contacts visible in admin/compliance view, can be reactivated
- `ans-tool delete-contact <id>` CLI (audit-logged)
- `ans-tool delete-company <id>` CLI (cascade, confirmation required, audit-logged)
- `ans-tool export-person <name>` CLI dumps everything held about a named individual (for SAR responses)
- Source attribution required on every contact (enforced at agent level)
- Lawful basis: legitimate interest for B2B prospecting; data is business-context only

## Admin section

Route `/admin`, protected by login session (4-hour expiry). Sub-pages:

- **/admin/credentials** — Anthropic API key, M365 client_id/client_secret/tenant_id (write-only fields, masked when present, test buttons that perform real calls). All secrets go to Keychain regardless of how entered.
- **/admin/budgets** — edit caps (per-job, daily soft/hard, monthly hard), view current spend
- **/admin/appearance** — theme (Light/Dark/System), density (Default/Compact), HDR accents toggle
- **/admin/knowledge** — list ANS knowledge files with last-modified, upload/replace each, validation status
- **/admin/database** — counts, export companies/contacts to CSV, backup SQLite file (download)
- **/admin/audit** — recent jobs with cost/status, trap triggers in last 30 days, link to download full audit log
- **/admin/compliance** — search contacts, toggle DNC, delete records
- **/admin/logs** — last 100 lines of app log, filterable by level
- **/admin/change-password** — current + new password (eye-toggle on both)

## Logo upload pipeline

**Module:** `backend/tools/logo_processor.py`

**Accepted formats:** PNG, JPG, JPEG, SVG, WebP, GIF, BMP, TIFF, PDF, EPS, AI.

**Processing stages:**
1. Format detection
2. Format-specific decoder: Pillow / cairosvg / pdf2image / Ghostscript subprocess
3. RGBA normalisation
4. Whitespace trim via alpha bbox
5. Resolution check (warn if either axis <150px)
6. Resize to two variants preserving aspect: header 120px, large 300px
7. Save to `jobs/{job_id}/logo_header.png` and `logo_large.png`

**Endpoint:** `POST /api/upload-logo` accepts multipart, returns job_id and preview URL.

**Startup probe:** Checks for Ghostscript and Poppler binaries. If missing, disables corresponding formats in frontend file picker and logs warning. Rejects files >20MB raw or <50×50px with useful error messages.

## ANS knowledge base structure

Read at startup via `backend/tools/knowledge_loader.py`. Missing files trigger "degraded mode" banner on frontend listing what's missing.

```
ans_knowledge/
├── products/
│   ├── emulators.md           # Models, specs, use cases, differentiators
│   └── traffic_generators.md  # Same
├── company.md                 # ANS history, size, customers, certifications
├── case_studies.md            # 3-5 anonymised wins with metrics
├── competitive_landscape.md   # vs Spirent, Keysight, Ixia — honest pros/cons
├── pricing_tiers.md           # Ballpark ranges, what's negotiable
├── known_objections.md        # Real objections from sales team
└── brand/
    ├── ans_logo.png
    └── brand.json             # primary_colour, secondary_colour, font
```

The agents are only as good as this content. README must emphasise: spend an afternoon populating these before testing seriously. Provides `make seed-knowledge` or python script to create template files for the user to fill in.

## UI design — macOS HIG with M4 optimisation

**Design tokens** (`frontend/styles/tokens.css`):

- **Typography:** SF Pro system font stack. HIG type scale: large-title (26/32, 700), title-1 (22/28, 700), title-2 (17/22, 600), title-3 (15/20, 600), headline (13/16, 600), body (13/16, 400), callout (12/15, 400), subhead (11/14, 400), footnote (10/13, 400). Letter-spacing tightens at larger sizes.
- **Spacing:** 4pt grid — `--space-1` through `--space-10` (4, 8, 12, 16, 20, 24, 32, 40).
- **Radii:** small 4px, medium 6px, large 10px, xl 14px.
- **Semantic colours:** `--label-primary/secondary/tertiary/quaternary`, `--fill-primary/secondary/tertiary/quaternary`, `--bg-primary/secondary/tertiary`, `--separator-opaque/non-opaque`. Adapts via `prefers-color-scheme` overridden by user setting.
- **P3 accent:** `--accent: #007aff` fallback, `--accent: color(display-p3 0 0.478 1)` enhanced. ANS brand colour converted to P3 at startup.
- **Motion:** `--ease-standard` cubic-bezier(0.25, 0.1, 0.25, 1.0), `--ease-emphasised`, `--ease-decelerated`, `--ease-accelerated`. Durations 100/200/300/400/600ms.

**Glass material system** — six materials with light/dark variants:
- ultra-thin (popovers): blur(20px) saturate(130%)
- thin (light overlays): blur(30px) saturate(150%)
- regular (sidebar): blur(40px) saturate(180%)
- thick (sheets): blur(50px) saturate(200%)
- ultra-thick (heavy): blur(60px) saturate(200%)
- chrome (toolbars): variant

Glass requires textured content beneath. Document background uses subtle gradient / accent-tinted texture as Layer 0 "wallpaper" so glass surfaces have something to show through.

**Layer architecture:**
- Layer 0: document background (wallpaper)
- Layer 1: content area (opaque/near-opaque)
- Layer 2: sidebar (regular material)
- Layer 3: toolbar (regular material; blur visible when content scrolls)
- Layer 4: sheets/inspectors (thick material)
- Layer 5: popovers (ultra-thin material)

**Components** (`frontend/styles/components.css`):
- Buttons: prominent (filled accent, semibold), bordered, plain, destructive. Default 28px height, prominent 32px, 16-20px horizontal padding.
- Text fields: filled style, 3px accent focus ring with 1px offset, 28px height.
- Password field: text field + trailing eye-toggle (Phosphor eye/eye-slash icons, aria-pressed updates). Jinja partial component reused on setup/login/reset/admin-change-password.
- Toggle switch: HIG capsule 51×31px, accent-fill when on.
- Segmented control: pill container, selected segment elevated and accent-tinted.
- List rows: 44px min height, leading icon optional, title + secondary text, trailing accessory, hairline separator between rows only.
- Form rows: leading right-aligned label, trailing input takes remaining width. Group has rounded-corner background fill.
- Sheets: 14px radius, slides up from bottom, toolbar with Cancel/Done.
- Progress: determinate 4px bar, indeterminate 8-segment radial fade spinner.

**Icons:** Phosphor Icons "regular" weight, 17pt body / 22pt toolbar. NOT SF Symbols (Apple licensing prohibits web use).

**M4-specific optimisations:**
- 0.5px hairlines (clean at 254 PPI)
- 3x raster assets baseline (3024px width)
- Avoid `#ffffff` for backgrounds (use `#fafafa`/`#f5f5f7` for True Tone)
- `color-scheme: light dark` in CSS
- Glass works with backdrop-filter (M4 has plentiful GPU)
- All animations target `transform` and `opacity` only (120Hz capable with ProMotion)
- `will-change` applied only during active animation, removed after
- Max 5 simultaneous backdrop-filter surfaces

**Specific motion patterns:**
- Sheet present: translateY 100→0 + opacity 0→1, 400ms decelerated
- Sheet dismiss: reverse, 300ms accelerated
- Button press: filter brightness 1→0.92, 100ms
- Page transition: opacity 0→1 + translateX 8px→0, 300ms decelerated
- Sidebar selection: bg opacity 0→1, 200ms standard

`prefers-reduced-motion: reduce` removes transitions except opacity at half-duration.

**Responsive layout (MBP form factor):**
- <1280px: sidebar collapses to 60px icon-only
- 1280-1600px (14" default): sidebar 240px
- 1600px+ (16" default): sidebar 280px, content max-width 1200px centred
- On 16": briefing source register as permanent right-hand inspector (300px); on 14" remains a tab

**Density modes (admin/appearance):**
- Default: HIG standard
- Compact: 90% scale (25px buttons, 40px rows, 12pt body)

Setting in DB and localStorage.

**Trackpad gestures (v1):**
- Two-finger swipe left/right on briefing tabs → switch sections
- Two-finger swipe right in main content → go back

**Keyboard shortcuts (v1):**
- Cmd+N: new research job
- Cmd+,: admin/settings
- Cmd+F: focus current-screen search
- Cmd+1 to Cmd+6: switch briefing review sections
- Cmd+[ / Cmd+]: back/forward
- Esc: close sheet/popover
- Cmd+/: show shortcuts overlay

**Screens to build:**
1. `/setup` — first-run wizard, sheet-style centred form
2. `/login` — centred ~360px card, eye-toggle
3. `/forgot` — single username field
4. `/reset` — sheet-style new-password form
5. `/` — home/sidebar + content layout
6. `/jobs/new` — form-style entry with logo drop-zone (HIG "well" component)
7. `/jobs/{id}/researching` — progress with stage list
8. `/jobs/{id}/briefing` — segmented-control tabs, inline editing, contacts list, approval gate as inspector panel
9. `/jobs/{id}/generating` — progress
10. `/jobs/{id}/complete` — completion state, download links, delivery receipt
11. `/jobs` — history list with search
12. `/contacts` — list with filter and search
13. `/admin/*` — System Settings pattern with secondary sidebar

**Quality bar:** "Would not look out of place alongside Apple's own apps on macOS Sequoia." Hairlines not borders, translucent materials not solid panels, semantic colours not hex values, 4pt grid not arbitrary pixels, leading-label forms not above-input forms, Apple's exact type scale, brightness-shift active states not transform tricks, 3px focus rings not browser defaults.

## Cost control configuration

`config.yaml` at project root:

```yaml
budgets:
  per_job_usd: 2.00
  per_job_max_via_ui_usd: 10.00
  daily_soft_usd: 10.00
  daily_hard_usd: 25.00
  monthly_hard_usd: 200.00

models:
  writer_model: claude-sonnet-4-5
  research_model: claude-sonnet-4-5
  critic_model: claude-haiku-4-5

keychain:
  service: ans-prospect-tool
  accounts:
    anthropic_api_key: anthropic-api-key
    m365_client_id: m365-client-id
    m365_client_secret: m365-client-secret
    m365_tenant_id: m365-tenant-id
    m365_refresh_token: m365-refresh-token

concurrency:
  max_concurrent_jobs: 3
```

## Audit logging

`~/.ans-tool/audit/audit-YYYY-MM.jsonl` — append-only, never edited/deleted by app.

Schema per entry:

```json
{
  "ts": "ISO timestamp",
  "job_id": "uuid",
  "agent": "agent_name",
  "model": "claude-sonnet-4-5",
  "input_tokens": 4823,
  "output_tokens": 1109,
  "cost_usd": 0.0193,
  "prompt_hash": "sha256:...",
  "approved_by": "user",
  "budget_remaining_job": 0.61,
  "budget_remaining_day": 4.05
}
```

Full prompts and outputs stored for debugging (named individuals scrubbed before logging).

CLI: `ans-tool audit week` summarises spend, traps triggered, near-misses; exports CSV.

`~/.ans-tool/incidents.jsonl` — every trap trigger with full context and macOS desktop notification.

## Build order recommendation

1. Directory scaffold and `requirements.txt`
2. Database models and Alembic initial migration
3. Auth (setup wizard, login, sessions) — get this working before anything else needs protection
4. Admin section shell + credentials page + Keychain integration
5. Cost control module + tests (Traps 1-7) with stubbed Claude client
6. Agent base wrapper + research agent (smallest path to seeing real output)
7. Remaining stage 1 agents (contact extraction, needs, briefing compiler)
8. Briefing PDF builder + briefing review UI
9. Approval gate UI + state machine
10. Stage 2 agents (mapping, writers, critic)
11. Document assembly (PDF templates + XLSX builder + watermarking)
12. Logo processing pipeline
13. M365 delivery + recipient-locking tests
14. Forgotten-password flow (depends on M365 working)
15. HIG design system (tokens, components)
16. All screens implemented
17. M4-specific polish (P3, ProMotion motion, glass layers, gestures, shortcuts)
18. End-to-end test on three real companies

## Things to deliberately NOT build

- Local LLMs / hybrid mode (discussed, rejected)
- Network TAPs or packet brokers (out of product scope)
- SMTP delivery (deprecated by Microsoft)
- Multiple user accounts (single-user tool)
- TOTP/2FA (not v1; password + rate limiting sufficient for localhost)
- Password complexity score meters
- "Remember me" checkbox
- Security questions for recovery
- SF Symbols icons (Apple licensing)
- Arbitrary recipient field for delivery (would defeat recipient-lock)
- Localstorage/sessionstorage for sensitive data
- Any source-code-embedded credentials

## What to do in the new Claude project

1. Paste this entire document as the project context / first message
2. Confirm Claude has parsed it correctly by asking it to enumerate the seven runaway traps
3. Then ask: "Now produce the consolidated final Claude Code build prompt — single ready-to-paste document organising every requirement from the handover into clean sections so Claude Code can execute methodically. Target 4000-5000 words. Structure it with clear section headers, code examples where they aid precision, and an explicit build-order checklist at the end."
4. Review the produced prompt, then paste it into Claude Code in an empty project directory

The new Claude doesn't have our conversation history but with this handover document has every decision and constraint we settled on.
