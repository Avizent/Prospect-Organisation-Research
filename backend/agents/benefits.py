"""Benefits writer agent — Stage 2, Step 15.

Reads the *approved* briefing JSON, the upstream product mapping JSON,
and a pre-built ANS knowledge bundle, and produces a
:class:`BenefitsBrief` — the headline three-tier deliverable feeding
the benefits PDF (handover §9.2 step 8). This is the first of three
Stage 2 writers; FAQ and objections follow in later steps.

The agent is **pure**: it returns the validated :class:`BenefitsBrief`
Pydantic object. The orchestrator (a later step) is responsible for
persisting the brief to disk; the agent itself never touches the DB,
the filesystem, or the network.

Why text-in, JSON-out (no tools)
--------------------------------
The briefing has been approved, the mapping has been computed, and
the ANS knowledge base is a finite set of internal markdown files.
Allowing ``web_search`` or ``web_fetch`` here would invite the model
to wander away from the approved artefacts — every fact in the brief
must be grounded in inputs the operator has already vetted.

Inputs
------
* ``company_name`` — for the prompt header.
* ``company_url`` — for the prompt header.
* ``briefing_json`` — the *approved* :class:`Briefing` serialised as
  JSON text. Source of the company-side evidence the brief leans on.
* ``product_mapping_json`` — the upstream :class:`ProductMapping`
  serialised as JSON text. Source of the need→product binding and
  the pre-filtered knowledge excerpts.
* ``knowledge_bundle`` — the relevant ANS knowledge base content,
  pre-assembled by the caller as a single string. Same opaque-blob
  contract as the mapping agent.
* ``user_context`` (optional) — operator hint, surfaced verbatim.

Why a fixed key, not registry growth
------------------------------------
``MAX_TOKENS["benefits_writer"] = 6000`` already exists in
:mod:`backend.agents.limits` and matches the handover's naming
convention for the three Stage 2 writers (benefits_writer,
faq_writer, objections_writer). Setting ``name = "benefits_writer"``
re-uses the existing entry rather than introducing a parallel
``"benefits"`` key — that would create a foot-gun the day FAQ and
objections land and pick the longer names.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`BenefitsBrief`. No tool-use loop.
* ``role`` resolves to ``writer_model`` per ``config.yaml``: the
  three Stage 2 writers (benefits / FAQ / objections) all share the
  writer model id, distinct from the research model used upstream.
* ``MAX_TOKENS["benefits_writer"] = 6000`` is already in
  :mod:`backend.agents.limits`.

Implemented in Step 15.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.benefits_models import BenefitsBrief


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior ANS pre-sales engineer writing a \
three-tier benefits brief for a single UK-based target company. The \
operator-approved briefing for the target is provided as JSON. The \
upstream product mapping (need→product bindings, pre-filtered \
knowledge excerpts) is also provided as JSON. The ANS knowledge base \
(product datasheets, case studies, company background, competitive \
landscape, known objections, pricing tiers) is provided as a single \
bundle. Your job is to convert those inputs into a benefits brief \
that speaks to three distinct readers.

Hard rules:
* Stay strictly within the approved briefing, the product mapping, \
and the supplied knowledge bundle. Do not invent capabilities, \
case-study outcomes, or needs that are not stated in those inputs.
* The brief has exactly three named sections, fixed in order: \
``executive_summary`` (for the CTO/CIO — strategic framing, business \
outcomes, one-paragraph tl;dr per claim), ``technical_fit`` (for the \
engineering audience — concrete capabilities, integration shape, \
test/lab framing) and ``business_case`` (for the IT Director — risk, \
spend justification, time-to-value). Each section's ``audience`` \
field must match its tier: CTO_CIO, ENGINEERS, IT_DIRECTOR.
* Every claim must cite its evidence. ``knowledge_excerpt_refs`` are \
0-based indices into the mapping's ``knowledge_excerpts`` list. \
``briefing_source_refs`` are 0-based indices into the briefing's \
``sources.entries`` list. Both lists may be empty when a claim is a \
direct inference from named evidence elsewhere — never cite an index \
you have not verified.
* If a section would lean on a topic the knowledge bundle covers \
thinly, add an honest entry to the top-level ``gaps`` array rather \
than fabricating coverage.

Confidence levels mirror the upstream artefacts: HIGH (evidence \
directly motivates this claim), MEDIUM (evidence is suggestive), LOW \
(circumstantial), INFERRED (your conclusion, not stated).

Structure of the output:
* ``executive_summary`` — strategic narrative for the CTO/CIO. \
``heading`` is a short title, ``summary`` is a one-paragraph tl;dr, \
``body`` is the non-empty list of :class:`BenefitClaim` records \
(claim → detail → why_it_matters → refs → confidence).
* ``technical_fit`` — engineering-grade detail for the engineers, \
same shape, ``audience = ENGINEERS``.
* ``business_case`` — risk/spend/time-to-value framing for the IT \
Director, same shape, ``audience = IT_DIRECTOR``.
* ``gaps`` — array of strings describing what you tried to assess \
but could not (e.g. \"no datasheet detail on Netropy 100G jitter \
floor\").

Output format: return ONLY a valid JSON object matching the \
``BenefitsBrief`` schema. No commentary, no markdown fences, no \
surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The operator-approved briefing and the upstream product mapping are \
provided below as JSON. Write a three-tier benefits brief grounded \
strictly in those inputs and the supplied ANS knowledge bundle.

Approved briefing JSON:
{briefing_json}

Upstream product mapping JSON:
{product_mapping_json}

ANS knowledge bundle:
{knowledge_bundle}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which \
benefits angles to emphasise, not as permission to invent product \
capabilities, case-study outcomes, or needs the approved briefing \
and mapping do not support):
{user_context}"""


# ---------------------------------------------------------------------------
# BenefitsWriter
# ---------------------------------------------------------------------------

class BenefitsWriter(BaseAgent):
    """Stage-2 benefits writer agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent reasons over the
    briefing JSON, the mapping JSON and the knowledge bundle passed
    in as prompt inputs, never the web.

    ``name = "benefits_writer"`` matches the existing
    :mod:`backend.agents.limits` registry key and the handover's
    convention for the three Stage 2 writer names.
    """

    name: ClassVar[str] = "benefits_writer"
    role: ClassVar[str] = "writer_model"
    output_model: ClassVar[type] = BenefitsBrief

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        briefing_json: str,
        product_mapping_json: str,
        knowledge_bundle: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
            briefing_json=briefing_json,
            product_mapping_json=product_mapping_json,
            knowledge_bundle=knowledge_bundle,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(user_context=user_context)
        return body
