# ANS Prospect Tool — Claude Code Build Prompt

> **How to use this document:** Place it in the project root alongside `ans-prospect-tool-handover.md`. The handover document is the source of truth for *why* every decision was made; this document is the source of truth for *what to build and in what order*. Where the two disagree, the handover wins. Begin with section "Build order" at the end and execute each step as a discrete task, reading the relevant detail sections below as needed.

---

## 1. Mission

Build a single-user, locally hosted web application that researches enterprise prospects (≥1000 staff) and produces co-branded internal sales drafts for Avizent Network Solutions (ANS) network emulators and traffic generators. The application runs on a MacBook Pro M4, is accessed only through the user's own browser, and delivers all outputs as Microsoft 365 email drafts to the authenticated user's own mailbox. Nothing leaves the user's mailbox automatically; all outputs are internal drafts.

**Quality bar:** Production-grade. The frontend "would not look out of place alongside Apple's own apps on macOS Sequoia." The backend is defensively engineered with seven independent cost-runaway traps and recipient-locked email delivery. No corners cut on security, cost control, or compliance.

---

## 2. Hard rules (non-negotiable)

These rules override any other consideration. If anything later in this document conflicts with them, the rules win.

1. **Cloud Claude API only.** No local LLMs, no fallback models, no hybrid mode.
2. **Single user account.** No multi-tenant code, no user table beyond the single `auth.*` settings keys.
3. **No source-embedded credentials, ever.** All secrets in macOS Keychain. The setup wizard creates credentials at first run.
4. **Recipient-locked delivery.** The M365 delivery module refuses to send anywhere except the authenticated user's own UPN. There is no recipient field in the UI or API.
5. **All seven runaway traps must be in place before any real Claude API call is made.** Stub the Claude client until traps and their tests pass.
6. **Network TAPs and packet brokers are out of scope.** Products covered: network emulators and traffic generators only.
7. **No SMTP.** M365 Graph API only.
8. **No browser localStorage or sessionStorage for sensitive data.** Sessions are server-side; cookies are HttpOnly.
9. **All generated documents are watermarked as "INTERNAL DRAFT".** PDFs and the XLSX.
10. **GDPR-compliant contact handling.** Source attribution on every contact, DNC flag honoured, deletion and SAR-export CLI commands exist.

---

## 3. Tech stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, Anthropic SDK (`anthropic>=0.40`), MSAL for M365, `keyring` for Keychain, `argon2-cffi` for password hashing.
- **Frontend:** Vanilla HTML, CSS, and JavaScript. No framework. No build step. Phosphor Icons (MIT-licensed SVGs in `frontend/icons/`).
- **Documents:** WeasyPrint (PDF from HTML/CSS), openpyxl (XLSX), Pillow + cairosvg + pdf2image (logo processing).
- **Local binaries required:** Ghostscript (EPS, AI, complex PDF logos), Poppler (PDF rasterisation via pdf2image). Probe both at app startup; if missing, disable the corresponding logo formats in the frontend picker.
- **Database:** SQLite at `~/.ans-tool/data.db`.
- **Models:** `claude-sonnet-4-5` for writers and research; `claude-haiku-4-5` for the critic. Configured in `config.yaml`, not hard-coded.

---

## 4. Project structure

Create this exact tree:

```
ans-prospect-tool/
├── README.md
├── .env.example
├── requirements.txt
├── config.yaml
├── ans_knowledge/
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
│       └── brand.json
├── backend/
│   ├── main.py
│   ├── orchestrator.py
│   ├── agents/
│   │   ├── base.py
│   │   ├── limits.py
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
│   ├── tools/
│   │   ├── logo_processor.py
│   │   └── knowledge_loader.py
│   ├── cost_control/
│   │   ├── job_budget.py
│   │   ├── budget_state.py
│   │   ├── audit.py
│   │   ├── pricing.py
│   │   ├── approval.py
│   │   └── cloud_client.py
│   ├── delivery/
│   │   └── m365.py
│   ├── auth/
│   │   ├── routes.py
│   │   ├── sessions.py
│   │   └── password.py
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── migrations/
│   └── assembly/
│       ├── pdf_builder.py
│       ├── xlsx_builder.py
│       └── templates/
│           ├── briefing.html
│           ├── benefits.html
│           └── faq.html
├── frontend/
│   ├── index.html
│   ├── styles/
│   │   ├── tokens.css
│   │   ├── components.css
│   │   └── screens/
│   ├── js/
│   │   ├── app.js
│   │   ├── api.js
│   │   ├── auth.js
│   │   ├── gestures.js
│   │   └── shortcuts.js
│   └── icons/
├── jobs/
└── tests/
    ├── runaway/
    ├── delivery/
    ├── auth/
    └── agents/
```

`ans_knowledge/` files are user-supplied; create them as empty placeholders with brief comments describing expected content. Stub `brand.json` with `{ "primary": "#0066CC", "secondary": "#333333", "font": "Inter" }` and document that the user will overwrite.

---

## 5. Configuration files

**`config.yaml`** (project root):

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

**`.env.example`** contains no secrets. It lists only non-sensitive runtime toggles (e.g. `APP_HOST=127.0.0.1`, `APP_PORT=8765`, `LOG_LEVEL=INFO`). Document in the file's header comment that all secrets live in Keychain, not the environment.

**`requirements.txt`** pins major versions of: `fastapi`, `uvicorn[standard]`, `anthropic>=0.40`, `sqlalchemy>=2.0`, `alembic`, `pydantic>=2`, `python-multipart`, `keyring`, `argon2-cffi`, `msal`, `weasyprint`, `openpyxl`, `pillow`, `cairosvg`, `pdf2image`, `pyyaml`, `python-dateutil`, `pytest`, `pytest-asyncio`, `httpx`.

---

## 6. Database schema

Database lives at `~/.ans-tool/data.db`. Create with Alembic. Initial migration must produce exactly these tables and indexes:

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

CLI: `ans-tool db upgrade` runs Alembic. Provide this as a click/typer entry point in `pyproject.toml` or a simple script.

---

## 7. Authentication

### 7.1 First-run setup wizard

- Route: `/setup`. Triggered when `settings.setup_completed_at IS NULL`. Returns 404 forever once set.
- Fields: `username` (3–32 chars), `password` (min 12 chars, mixed character classes), `confirm_password`, `recovery_email`.
- Password hashed with argon2id using `argon2-cffi` defaults.
- On success, writes settings keys `auth.username`, `auth.password_hash`, `auth.recovery_email`, `auth.setup_completed_at`.

### 7.2 Login

- Route: `/login`. Username + password. Eye-toggle on the password field using Phosphor `eye` and `eye-slash` icons. The toggle has `aria-pressed` and `aria-label` that update.
- Session cookie: HttpOnly, SameSite=Strict, Secure (except on localhost). 4-hour expiry with sliding refresh.
- Rate limit: 5 failures per IP per 15 minutes triggers a 15-minute lockout. Track in `login_attempts` table.
- Never log passwords. Log IP and user-agent only.

### 7.3 Forgot / reset password

- `/forgot`: single username field. Response timing identical whether username exists or not (insert a 200–500ms random delay on the miss path).
- If username exists: generate a 32-byte urlsafe token, store SHA-256 hash in `password_resets`, 30-minute expiry, send link via M365 Graph API to the recovery email.
- `/reset?token=...`: validates by hash lookup. Token must not be expired and not used. New password form has two fields with eye-toggle on both. Successful reset updates password hash, marks `used_at`, and invalidates all existing sessions for the user.
- Tokens are single-use even within validity window.
- Rate limit: 3 reset emails per hour per username.
- Tokens never logged after creation.

### 7.4 Change password (admin)

`/admin/change-password` requires current password + new password (eye-toggle on both). This is the everyday rotation path; forgot-password is reserved for actual lockout.

---

## 8. The seven runaway traps

All seven must be present and tested in `tests/runaway/` before any real Claude call is made. Each must fail loudly, log to `~/.ans-tool/incidents.jsonl`, and trigger a macOS desktop notification.

**Trap 1 — Per-call `max_tokens` ceiling.** `backend/agents/limits.py` exports a dict with these exact values: research 4000, needs 3000, briefing 8000, mapping 2000, benefits 6000, faq 4000, objections 4000, critic 2000. The shared client wrapper in `agents/base.py` requires the per-agent value on every call. No default fallback.

**Trap 2 — Tool-call ceilings.** Research agent: web_search ≤ 20, web_fetch ≤ 10. All other agents: ≤ 3 tool calls total. Exceeding raises `ToolCallLimitExceeded`. The job fails immediately. No retry.

**Trap 3 — Per-job budget guard.** `JobBudgetGuard` wraps every Claude call and computes a pre-call cost estimate from input tokens and the per-agent `max_tokens`. If `estimate + job.cost_so_far > job.cost_cap`, the call is refused. Default cap $2; max via UI $10; above $10 requires editing `config.yaml`.

**Trap 4 — Daily / monthly caps.** `backend/cost_control/budget_state.py` maintains `~/.ans-tool/budget_state.json` with atomic writes guarded by `fcntl` locking. Daily soft $10 (warn in UI, allow proceed). Daily hard $25 (block, override only with `--force` flag from CLI). Monthly hard $200 (block, override requires config edit).

**Trap 5 — Wall-clock timeouts.** Per-API-call timeout 90s. Per-agent timeout 5 minutes. Per-job timeout 20 minutes. The per-job timeout is a separate `asyncio` task that cancels the orchestrator.

**Trap 6 — Critic-revise loop bound.** Maximum one revision pass. Flow: Writer v1 → Critic → if issues → Writer v2 → Critic → ship v2 regardless. If v2 still rejected, ship with a warning flag in the job record and show a banner in the completion UI.

**Trap 7 — Concurrent job limit.** Semaphore in `main.py` caps active jobs at 3. Excess jobs queue with status visible in the jobs list. Queue persists in SQLite via the `jobs.status` field.

**Visibility:** Live spend panel in the UI polls every 2 seconds. `~/.ans-tool/incidents.jsonl` is append-only and never modified after write. `ans-tool audit week` CLI summarises spend and trap fires.

---

## 9. Two-stage pipeline

### 9.1 Job state machine

`created → researching → briefing_ready → user_editing → regenerating_section → approved → generating_documents → complete | failed`

State persisted in `jobs/{job_id}/state.json` and mirrored in `jobs.status`. Frontend polls `/api/jobs/{id}/state` and renders per state.

### 9.2 Stage 1 — Research and briefing

1. **Intake** — validates company name + URL, normalises (strip protocol from URL, trim whitespace), ensures the logo upload completed.
2. **Research agent** (Sonnet, with `web_search` and `web_fetch` tools). 15–20 queries. Priority targets, in order: job postings, annual reports / investor materials, sector regulatory pressure (DORA, NIS2, PCI-DSS, HIPAA, FedRAMP), vendor footprint (case studies and conference talks), M&A activity, public network incidents in the last 24 months, recent senior infrastructure hires. Output: structured JSON dossier at `jobs/{job_id}/research_dossier.json`.
3. **Contact extraction agent** (Sonnet, no web tools). Reads dossier, writes contacts directly to the `contacts` table. Constraints baked into the system prompt: business contexts only; only network/infra/IT-exec/security-exec/CTO/CIO functions; no personal email domains (gmail, hotmail, yahoo); no contacts under 21; mark confidence as `high | medium | low | inferred`; source URL and retrieval date required per record.
4. **Needs inference agent** (Sonnet, no tools). Reasons over the dossier. Must produce an explicit `lab_maturity` value of `none_visible | basic | mature | modernisation_in_progress` with evidence pointers. This value drives Stage 2 writer priorities.
5. **Briefing compiler** (Sonnet). Produces `jobs/{job_id}/briefing.json` with six sections:
   - **Snapshot** — company info, lab maturity, why interesting to ANS.
   - **Business context** — news, M&A, financials, regulatory, risks.
   - **IT & network landscape** — tech stack, architecture, vendors, transformation programmes, lab maturity reasoning, team shape, tooling.
   - **Key people** — named contacts, hiring signals.
   - **ANS opportunity hypothesis** — needs ranked, evidence, suggested products, buying-cycle stage, entry angle, watch-outs.
   - **Source register** — every URL with retrieval date, confidence flags, and a `gaps` array listing what was searched for but not found.

   Every claim has `confidence: high | medium | low | inferred`.
6. **Briefing PDF render** — WeasyPrint from a Jinja2 template, watermarked.

### 9.3 Human approval gate

The most important screen in the app. Route `/jobs/{id}/briefing`. Renders the briefing with:

- Segmented-control tabs across sections (Snapshot, Business, IT, People, Opportunity, Sources).
- Inline `contenteditable` fields with debounced PATCH to `/api/briefing/{job_id}`.
- "Contacts" tab listing extracted contacts. User can edit, delete, toggle DNC, or mark for inclusion.
- "Add context" textarea persisted to `briefing.user_context`.
- Per-section "Regenerate" button that triggers focused re-research with the user's hint.
- Confidence badges next to claims.
- Cost estimate panel: per-agent breakdown, total, remaining budgets.
- Depth selector: Quick / Standard / Deep.
- Three actions: "Approve & Generate", "Approve with modifications", and "Generate locally only" (the latter is a no-op placeholder in this cloud-only build but stays in the UI for parity with future builds).
- "Download briefing PDF" available throughout.

Stage 2 reads from the *approved* `briefing.json` (post-edit), never from the raw research dossier.

### 9.4 Stage 2 — Document generation (only after approval)

7. **Product mapping agent** (Sonnet). Reads approved briefing + ANS knowledge base. Produces need-to-product mappings with concrete use-case framing. Pre-filters which knowledge base excerpts each writer needs.
8. **Three writers in parallel** via `asyncio.gather`:
   - **Benefits writer** (Sonnet). Tiered structure: Executive summary (1 page, CTO/CIO audience, business outcomes), Technical fit (2 pages, engineers, specs and integration), Business case (1 page, IT Director, ROI and risk). Receives dossier + briefing + needs + mapping + relevant ANS knowledge.
   - **FAQ writer** (Sonnet). 12–15 Q&A pairs grouped under: About ANS, Products, Implementation, Commercial, Support.
   - **Objections writer** (Sonnet). 15–20 rows for the XLSX. Columns: Category | Objection | Underlying concern | Response | Supporting evidence | Escalation path.

   **Critical instruction in every writer prompt:** if the research or knowledge base is thin on a topic, state that explicitly in the output. Never fabricate.

9. **Critic agent** (Haiku 4.5). Reviews all three drafts against approved briefing + ANS knowledge. Flags: claims about the target company not supported by the briefing; claims about ANS products not in the knowledge base; factual inconsistencies; off-brand tone. Triggers at most one revision pass (Trap 6).
10. **Assembly** — deterministic Python, no LLM. WeasyPrint produces the PDFs; openpyxl produces the XLSX. All outputs watermarked.

---

## 10. Document assembly and watermarking

**PDFs (briefing, benefits, FAQ)** rendered with WeasyPrint from Jinja2 templates in `backend/assembly/templates/`. Templates are *light-themed regardless of the app theme*. Every page has:
- Footer via CSS `@page` rules: `"INTERNAL DRAFT — {company} — {date} — Not for external distribution"` in 8pt grey `#666`.
- Diagonal background watermark of repeated "DRAFT" text, low opacity, rotated -45 degrees, behind content.

**Objections XLSX** built with openpyxl. Row 1 is merged across all columns, red fill `#C00000`, white bold text "INTERNAL DRAFT — Not for external distribution". Headers occupy row 2. The file-level Status property is set to "Draft" via `workbook.properties`.

**Co-branding:** Every document header includes the ANS logo (from `ans_knowledge/brand/ans_logo.png`) on the right and the target company logo (from `jobs/{job_id}/logo_header.png`) on the left. The benefits PDF cover uses the large logos (`logo_large.png`).

---

## 11. Logo upload pipeline

Module: `backend/tools/logo_processor.py`. Accepts PNG, JPG, JPEG, SVG, WebP, GIF, BMP, TIFF, PDF, EPS, AI.

Processing stages:
1. Format detection.
2. Format-specific decoder: Pillow for raster, cairosvg for SVG, pdf2image for PDF, Ghostscript subprocess for EPS and AI.
3. RGBA normalisation.
4. Whitespace trim via alpha bounding box.
5. Resolution check — warn if either axis < 150px.
6. Resize to two variants preserving aspect ratio: header 120px tall, large 300px tall.
7. Save to `jobs/{job_id}/logo_header.png` and `logo_large.png`.

Endpoint: `POST /api/upload-logo` accepts multipart, returns `{ "job_id": "...", "preview_url": "..." }`.

**Startup probe:** check that Ghostscript and Poppler binaries are on PATH. If missing, disable the corresponding formats in the frontend file picker and log a warning to the app log.

---

## 12. M365 delivery

Module: `backend/delivery/m365.py`.

**Auth:** Delegated permissions via MSAL. Required scopes: `Mail.Send`, `User.Read`. Credentials (client_id, client_secret, tenant_id, refresh_token) live in Keychain only. Admin UI has a "Test M365" button that performs `/me` to verify before any real send.

**Recipient lock — the critical invariant.** The send function takes no recipient parameter from the caller. It always resolves the authenticated user's UPN via the `/me` Graph endpoint and sends *only* to that address. The signature is `send_drafts(job_id: str, files: list[Path]) -> DeliveryReceipt`. There is no overload that accepts a different recipient. There is no CC, BCC, or arbitrary recipient field anywhere in the app.

**What gets sent:** four attachments (briefing PDF, benefits PDF, FAQ PDF, objections XLSX) plus a short body summarising the job and pointing to the local `jobs/{job_id}/` folder. The body must restate "INTERNAL DRAFT — Not for external distribution".

**Tests** in `tests/delivery/` must prove:
- `send_drafts` ignores any recipient passed in from the test (since there is no such parameter, the test verifies the surface area).
- A simulated `/me` returning a different UPN than the session's username does not cause cross-account delivery — the function trusts `/me` of the authenticated token.
- A failure in token refresh raises an error visible in the UI, not a silent fallback.

---

## 13. Audit logging

Append-only file `~/.ans-tool/audit/audit-YYYY-MM.jsonl`. Never edited or deleted by the app. One JSON object per line:

```json
{
  "ts": "2026-05-25T14:23:11Z",
  "job_id": "uuid",
  "agent": "research",
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

Full prompts and full outputs are stored in a sibling debug log for triage, with named individuals scrubbed before write.

`~/.ans-tool/incidents.jsonl` records every trap trigger with full context. macOS desktop notification fires on every incident.

CLI: `ans-tool audit week` summarises spend, traps triggered, and near-misses. Exports CSV.

---

## 14. GDPR / compliance features

- `contacts.do_not_contact` flag excludes the row from all generated documents.
- DNC contacts remain visible in `/admin/compliance` and can be reactivated.
- CLI `ans-tool delete-contact <id>` — audit-logged.
- CLI `ans-tool delete-company <id>` — cascades to contacts and jobs, requires confirmation, audit-logged.
- CLI `ans-tool export-person <name>` — dumps everything held about a named individual (for SAR responses).
- Source attribution enforced at the contact extraction agent level: a row with no source is rejected.
- Lawful basis recorded in `README.md`: legitimate interest for B2B prospecting; data is business-context only.

---

## 15. Admin section

All routes under `/admin`, protected by login session (4-hour expiry, sliding refresh). Sub-pages:

- `/admin/credentials` — Anthropic API key + M365 client_id/client_secret/tenant_id. Fields are write-only and masked when present. Test buttons perform real `/me` and a minimal Claude `messages.create` call. All secrets go to Keychain regardless of how they were entered.
- `/admin/budgets` — edit caps and view current spend.
- `/admin/appearance` — Theme (Light / Dark / System), Density (Default / Compact), HDR accents toggle.
- `/admin/knowledge` — list `ans_knowledge/` files with last-modified, upload/replace each, validation status.
- `/admin/database` — counts of companies/contacts/jobs, export companies and contacts to CSV, backup SQLite file (download).
- `/admin/audit` — recent jobs with cost/status, trap triggers in last 30 days, link to download full audit log.
- `/admin/compliance` — search contacts, toggle DNC, delete records.
- `/admin/logs` — last 100 lines of the app log, filterable by level.
- `/admin/change-password` — current + new password with eye-toggle on both.

---

## 16. Frontend — HIG quality bar

The frontend is vanilla HTML/CSS/JS organised around macOS Human Interface Guidelines and tuned for the MacBook Pro M4 (Liquid Retina XDR, P3 gamut, ProMotion 120Hz).

**Design tokens** (`frontend/styles/tokens.css`) include:
- Semantic colour variables (never hex literals in screen CSS).
- Apple's exact type scale.
- 4pt grid spacing tokens.
- Light/Dark theme via `prefers-color-scheme` and an explicit class override.
- HDR-aware accent colours when the HDR toggle is on.

**Components** (`frontend/styles/components.css`) provide:
- Sidebar with hairline divider (not a border).
- Translucent material panels (not solid).
- HIG "well" component for the logo drop-zone.
- Segmented controls.
- Leading-label form rows (label to the left of the input, not above).
- 3px focus rings (not browser default).
- Brightness-shift active states (not transform tricks).

**Interactions:**
- `gestures.js` implements trackpad-aware swipe/pinch handlers where appropriate.
- `shortcuts.js` registers Cmd-N (new job), Cmd-/ (focus search), Cmd-, (open admin), Cmd-K (command bar — future, stub for now).
- ProMotion: long animations use `prefers-reduced-motion` and avoid fractional millisecond easing that would betray 60fps assumptions.

**Quality bar reminder:** hairlines not borders, translucent materials not solid panels, semantic colours not hex values, 4pt grid not arbitrary pixels, leading-label forms not above-input forms, Apple's exact type scale, brightness-shift active states not transform tricks, 3px focus rings not browser defaults.

---

## 17. Screens (routes)

1. `/setup` — first-run wizard (404s after setup_completed_at).
2. `/login` — username + password, eye-toggle, lockout banner if rate-limited.
3. `/forgot` — single username field.
4. `/reset?token=...` — sheet-style new-password form.
5. `/` — home with sidebar + content layout.
6. `/jobs/new` — form-style entry with the logo drop-zone "well".
7. `/jobs/{id}/researching` — progress with stage list.
8. `/jobs/{id}/briefing` — segmented-control tabs, inline editing, contacts list, approval gate as an inspector panel.
9. `/jobs/{id}/generating` — progress.
10. `/jobs/{id}/complete` — completion state, download links, delivery receipt.
11. `/jobs` — history list with search.
12. `/contacts` — list with filter and search.
13. `/admin/*` — System Settings pattern with secondary sidebar.

---

## 18. CLI commands

Entry point: `ans-tool`. Provided via a console_scripts entry in `pyproject.toml` (or equivalent).

- `ans-tool db upgrade` — run Alembic migrations.
- `ans-tool audit week` — summarise spend / traps for the past 7 days. Exports CSV.
- `ans-tool delete-contact <id>` — GDPR delete, audit-logged.
- `ans-tool delete-company <id>` — cascade delete, confirmation required, audit-logged.
- `ans-tool export-person <name>` — SAR export.
- `ans-tool run --company "Name" --url "https://..." --logo path/to/logo.png` — headless run for testing; still goes through all traps and recipient-locked delivery.

---

## 19. Tests required

Minimum coverage before marking the build complete:

- **`tests/runaway/`** — one test per trap proving it fires under the conditions described in section 8. All seven must pass.
- **`tests/delivery/`** — recipient-lock invariants (section 12). Includes a test where a hypothetical mutation of `send_drafts` adding a recipient parameter would break the contract test.
- **`tests/auth/`** — argon2 round-trip, login rate limit, session expiry, password reset token single-use and expiry, timing-safe forgot-password response.
- **`tests/agents/`** — base wrapper enforces `max_tokens`, enforces tool-call ceilings, parses JSON correctly, surfaces partial errors.

All async tests use `pytest-asyncio`. All Claude calls in tests use a stubbed client; no test ever hits the real API.

---

## 20. Things to deliberately NOT build

- Local LLMs or any hybrid mode.
- Network TAPs or packet brokers.
- SMTP delivery.
- Multiple user accounts.
- TOTP / 2FA.
- Password complexity score meters.
- "Remember me" checkbox.
- Security questions for recovery.
- SF Symbols icons (Apple licensing).
- Arbitrary recipient field for delivery.
- localStorage / sessionStorage for sensitive data.
- Any source-code-embedded credentials.

If a feature here seems useful, do not add it. Confirm with the user first.

---

## 21. Build order — execute in this sequence

Each step is a discrete task. Do not move to step N+1 until step N is committed and any associated tests pass. The model column shows the recommended Claude model for that step; switch using `/model` in Claude Code at step boundaries.

| # | Step | Model | Why |
|---|------|-------|-----|
| 1 | Directory scaffold + `requirements.txt` + empty `ans_knowledge/` placeholders + `config.yaml` + `.env.example` | Haiku 4.5 | Pure boilerplate, fully specified above |
| 2 | SQLAlchemy models + Alembic initial migration matching section 6 exactly | Sonnet 4.6 | Schema is precise but relationships need care |
| 3 | Auth: setup wizard, login, sessions, rate limiting, argon2id | **Opus 4.7** | Security-critical; a bug here breaks the whole app's security |
| 4 | Admin shell + `/admin/credentials` + Keychain integration | **Opus** for Keychain wrapper and masked write-only fields; Sonnet for routes/UI | Split the step at the security boundary |
| 5 | Cost control module + tests for all seven runaway traps with a stubbed Claude client | **Opus 4.7** | Most important defensive infrastructure in the app; a subtle bug = runaway spend |
| 6 | Agent base wrapper (`agents/base.py`) + research agent | **Opus** for the base wrapper; Sonnet for the research agent | Base wrapper is core infrastructure for every later agent |
| 7 | Remaining Stage 1 agents: contact extractor, needs, briefing compiler | Sonnet 4.6 | Well-specified deliverables on top of a solid base wrapper |
| 8 | Briefing PDF builder + briefing review UI | Sonnet 4.6 | Standard WeasyPrint + Jinja2 work |
| 9 | Approval gate state machine and inspector UI | **Opus** for the state machine; Sonnet for the UI | State machine bugs cause stuck jobs |
| 10 | Stage 2 agents: mapping, three writers, critic | **Opus** for mapping and critic; Sonnet for the three writers | Mapping and critic involve subtle reasoning; writers are well-specified |
| 11 | Document assembly: PDFs, XLSX, watermarking | Sonnet 4.6 | Deterministic Python, fully specified |
| 12 | Logo processing pipeline + startup probe for Ghostscript/Poppler | Sonnet 4.6 | Multi-format with subprocesses; Sonnet handles the edge cases |
| 13 | M365 delivery + recipient-locking tests | **Opus 4.7** | Recipient-lock is the entire compliance story |
| 14 | Forgotten-password flow (token issuance, email via M365, reset endpoint) | **Opus 4.7** | Auth-adjacent; security-critical |
| 15 | HIG design system: `tokens.css`, `components.css` | Sonnet 4.6 (Haiku for pure token files) | Volume, not complexity, once the system is defined |
| 16 | All 13 screens implemented | Sonnet 4.6 | Standard frontend work |
| 17 | M4-specific polish: P3 colour, ProMotion motion, glass layers, gestures, shortcuts | Sonnet baseline; **Opus** for hard problems | ProMotion and P3 mapping have judgement in them |
| 18 | End-to-end test on three real companies | Sonnet 4.6 (Opus only if a deep design issue emerges) | Running the pipeline and triaging |

**After step 13, run one Opus audit pass over `backend/delivery/m365.py` specifically looking for recipient-lock bypass paths. After step 5, run one Opus audit pass over `backend/cost_control/` specifically looking for trap-evasion paths.** These are cheap insurance against the most expensive possible bugs.

---

## 22. Acceptance criteria

The build is complete when all of the following are true:

1. All seven runaway traps have passing tests and fire correctly in manual smoke tests.
2. Recipient-lock tests in `tests/delivery/` pass.
3. Auth flow tests pass: setup wizard → login → forgot → reset → change password.
4. A full end-to-end run against three real ≥1000-staff companies produces four watermarked outputs delivered to the user's M365 mailbox as drafts.
5. The Admin section exposes every panel listed in section 15.
6. `ans-tool audit week` produces a sensible CSV for a non-empty run history.
7. The frontend passes the quality bar reminder (hairlines, materials, semantic colours, 4pt grid, leading labels, type scale, brightness-shift, focus rings).
8. No source file contains a literal API key, password, or other secret. `git grep -E "(sk-ant|client_secret|password.*=.*\")"` returns nothing from production code.

Begin with step 1.
