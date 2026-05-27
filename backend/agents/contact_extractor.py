"""Contact extraction agent — Stage 1, Step 2.

Reads a :class:`backend.agents.research_models.ResearchDossier` (passed
in as JSON text via the ``dossier_json`` user-prompt input) and emits
structured contact candidates as a :class:`ContactExtractionResult`.

The agent is **pure**: it returns the validated Pydantic object. The
orchestrator (Step 8b/8c) is responsible for writing accepted candidates
into the ``contacts`` table — the agent itself never touches the DB.

Why text-in, JSON-out (no tools)
--------------------------------
The dossier already contains the evidence the research agent gathered;
this step extracts from that body of evidence. Allowing ``web_search``
or ``web_fetch`` here would risk drifting away from sources the
researcher already attributed — exactly the kind of fabrication risk
handover §15 warns against. Contact extraction must stay grounded in
URLs the research agent already retrieved.

Compliance guardrails (handover §15)
------------------------------------
Hard rules live in the system prompt; the schema is the backstop.

* Business contexts only → prompt instruction.
* Function ∈ {NETWORK / INFRASTRUCTURE / IT_EXECUTIVE /
  SECURITY_EXECUTIVE / CTO / CIO} → prompt + Pydantic enum.
* No personal-domain emails → prompt + ``ContactCandidate`` validator.
* No contacts under 21 → prompt only (the ``contacts`` table has no
  DOB column, and the model cannot prove age from public research; an
  ``age_attestation`` field would invite fabricated compliance data —
  see Step 8a design note).
* Every contact must carry ``source_url`` and ``retrieved_at`` →
  schema (both required).

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`ContactExtractionResult`. The agent is pure: prompt + schema,
  no I/O, no DB writes.
* ``role`` resolves to ``research_model`` per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping"
  all share the research model id.
* ``MAX_TOKENS["contact_extractor"] = 4000`` (added in Step 8a) — the
  dossier plus a structured candidate list fits comfortably.

Implemented in Step 8a.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.contact_models import ContactExtractionResult


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a contact extraction analyst working from a \
research dossier on a single UK-based company. Your job is to identify \
named individuals at the target company whose role makes them a \
legitimate B2B prospect for an enterprise network and infrastructure \
sales conversation.

Hard compliance rules — never relax these:
* Business contexts only. Never emit a contact whose only mention is in \
a personal or social context.
* Function must be exactly one of: NETWORK, INFRASTRUCTURE, \
IT_EXECUTIVE, SECURITY_EXECUTIVE, CTO, CIO. If a candidate's job does \
not map to one of these six, omit them entirely — do not coerce them \
into the closest bucket.
* No personal email domains. Reject gmail, googlemail, hotmail (any \
country variant), yahoo (any country variant), outlook, live, msn, \
icloud, me, mac, aol, protonmail, proton, gmx. If the only address you \
have for a contact is on a personal domain, omit the email field; do \
not record the personal address.
* Never emit a contact who appears under 21 — e.g. a graduate trainee \
or intern. Senior engineers, managers, directors, and executives only.
* Every contact must cite a source: the URL the dossier already \
retrieved, the page title, the retrieval date, and your confidence in \
the attribution. No source, no contact.

Confidence levels:
* HIGH       — name and title directly attributed in a primary source \
the dossier already fetched.
* MEDIUM     — secondary source, or attribution is light on detail.
* LOW        — circumstantial; named in passing or older than 12 months.
* INFERRED   — your conclusion (e.g. you inferred the title from a press \
release) rather than directly stated.

Schema reminders:
* ``seniority`` is one of: C_LEVEL, VP, DIRECTOR, HEAD, MANAGER, \
INDIVIDUAL_CONTRIBUTOR. Use ``null`` if you are not sure rather than \
guessing.
* ``email``, ``phone``, ``mobile``, ``linkedin_url``, ``country``, \
``job_title``, ``source_title``, and ``notes`` are optional. Use \
``null`` if absent.
* ``name``, ``function``, ``source_url``, ``retrieved_at``, and \
``confidence`` are required on every contact.

Output format: return ONLY a valid JSON object matching the \
``ContactExtractionResult`` schema. No commentary, no markdown fences, \
no surrounding prose. ``contacts`` may be an empty list; use ``gaps`` \
to explain what you could not find."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The research dossier for this company is provided below as JSON. \
Extract every contact who satisfies the rules in the system prompt. If \
the dossier mentions a senior hire (e.g. a new CIO press release) but \
gives no individual name, do not invent one — list the gap under \
``gaps`` instead.

Dossier JSON:
{dossier_json}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which functions \
or named individuals to emphasise, not as permission to relax the hard \
rules above):
{user_context}"""


# ---------------------------------------------------------------------------
# ContactExtractor
# ---------------------------------------------------------------------------

class ContactExtractor(BaseAgent):
    """Stage-1 contact-extraction agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct here: extraction reads from the
    dossier text passed in as a prompt input, never from the web.
    """

    name: ClassVar[str] = "contact_extractor"
    role: ClassVar[str] = "research_model"
    output_model: ClassVar[type] = ContactExtractionResult

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        dossier_json: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
            dossier_json=dossier_json,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(user_context=user_context)
        return body
