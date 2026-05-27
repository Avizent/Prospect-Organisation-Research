"""Briefing compiler agent — Stage 1, Step 4.

Reads three upstream artefacts (passed in as JSON text via the
``dossier_json``, ``contacts_json``, and ``needs_json`` user-prompt
inputs) and emits a :class:`Briefing` — the load-bearing artefact for
the human approval gate and every Stage 2 writer.

The agent is **pure**: it returns the validated Pydantic object. The
orchestrator (a later step) is responsible for persisting the briefing
to ``jobs/{job_id}/briefing.json``; the agent itself never touches the
DB or the filesystem.

Why text-in, JSON-out (no tools)
--------------------------------
Handover §9.2 step 5: the briefing compiler "produces structured
briefing.json" from the upstream artefacts. Allowing ``web_search`` or
``web_fetch`` here would re-do the research agent's work and risk
drifting away from sources the researcher already attributed. The
compiler merges; the researcher gathers.

Inputs
------
* ``company_name`` — for the prompt header
* ``company_url`` — for the prompt header
* ``dossier_json`` — :class:`ResearchDossier` serialised as JSON text
* ``contacts_json`` — :class:`ContactExtractionResult` serialised as
  JSON text
* ``needs_json`` — :class:`NeedsAssessment` serialised as JSON text
* ``user_context`` (optional) — operator hint, surfaced verbatim as a
  *hint to the model*. The agent does NOT echo this back into
  :attr:`Briefing.user_context` — that field is owned by the approval
  screen / edit endpoint.

This is the only Stage-1 agent that consumes all three upstream
artefacts. Subsequent Stage-2 agents read the *approved*
``briefing.json`` (handover line 236), not the raw inputs.

Where this agent fits
---------------------
* The base wrapper drives the call: a single ``messages_create``,
  retry-on-parse-failure once, validate against :class:`Briefing`. The
  agent is pure: prompt + schema, no I/O, no DB writes.
* ``role`` resolves to ``research_model`` per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping"
  all share the research model id.
* ``MAX_TOKENS["briefing_compiler"] = 8000`` is already in
  :mod:`backend.agents.limits` (no Step 8c change there).

Implemented in Step 8c.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.briefing_models import Briefing


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior infrastructure and network sales \
analyst compiling a single human-reviewable briefing for one UK-based \
target company. Your job is to MERGE three upstream artefacts — the \
research dossier, the extracted contacts, and the needs assessment — \
into a structured ``Briefing`` JSON. You have no web-search or \
web-fetch tools.

Hard rules:
* Merge only — do not invent facts not present in the three upstream \
artefacts. If a claim is not supported by them, omit it or list it \
under ``sources.gaps`` rather than fabricating.
* Every claim-bearing field carries a ``confidence`` value: HIGH \
(directly attributed in a primary source the dossier fetched), MEDIUM \
(secondary source or light on detail), LOW (circumstantial or older \
than 12 months), INFERRED (your conclusion, not stated).
* The ``sources.entries`` array is the single canonical list of \
sources for the whole briefing. Every claim references it positionally \
via ``source_indices`` (or ``source_index`` for a contact). Indices \
are zero-based and must point to a real entry — out-of-range indices \
will be rejected by the schema.
* Contacts have already been validated upstream (function whitelist, \
personal-email block list, no under-21s). Render them as you received \
them; do not relax those rules.

Six sections, in order:

1. ``snapshot`` — headline, one-line description, sector, headcount \
band, HQ country, ownership, lab maturity (use the value from the \
needs assessment verbatim), why this target is interesting to ANS, \
and overall confidence.

2. ``business_context`` — news, M&A, financials, regulatory exposure, \
risks. Each list contains ``BriefingClaim`` rows.

3. ``it_landscape`` — tech stack, architecture notes, vendors, \
transformation programmes, lab-maturity reasoning paragraph, team \
shape, tooling.

4. ``key_people`` — named contacts (rendered as ``BriefingContact`` \
rows) and hiring signals. Use the contacts from the contact extraction \
result; do not add new people the dossier does not already name.

5. ``opportunity`` — ANS opportunity hypothesis. ``ranked_needs`` \
mirrors the needs assessment, augmented with suggested product \
*categories* (e.g. "SD-WAN replacement", "zero-trust segmentation") — \
NOT ANS catalogue SKUs. Product mapping is a separate downstream step. \
Include ``buying_cycle_stage``, a one-paragraph \
``recommended_angle``, and ``watch_outs``.

6. ``sources`` — the source register. Every URL the briefing depends \
on appears in ``sources.entries`` exactly once. Use ``sources.gaps`` \
to record what you could not reconcile from the upstream artefacts.

Output format: return ONLY a valid JSON object matching the \
``Briefing`` schema. No commentary, no markdown fences, no \
surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

Compile a ``Briefing`` from the three upstream artefacts provided \
below as JSON. Merge them — do not re-research, do not invent facts \
the three artefacts do not support. If a claim cannot be backed by \
the upstream artefacts, list the question under ``sources.gaps`` \
rather than guessing.

Research dossier JSON:
{dossier_json}

Contact extraction result JSON:
{contacts_json}

Needs assessment JSON:
{needs_json}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which \
sections or angles to emphasise, not as permission to invent claims \
the three upstream artefacts do not support):
{user_context}"""


# ---------------------------------------------------------------------------
# BriefingCompiler
# ---------------------------------------------------------------------------

class BriefingCompiler(BaseAgent):
    """Stage-1 briefing compiler.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent merges the three upstream
    artefacts passed in as prompt inputs, never the web.
    """

    name: ClassVar[str] = "briefing_compiler"
    role: ClassVar[str] = "research_model"
    output_model: ClassVar[type] = Briefing

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        dossier_json: str,
        contacts_json: str,
        needs_json: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
            dossier_json=dossier_json,
            contacts_json=contacts_json,
            needs_json=needs_json,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(
                user_context=user_context
            )
        return body
