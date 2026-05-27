"""Product mapping agent — Stage 2, Step 1.

Reads the *approved* briefing JSON plus a pre-built ANS knowledge
bundle and produces need→product mappings with concrete use-case
framing. Pre-filters which knowledge base excerpts each downstream
writer (benefits / FAQ / objections) will need.

The agent is **pure**: it returns the validated :class:`ProductMapping`
Pydantic object. The orchestrator (a later step) is responsible for
persisting the mapping to disk; the agent itself never touches the DB,
the filesystem, or the network.

Why text-in, JSON-out (no tools)
--------------------------------
Handover §9.4 step 7 (line 240): "reads approved briefing + ANS
knowledge base." There is no web work to do at this stage — the
research agent has already gathered evidence, the briefing has been
approved by the operator, and the ANS knowledge base is a finite set
of internal markdown files. Allowing ``web_search`` or ``web_fetch``
here would invite the model to wander away from the approved
artefacts.

Inputs
------
* ``company_name`` — for the prompt header
* ``company_url`` — for the prompt header
* ``briefing_json`` — the *approved* :class:`Briefing` serialised as
  JSON text. The agent treats this as opaque text; the orchestrator
  is responsible for serialising the on-disk artefact.
* ``knowledge_bundle`` — the relevant ANS knowledge base content,
  pre-assembled by the caller as a single string. Step 13 takes this
  as an opaque blob; the future knowledge loader will decide what to
  include based on the briefing's needs. Keeping the agent agnostic
  to the bundle's internal shape means schema decisions can be made
  later without re-touching the agent.
* ``user_context`` (optional) — operator hint, surfaced verbatim.

The agent does NOT receive (and must never read from disk):
* :class:`backend.agents.research_models.ResearchDossier` — Stage 2
  reads only the approved briefing (handover line 236).
* :class:`backend.agents.contact_models.ContactExtractionResult` —
  contacts are already projected into ``briefing.key_people``.
* :class:`backend.agents.needs_models.NeedsAssessment` — needs are
  already projected into ``briefing.opportunity.ranked_needs``.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`ProductMapping`. No tool-use loop.
* ``role`` resolves to ``research_model`` per ``config.yaml`` line 14:
  "Research, contact extraction, needs, briefing compiler, mapping"
  all share the research model id. The model choice at runtime is
  decided by the loaded :class:`Models` instance; the agent has no
  opinion.
* ``MAX_TOKENS["mapping"] = 2000`` is already in
  :mod:`backend.agents.limits`.

Implemented in Step 13.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.mapping_models import ProductMapping


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior ANS pre-sales engineer preparing \
a need-to-product mapping for a single UK-based target company. The \
operator-approved briefing for the target is provided as JSON. The \
ANS knowledge base (product datasheets, case studies, company \
background, competitive landscape, known objections, pricing tiers) \
is provided as a single bundle. Your job is to bind each ranked need \
in the briefing to one or more concrete ANS products and to \
pre-filter the knowledge excerpts the downstream writers will need.

Hard rules:
* Stay strictly within the approved briefing and the supplied \
knowledge bundle. Do not invent product capabilities not stated in \
the bundle. Do not invent needs not stated in the briefing.
* Every match must point at one of the ANS product lines that exist \
in the knowledge bundle. Today there are exactly two product lines: \
``emulators`` (Apposite Linktropy / Netropy series) and \
``traffic_generators``. If a need cannot be served by either of those \
two lines, put it under ``unmatched_needs`` with an honest reason \
rather than forcing a match.
* Every ``knowledge_excerpt_refs`` value must be a 0-based index into \
the ``knowledge_excerpts`` list you yourself emit. Out-of-range \
indices fail validation.
* If the knowledge bundle is thin on a relevant topic, list the gap \
under ``gaps`` rather than fabricating coverage.

Confidence levels mirror the briefing's: HIGH (the briefing's \
evidence directly motivates this match), MEDIUM (the briefing's \
evidence is suggestive), LOW (circumstantial), INFERRED (your \
conclusion, not stated).

Structure of the output:
* ``matches`` — one entry per ranked need you bind to >= 1 product. \
``need_priority`` and ``need_summary`` must mirror the briefing entry \
verbatim. ``products`` is a non-empty list of \
``{product_line, product_name, knowledge_excerpt_refs}`` records. \
``use_case_framing`` is the concrete framing for that need (how the \
product addresses it in this company's context). ``why_this_fits`` \
is the short rationale grounded in briefing evidence.
* ``unmatched_needs`` — ranked needs from the briefing that ANS \
cannot serve from the current product set. Each entry repeats the \
need's priority and summary and gives an honest reason.
* ``knowledge_excerpts`` — the pre-filtered list of knowledge \
excerpts the downstream writers will need. Each entry names the \
source file (one of the seven values listed in the schema), the \
markdown heading, and a short rationale.
* ``gaps`` — array of strings describing what you tried to assess \
but could not (e.g. \"no datasheet detail on Netropy 100G latency \
limits\").

Output format: return ONLY a valid JSON object matching the \
``ProductMapping`` schema. No commentary, no markdown fences, no \
surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The operator-approved briefing for this company is provided below as \
JSON. Bind each ranked need in ``opportunity.ranked_needs`` to one or \
more ANS products using only the supplied knowledge bundle. Stay \
strictly within both inputs.

Approved briefing JSON:
{briefing_json}

ANS knowledge bundle:
{knowledge_bundle}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which needs \
or product angles to emphasise, not as permission to invent product \
capabilities or needs the approved briefing does not support):
{user_context}"""


# ---------------------------------------------------------------------------
# ProductMappingAgent
# ---------------------------------------------------------------------------

class ProductMappingAgent(BaseAgent):
    """Stage-2 product-mapping agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent reasons over the
    briefing JSON and the knowledge bundle passed in as prompt
    inputs, never the web.
    """

    name: ClassVar[str] = "mapping"
    role: ClassVar[str] = "research_model"
    output_model: ClassVar[type] = ProductMapping

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        briefing_json: str,
        knowledge_bundle: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
            briefing_json=briefing_json,
            knowledge_bundle=knowledge_bundle,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(user_context=user_context)
        return body
