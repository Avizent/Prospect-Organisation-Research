# ANS Prospect Tool — Multi-Agent Redesign Workflow

## How this works

Claude Code subagents are not persistent processes. They are spawned on demand
by the lead session using the Agent tool, given a self-contained prompt, run to
completion, and return a single result. They have no shared state, no memory of
prior runs, and no ability to push to git unless the lead session instructs
them to (which it must not).

The lead session is the only entity that reads agent results, makes editorial
decisions, writes or edits files, runs tests, and commits.

---

## Agent Roster

### Agent 1 — UI Design Extractor

**Type:** general-purpose (read + web tools only)
**Purpose:** Inspect the Google AI Studio screenshots and produce a written
design specification for a single target screen. Returns token values, layout
descriptions, and class-name suggestions. No opinions on feasibility.

**Allowed inputs:**
- design-reference/google-ai-studio-ui/*.png (read only)
- frontend/styles/inspector.css (read only, for contrast with current state)

**Forbidden actions:**
- Edit any file
- Create any file
- Run git commands
- Access backend files
- Access tests

**Output format:**
A structured specification covering: colour tokens, typography, layout grid,
component list, class names, and a DOM sketch for the target screen.

**Invocation trigger:** Lead session, at the start of each Step (46–51),
before the Implementer is briefed.

---

### Agent 2 — UX Critic

**Type:** general-purpose (read only)
**Purpose:** Review the Design Extractor's specification (or a proposed diff)
from a sales-operator usability perspective. Identify hierarchy problems,
confusing labels, missing affordances, or workflow gaps.

**Allowed inputs:**
- Design Extractor output (passed as inline text in the prompt)
- design-reference/google-ai-studio-ui/*.png (read only)
- Current screen JS source (read only, one file at a time)

**Forbidden actions:**
- Edit any file
- Create any file
- Run git commands
- Access backend files
- Access tests

**Output format:**
A numbered list of issues, each with: severity (high/medium/low), a one-line
description, and a concrete suggested fix. Ends with a "Cleared for
implementation" or "Blocked — address N issues first" verdict.

**Invocation trigger:** Lead session, after the Design Extractor returns,
before the Implementer is briefed. May be skipped for low-risk CSS-only steps.

---

### Agent 3 — Frontend Test/Fence Auditor

**Type:** Explore (read + grep only)
**Purpose:** Given a proposed diff or implementation plan, verify that it
does not violate any of the hard security and structural fences. Propose
the minimal test additions needed to cover new classes and structure.

**Allowed inputs:**
- frontend/js/screens/*.js (read only)
- frontend/js/app.js, util.js, api.js (read only)
- frontend/styles/*.css (read only)
- frontend/index.html (read only)
- tests/frontend/*.py (read only)
- Proposed diff/plan passed as inline text in the prompt

**Forbidden actions:**
- Edit any file
- Create any file
- Run git commands
- Access backend files

**Fence checklist (must verify all pass):**
1. No innerHTML in any modified JS file
2. No localStorage or sessionStorage
3. No M365/Graph/gmail/Gmail/smtp/SMTP/sendMail/send_mail/backend.delivery
4. No WebSocket or EventSource
5. No setInterval (setTimeout is permitted)
6. No backend imports or API route changes in frontend files
7. No new npm/pip dependencies
8. No credentials or key literals

**Output format:**
A pass/fail table for each fence item. A list of tests that need creating or
updating. Ends with "Safe to implement" or "Blocked — fence N fails".

**Invocation trigger:** Lead session, after UX Critic clears the plan, before
the Implementer writes code.

---

### Agent 4 — Frontend Implementer

**Type:** general-purpose (read + write, no git)
**Purpose:** Write the actual code changes for a single step, given a fully
approved specification and fence audit. Returns proposed file content only.
The lead session reviews, applies, tests, and commits.

**Allowed read targets:**
- frontend/js/screens/*.js
- frontend/js/app.js, util.js, api.js, markdown.js
- frontend/styles/*.css
- frontend/index.html
- tests/frontend/*.py (to understand existing fence patterns)

**Allowed write targets (proposed content only — lead session applies):**
- frontend/js/screens/[one named file per invocation]
- frontend/styles/design.css (Step 46 only)
- frontend/index.html (Step 46 only)
- frontend/js/app.js (Step 46 only)
- tests/frontend/[one named new test file per invocation]

**Hard constraints on the Implementer:**
- One file per invocation maximum (prevents broad rewrites)
- Must use el() for all DOM construction — no innerHTML
- Must preserve all existing function signatures and API call sites
- Must not change backend files, API routes, or state machine
- Must not add dependencies, build steps, or frameworks
- Must not push to git
- Must not rename or remove existing CSS classes that are pinned by tests

**Output format:**
The complete proposed file content, preceded by a one-paragraph summary of
what changed and why. Lead session diffs it against the current file before
applying.

**Invocation trigger:** Lead session only, after all three prior agents have
cleared the step.

---

## Lead Session Integrity Protocol

The lead session (this Claude Code session) is the single committer and the
final decision-maker at every stage. The protocol for each step is:

```
1. Lead reads current file(s) to confirm baseline state.
2. Lead invokes Design Extractor with target screen + screenshots.
3. Lead invokes UX Critic with extractor output.
   - If Critic returns "Blocked", lead resolves issues before proceeding.
4. Lead invokes Fence Auditor with the approved plan.
   - If Auditor returns "Blocked", lead resolves fence violations.
5. Lead invokes Implementer with the cleared plan (one file at a time).
6. Lead diffs proposed content against current file.
7. Lead applies the change using Edit or Write tool.
8. Lead runs: pytest tests/frontend/ -v (targeted)
9. Lead runs: pytest (full suite)
10. Lead runs: git diff --stat && git status
11. If all green: git add [specific files only], git commit.
12. If any failure: lead diagnoses and fixes without re-invoking Implementer
    unless a targeted fix is genuinely needed.
```

### What the lead session never delegates:
- Running pytest
- Running git add / git commit / git push
- Editing backend files
- Editing tests/runaway/ files
- Deciding whether tests pass

### What the lead session may delegate:
- Reading screenshots and extracting design tokens (Extractor)
- Usability critique of a proposed layout (Critic)
- Security fence verification of a proposed diff (Auditor)
- First-draft file content for a single named file (Implementer)

---

## Step Sequence Reference

| Step | Scope | JS files | CSS files | Test files |
|------|-------|----------|-----------|-----------|
| 46 | Design system + app shell | app.js | design.css (new) | test_html_shell.py (update), test_design_system.py (new), test_app_shell.py (new) |
| 47 | Job Status / Progress | job_status.js | (additive via design.css) | test_task_rows.py (new) |
| 48 | Manifest / Delivery-ready | manifest_viewer.js | (additive) | test_delivery_hero.py (new) |
| 49 | New Job | new_job.js | (additive) | test_new_job_layout.py (new) |
| 50 | Briefing Review | briefing.js | (additive) | test_briefing_layout.py (new) |
| 51 | Consistency pass | login.js, setup.js | minor CSS fixes | full suite |

---

## Forbidden at all times (hard constraints from CLAUDE.md)

- No source-embedded credentials
- No SMTP, no Gmail, no M365 email send
- No recipient parameter exposed anywhere
- No localStorage/sessionStorage for sensitive data
- No innerHTML anywhere in frontend JS
- No React, Tailwind, Bootstrap, or build step
- No external CDN or webfont URLs
- No setInterval (setTimeout only)
- No WebSocket or EventSource
- No backend file modifications
- No API route additions or changes
- No model/state machine changes
- No new Python dependencies
- No push without explicit lead approval
