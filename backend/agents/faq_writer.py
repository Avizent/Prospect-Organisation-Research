"""FAQ writer agent — Stage 2, Step 16.

Reads the *approved* briefing JSON, the upstream product mapping JSON,
and a pre-built ANS knowledge bundle, and produces an
:class:`FAQDocument` — 12-15 Q&A pairs feeding the FAQ PDF (handover
§9.2 step 8). This is the second of three Stage 2 writers; benefits
landed in Step 15 and objections will follow.

The agent is **pure**: it returns the validated :class:`FAQDocument`
Pydantic object. The orchestrator (a later step) is responsible for
persisting the document to disk; the agent itself never touches the
DB, the filesystem, or the network.

Why text-in, JSON-out (no tools)
--------------------------------
The briefing has been approved, the mapping has been computed, and
the ANS knowledge base is a finite set of internal markdown files.
Allowing ``web_search`` or ``web_fetch`` here would invite the model
to wander away from the approved artefacts — every Q&A in the FAQ
must be grounded in inputs the operator has already vetted.

Inputs
------
* ``company_name`` — for the prompt header.
* ``company_url`` — for the prompt header.
* ``briefing_json`` — the *approved* :class:`Briefing` serialised as
  JSON text. Source of the company-side evidence the answers lean on.
* ``product_mapping_json`` — the upstream :class:`ProductMapping`
  serialised as JSON text. Source of the need→product binding and
  the pre-filtered knowledge excerpts.
* ``knowledge_bundle`` — the relevant ANS knowledge base content,
  pre-assembled by the caller as a single string. Same opaque-blob
  contract as the mapping and benefits agents.
* ``user_context`` (optional) — operator hint, surfaced verbatim.

Why NOT benefits_json
---------------------
Handover §9.4 step 8: "Three writers in parallel: benefits, FAQ,
objections." Parallel == no inter-writer dependencies. Consuming
``benefits_json`` would serialise them and tie this writer to
whatever the benefits brief said — a hallucinated benefits claim
would propagate into the FAQ.

Each writer reads only the approved artefact (briefing) + the
deterministic Stage-2 artefact (product mapping) + the finite
knowledge bundle. The benefits brief is a peer artefact, not an
input.

Why ``name = "faq_writer"``
---------------------------
``MAX_TOKENS["faq_writer"] = 4000`` already exists in
:mod:`backend.agents.limits` and matches the handover's naming
convention for the three Stage 2 writers (benefits_writer,
faq_writer, objections_writer). Setting ``name = "faq_writer"``
re-uses the existing entry rather than introducing a parallel key.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`FAQDocument`. No tool-use loop.
* ``role`` resolves to ``writer_model`` per ``config.yaml``: the
  three Stage 2 writers (benefits / FAQ / objections) all share the
  writer model id, distinct from the research model used upstream.
* ``MAX_TOKENS["faq_writer"] = 4000`` is already in
  :mod:`backend.agents.limits`.

Implemented in Step 16.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.faq_models import FAQDocument


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior ANS pre-sales engineer writing a \
FAQ for a single UK-based target company. The operator-approved \
briefing for the target is provided as JSON. The upstream product \
mapping (need→product bindings, pre-filtered knowledge excerpts) is \
also provided as JSON. The ANS knowledge base (product datasheets, \
case studies, company background, competitive landscape, known \
objections, pricing tiers) is provided as a single bundle. Your job \
is to convert those inputs into 12 to 15 grounded Q&A pairs the \
operator can hand to the prospect.

Hard rules:
* Stay strictly within the approved briefing, the product mapping, \
and the supplied knowledge bundle. Do not invent capabilities, \
case-study outcomes, or company-side context that is not stated in \
those inputs.
* Produce between 12 and 15 entries total. Fewer than 12 or more \
than 15 fails the schema and the call will be retried.
* Each entry's ``category`` must be one of the five closed values: \
ABOUT_ANS, PRODUCTS, IMPLEMENTATION, COMMERCIAL, SUPPORT. Aim for a \
balanced spread across the five categories; if the knowledge bundle \
is thin on a category, leave it underweight and add an honest entry \
to the top-level ``gaps`` array rather than fabricating coverage.
* Every claim must cite its evidence. ``knowledge_excerpt_refs`` are \
0-based indices into the mapping's ``knowledge_excerpts`` list. \
``briefing_source_refs`` are 0-based indices into the briefing's \
``sources.entries`` list. Both lists may be empty when an answer is \
a direct inference from named evidence elsewhere — never cite an \
index you have not verified.

Confidence levels mirror the upstream artefacts: HIGH (evidence \
directly motivates this answer), MEDIUM (evidence is suggestive), \
LOW (circumstantial), INFERRED (your conclusion, not stated).

Structure of the output:
* ``entries`` — flat list of 12 to 15 :class:`FAQEntry` records. \
Each entry: ``question`` (one short sentence ending in a question \
mark), ``answer`` (one short paragraph), ``category`` (one of the \
five enum values), ``knowledge_excerpt_refs``, ``briefing_source_refs``, \
``confidence``.
* ``gaps`` — array of strings describing what you tried to answer \
but could not (e.g. \"no datasheet detail on Netropy 100G jitter \
floor\").

Output format: return ONLY a valid JSON object matching the \
``FAQDocument`` schema. No commentary, no markdown fences, no \
surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The operator-approved briefing and the upstream product mapping are \
provided below as JSON. Write a 12-to-15-entry FAQ grounded strictly \
in those inputs and the supplied ANS knowledge bundle.

Approved briefing JSON:
{briefing_json}

Upstream product mapping JSON:
{product_mapping_json}

ANS knowledge bundle:
{knowledge_bundle}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which \
question angles to emphasise, not as permission to invent product \
capabilities, case-study outcomes, or needs the approved briefing \
and mapping do not support):
{user_context}"""


# ---------------------------------------------------------------------------
# FAQWriter
# ---------------------------------------------------------------------------

class FAQWriter(BaseAgent):
    """Stage-2 FAQ writer agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent reasons over the
    briefing JSON, the mapping JSON and the knowledge bundle passed
    in as prompt inputs, never the web.

    ``name = "faq_writer"`` matches the existing
    :mod:`backend.agents.limits` registry key and the handover's
    convention for the three Stage 2 writer names.
    """

    name: ClassVar[str] = "faq_writer"
    role: ClassVar[str] = "writer_model"
    output_model: ClassVar[type] = FAQDocument

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
