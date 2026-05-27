"""Needs inference agent — Stage 1, Step 3.

Reads a :class:`backend.agents.research_models.ResearchDossier` (passed
in as JSON text via the ``dossier_json`` user-prompt input) and emits a
:class:`NeedsAssessment` whose headline value is a
:class:`LabMaturity` enum drawn from the four canonical buckets.

The agent is **pure**: it returns the validated Pydantic object. The
orchestrator (a later step) is responsible for persisting the
assessment to disk and into ``companies.lab_maturity``; the agent
itself never touches the DB.

Why text-in, JSON-out (no tools)
--------------------------------
Handover §9.3 step 4: "Reasons over the dossier." Allowing
``web_search`` or ``web_fetch`` here would re-do the research agent's
work and risk drifting away from sources the researcher already
attributed. The needs agent reasons; the research agent gathers.

Inputs
------
* ``company_name`` — for the prompt header
* ``company_url`` — for the prompt header
* ``dossier_json`` — the ``ResearchDossier`` serialised as JSON text
* ``user_context`` (optional) — operator hint, surfaced verbatim

The agent does NOT receive ``ContactExtractionResult``. Contacts are
people, not signals of lab maturity; conflating the two would invite
the model to anchor on individuals (e.g. "they just hired a CIO ⇒
MATURE") when the dossier's job-postings, vendor-footprint, and
annual-reports sections are the right primary signals.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`NeedsAssessment`. The agent is pure: prompt + schema, no
  I/O, no DB writes.
* ``role`` resolves to ``research_model`` per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping"
  all share the research model id.
* ``MAX_TOKENS["needs"] = 3000`` is already in
  :mod:`backend.agents.limits` (no Step 8b change there).

Implemented in Step 8b.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.needs_models import NeedsAssessment


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior infrastructure and network sales \
analyst preparing a needs-inference for a single UK-based target \
company. Your job is reasoning, not research: the research agent has \
already gathered the evidence, and you must work strictly from the \
dossier provided. You have no web-search or web-fetch tools.

Hard rules:
* Reasoning only — do not invent facts not present in the dossier. If \
the dossier does not support a claim, omit it or list it under \
``gaps`` rather than fabricating.
* Every load-bearing claim must cite at least one evidence pointer \
back to a dossier source: a URL, the page title, the retrieval date, \
and your confidence in the citation.
* Confidence levels mirror the dossier's: HIGH (directly attributed in \
a primary source the dossier fetched), MEDIUM (secondary source or \
light on detail), LOW (circumstantial or older than 12 months), \
INFERRED (your conclusion, not stated).

Headline output — ``lab_maturity``:
Choose exactly one of these four values, lower-case, verbatim:
* ``none_visible`` — no public evidence of a network / infrastructure \
lab function at all.
* ``basic`` — minimal in-house infrastructure team, mostly \
off-the-shelf vendors, no transformation programme.
* ``mature`` — established in-house team, named senior leadership, \
stable vendor footprint, no obvious modernisation pressure.
* ``modernisation_in_progress`` — public evidence of an active \
transformation programme (cloud / SDN / zero-trust job postings, \
vendor case studies dated within 18 months, press releases on \
infrastructure refresh).

Lab-maturity supporting fields:
* ``lab_maturity_reasoning`` — a short paragraph explaining the call.
* ``lab_maturity_evidence`` — a list of pointers back to dossier \
sources. MAY be empty when ``lab_maturity == none_visible`` (the \
absence of evidence is the call). For all other values, supply at \
least one evidence pointer.
* ``lab_maturity_confidence`` — your overall confidence in the bucket \
choice (HIGH / MEDIUM / LOW / INFERRED).

Needs list (``needs``):
* Each entry is one identified need at the target company that ANS \
might address. Fields: ``summary`` (one-line headline), ``detail`` \
(supporting paragraph), ``priority`` (positive integer, 1 = highest, \
ascending, no ties), ``evidence`` (list of pointers), ``confidence``.
* MAY be empty if the dossier surfaces no actionable needs — list \
what you looked for under ``gaps`` instead of inventing needs.

Gaps:
* ``gaps`` — array of strings describing what you tried to assess but \
could not (e.g. "no public information on internal team size").

Output format: return ONLY a valid JSON object matching the \
``NeedsAssessment`` schema. No commentary, no markdown fences, no \
surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The research dossier for this company is provided below as JSON. \
Reason over it and produce a ``NeedsAssessment``. Stay strictly within \
the dossier's evidence — if it does not support a claim, list the \
question under ``gaps`` rather than guessing.

Dossier JSON:
{dossier_json}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which needs \
or maturity signals to emphasise, not as permission to invent claims \
the dossier does not support):
{user_context}"""


# ---------------------------------------------------------------------------
# NeedsAgent
# ---------------------------------------------------------------------------

class NeedsAgent(BaseAgent):
    """Stage-1 needs-inference agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent reasons over the
    dossier text passed in as a prompt input, never the web.
    """

    name: ClassVar[str] = "needs"
    role: ClassVar[str] = "research_model"
    output_model: ClassVar[type] = NeedsAssessment

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
