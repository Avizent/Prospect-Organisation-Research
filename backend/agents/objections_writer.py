"""Objections writer agent — Stage 2, Step 17.

Reads the *approved* briefing JSON, the upstream product mapping JSON,
and a pre-built ANS knowledge bundle, and produces an
:class:`ObjectionsRegister` — 15-20 XLSX-shaped rows feeding the
objections workbook (handover §9.2 step 8). This is the third of
three Stage 2 writers; benefits landed in Step 15 and FAQ in Step 16.

The agent is **pure**: it returns the validated
:class:`ObjectionsRegister` Pydantic object. The orchestrator (a
later step) is responsible for persisting the register to disk; the
agent itself never touches the DB, the filesystem, or the network.

Why text-in, JSON-out (no tools)
--------------------------------
The briefing has been approved, the mapping has been computed, and
the ANS knowledge base is a finite set of internal markdown files
(``known_objections.md`` is already inside ``knowledge_bundle``).
Allowing ``web_search`` or ``web_fetch`` here would invite the model
to wander away from the approved artefacts — every row in the
register must be grounded in inputs the operator has already vetted.

Inputs
------
* ``company_name`` — for the prompt header.
* ``company_url`` — for the prompt header.
* ``briefing_json`` — the *approved* :class:`Briefing` serialised as
  JSON text. Source of the company-side evidence the responses
  lean on.
* ``product_mapping_json`` — the upstream :class:`ProductMapping`
  serialised as JSON text. Source of the need→product binding and
  the pre-filtered knowledge excerpts.
* ``knowledge_bundle`` — the relevant ANS knowledge base content,
  pre-assembled by the caller as a single string. Same opaque-blob
  contract as the mapping / benefits / FAQ agents.
* ``user_context`` (optional) — operator hint, surfaced verbatim.

Why NOT benefits_json or faq_json
---------------------------------
Handover §9.4 step 8: "Three writers in parallel: benefits, FAQ,
objections." Parallel == no inter-writer dependencies. Consuming a
peer writer's output would serialise them and tie this writer to
whatever the upstream brief said — a hallucinated peer-writer claim
would propagate into the register.

Each writer reads only the approved artefact (briefing) + the
deterministic Stage-2 artefact (product mapping) + the finite
knowledge bundle. Peer writer outputs are not inputs.

Why ``name = "objections_writer"``
----------------------------------
``MAX_TOKENS["objections_writer"] = 4000`` already exists in
:mod:`backend.agents.limits` and matches the handover's naming
convention for the three Stage 2 writers (benefits_writer,
faq_writer, objections_writer). Setting
``name = "objections_writer"`` re-uses the existing entry rather
than introducing a parallel key.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`ObjectionsRegister`. No tool-use loop.
* ``role`` resolves to ``writer_model`` per ``config.yaml``: the
  three Stage 2 writers (benefits / FAQ / objections) all share the
  writer model id, distinct from the research model used upstream.
* ``MAX_TOKENS["objections_writer"] = 4000`` is already in
  :mod:`backend.agents.limits`.

Implemented in Step 17.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.objections_models import ObjectionsRegister


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior ANS pre-sales engineer compiling \
an objections register for a single UK-based target company. The \
operator-approved briefing for the target is provided as JSON. The \
upstream product mapping (need→product bindings, pre-filtered \
knowledge excerpts) is also provided as JSON. The ANS knowledge \
base — including the known_objections file — is provided as a \
single bundle. Your job is to convert those inputs into 15 to 20 \
XLSX-shaped rows the operator can hand to the sales team.

The XLSX has exactly six columns, fixed in order:
1. Category — one of PRICE, TIMING, TECHNICAL_FIT, COMPETITION, \
AUTHORITY, TRUST, INTEGRATION, COMPLIANCE.
2. Objection — the customer's stated objection, one short sentence.
3. Underlying concern — what the customer actually fears beneath \
the stated objection, one or two sentences.
4. Response — the ANS response, grounded in the briefing or \
knowledge bundle, one short paragraph.
5. Supporting evidence — references into the briefing's \
``sources.entries`` and the mapping's ``knowledge_excerpts``. \
Emitted as the two ref-list fields ``knowledge_excerpt_refs`` and \
``briefing_source_refs``; the renderer concatenates them into the \
single XLSX column.
6. Escalation path — who to bring in if the prospect pushes back, \
one short sentence. Required, never blank.

Hard rules:
* Stay strictly within the approved briefing, the product mapping, \
and the supplied knowledge bundle. Do not invent capabilities, \
case-study outcomes, competitor positioning, or company-side context \
that is not stated in those inputs.
* Produce between 15 and 20 rows total. Fewer than 15 or more than \
20 fails the schema and the call will be retried.
* Each row's ``category`` must be one of the eight closed values \
listed above. Aim for a balanced spread; if the knowledge bundle is \
thin on a category, leave it underweight and add an honest entry to \
the top-level ``gaps`` array rather than fabricating coverage.
* ``escalation_path`` must be non-empty. Even when the answer is \
"the account owner stays on the call", say so — never leave the \
cell blank.
* Every claim must cite its evidence. ``knowledge_excerpt_refs`` \
are 0-based indices into the mapping's ``knowledge_excerpts`` list. \
``briefing_source_refs`` are 0-based indices into the briefing's \
``sources.entries`` list. Both lists may be empty when a response \
is a direct inference from named evidence elsewhere — never cite an \
index you have not verified.

Confidence levels mirror the upstream artefacts: HIGH (evidence \
directly motivates this response), MEDIUM (evidence is suggestive), \
LOW (circumstantial), INFERRED (your conclusion, not stated).

Structure of the output:
* ``rows`` — flat list of 15 to 20 :class:`ObjectionRow` records. \
Fields: ``category``, ``objection``, ``underlying_concern``, \
``response``, ``knowledge_excerpt_refs``, ``briefing_source_refs``, \
``escalation_path``, ``confidence``.
* ``gaps`` — array of strings describing what you tried to answer \
but could not (e.g. \"no datasheet detail on Netropy 100G jitter \
floor\").

Output format: return ONLY a valid JSON object matching the \
``ObjectionsRegister`` schema. No commentary, no markdown fences, \
no surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The operator-approved briefing and the upstream product mapping are \
provided below as JSON. Compile a 15-to-20-row objections register \
grounded strictly in those inputs and the supplied ANS knowledge \
bundle.

Approved briefing JSON:
{briefing_json}

Upstream product mapping JSON:
{product_mapping_json}

ANS knowledge bundle:
{knowledge_bundle}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which \
objection angles to emphasise, not as permission to invent product \
capabilities, case-study outcomes, or company-side context the \
approved briefing and mapping do not support):
{user_context}"""


# ---------------------------------------------------------------------------
# ObjectionsWriter
# ---------------------------------------------------------------------------

class ObjectionsWriter(BaseAgent):
    """Stage-2 objections writer agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the agent reasons over the
    briefing JSON, the mapping JSON and the knowledge bundle passed
    in as prompt inputs, never the web.

    ``name = "objections_writer"`` matches the existing
    :mod:`backend.agents.limits` registry key and the handover's
    convention for the three Stage 2 writer names.
    """

    name: ClassVar[str] = "objections_writer"
    role: ClassVar[str] = "writer_model"
    output_model: ClassVar[type] = ObjectionsRegister

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
