"""Stage 2 critic agent — Stage 2, Step 19.

Reviews the three Stage 2 writer artefacts (benefits brief, FAQ
register, objections register) against the operator-approved briefing
and the ANS knowledge bundle, and produces a :class:`Stage2CriticReport`
— a normalised list of issues plus an overall readiness verdict.

The agent is **pure**: it returns the validated
:class:`Stage2CriticReport` Pydantic object. The orchestrator (a later
step) is responsible for persisting the report and deciding whether to
trigger a revision pass; the agent itself never touches the DB, the
filesystem, or the network.

Why text-in, JSON-out (no tools)
--------------------------------
Every input the critic needs has already been vetted — the briefing is
operator-approved, the mapping is computed from it, the writer
artefacts are the three drafts under review, and the knowledge bundle
is a finite set of internal markdown files. Allowing ``web_search`` or
``web_fetch`` would let the critic introduce new evidence the operator
has never seen and could not have approved. The closed-world contract
is the point.

Inputs
------
* ``company_name`` — for the prompt header (and round-tripped into
  the report so the artefact is self-describing).
* ``company_url`` — for the prompt header (likewise).
* ``briefing_json`` — the *approved* :class:`Briefing` serialised as
  JSON text. The source-of-truth for company-side evidence; a writer
  claim about the target company that is not supported here is flagged
  ``UNSUPPORTED_COMPANY_CLAIM``.
* ``product_mapping_json`` — the upstream :class:`ProductMapping`
  serialised as JSON text. Threaded so the critic can see which
  knowledge excerpts the mapping pre-selected; this is the index the
  writers' ``knowledge_excerpt_refs`` resolve against.
* ``benefits_json`` — the :class:`BenefitsBrief` serialised as JSON.
* ``faq_json`` — the :class:`FAQRegister` serialised as JSON.
* ``objections_json`` — the :class:`ObjectionsRegister` serialised as
  JSON.
* ``knowledge_bundle`` — the relevant ANS knowledge base content,
  pre-assembled by the caller as a single string. Same opaque-blob
  contract as the writers and the mapping agent; a writer claim about
  an ANS product that is not supported here is flagged
  ``UNSUPPORTED_PRODUCT_CLAIM``.
* ``user_context`` (optional) — operator hint, surfaced verbatim.

Why a single ``"critic"`` registry key
--------------------------------------
``MAX_TOKENS["critic"] = 2000`` already exists in
:mod:`backend.agents.limits` and was scoped for this agent from the
outset (Step 1 stubbed ``critic.py`` and reserved the key). Re-using
the existing entry rather than minting a parallel ``"stage2_critic"``
key keeps the trap registry small and removes a foot-gun the day a
future Stage 3 critic lands.

Where this agent fits
---------------------
* The base wrapper (Step 6a) drives the call: a single
  ``messages_create``, retry-on-parse-failure once, validate against
  :class:`Stage2CriticReport`. No tool-use loop.
* ``role`` resolves to ``critic_model`` per ``config.yaml`` — distinct
  from the writer and research model ids, matching the handover's
  choice of a smaller/faster model for the review pass.
* ``MAX_TOKENS["critic"] = 2000`` is already in
  :mod:`backend.agents.limits`.

Implemented in Step 19.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.critic_models import Stage2CriticReport


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior ANS pre-sales reviewer auditing \
three writer artefacts for a single UK-based target company before \
they ship to the operator. The operator-approved briefing for the \
target is provided as JSON. The upstream product mapping \
(need→product bindings, pre-filtered knowledge excerpts) is provided \
as JSON. The three writer artefacts under review — the benefits brief \
(BENEFITS), the FAQ register (FAQ) and the objections register \
(OBJECTIONS) — are provided as JSON. The ANS knowledge base (product \
datasheets, case studies, company background, competitive landscape, \
known objections, pricing tiers) is provided as a single bundle. \
Your job is to surface issues, not to rewrite the artefacts.

Hard rules:
* Stay strictly within the approved briefing, the product mapping, \
the three writer artefacts and the supplied knowledge bundle. Do not \
introduce facts from outside those inputs — your role is to audit \
what is in front of you, not to research the target afresh.
* Run four checks on every writer artefact: (1) claims about the \
target company that are not supported by the approved briefing — \
category UNSUPPORTED_COMPANY_CLAIM; (2) claims about ANS products or \
services that are not supported by the knowledge bundle — category \
UNSUPPORTED_PRODUCT_CLAIM; (3) factual inconsistencies, either \
within one artefact or between artefacts — category \
FACTUAL_INCONSISTENCY; (4) off-brand tone, hype language, or framing \
that does not match a senior pre-sales voice — category \
OFF_BRAND_TONE. Two further categories surface naturally: \
CITATION_REF_ISSUE for broken or out-of-range refs into the briefing \
sources or the mapping's knowledge excerpts, and EVIDENCE_GAP for \
material the artefacts lean on but the inputs cover only thinly. \
OTHER is a last-resort hatch — try every other category first.
* Each issue records exactly one artefact (BENEFITS, FAQ or \
OBJECTIONS), a human-readable ``locator`` into that artefact \
(e.g. ``executive_summary.body[0].claim`` or ``rows[7].response``), \
a closed severity (BLOCKING, WARNING, INFO), a closed category, a \
``description`` and an optional ``suggested_fix``. Do NOT split one \
issue across two records to make the verdict look better — one \
issue, one record.
* Severity rules: BLOCKING is reserved for issues an operator must \
fix before shipping (a fabricated product capability, a contradiction \
that misleads the prospect, a claim about the target with no support \
in the briefing). WARNING is reserved for issues an operator should \
address but the artefact would survive (a soft tone slip, a citation \
that points to a thin source). INFO is noise-floor observations that \
do not affect shipping.
* The overall ``verdict`` must agree with the severity distribution: \
READY means no BLOCKING and no WARNING issues; READY_WITH_WARNINGS \
means no BLOCKING issues and at least one WARNING issue; \
NEEDS_REVISION means at least one BLOCKING issue. Stamping a verdict \
that contradicts the issue list fails schema validation.
* ``summary`` is a one-paragraph operator-facing read of the verdict \
— why you think the artefacts are (or are not) ready. Even a READY \
verdict requires a summary so the operator can spot a \
confidently-wrong reviewer.
* If you tried to assess something but the inputs would not let you \
finish (e.g. "could not verify Netropy 100G jitter floor without a \
datasheet"), record an honest entry under top-level ``gaps`` rather \
than fabricating coverage.

Output format: return ONLY a valid JSON object matching the \
``Stage2CriticReport`` schema. No commentary, no markdown fences, \
no surrounding prose."""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

The operator-approved briefing, the upstream product mapping, the \
three writer artefacts and the ANS knowledge bundle are provided \
below. Audit the three writer artefacts against those inputs and \
return a Stage2CriticReport.

Approved briefing JSON:
{briefing_json}

Upstream product mapping JSON:
{product_mapping_json}

Benefits brief JSON (artefact = BENEFITS):
{benefits_json}

FAQ register JSON (artefact = FAQ):
{faq_json}

Objections register JSON (artefact = OBJECTIONS):
{objections_json}

ANS knowledge bundle:
{knowledge_bundle}"""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about which review \
angles to emphasise, not as permission to introduce facts the \
approved briefing, the mapping, the three writer artefacts and the \
knowledge bundle do not support):
{user_context}"""


# ---------------------------------------------------------------------------
# Stage2CriticAgent
# ---------------------------------------------------------------------------

class Stage2CriticAgent(BaseAgent):
    """Stage 2 critic agent.

    Subclass surface mirrors every other agent: declare ``name`` /
    ``role`` / ``output_model``, implement :meth:`system_prompt` and
    :meth:`user_prompt`. No :meth:`tools` override — the base class
    default of ``None`` is correct: the critic reasons over the
    briefing JSON, the mapping JSON, the three writer JSONs and the
    knowledge bundle passed in as prompt inputs, never the web.

    ``name = "critic"`` matches the existing
    :mod:`backend.agents.limits` registry key (reserved at Step 1 for
    exactly this agent). ``role = "critic_model"`` resolves to a
    smaller/faster model id per ``config.yaml``.
    """

    name: ClassVar[str] = "critic"
    role: ClassVar[str] = "critic_model"
    output_model: ClassVar[type] = Stage2CriticReport

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        briefing_json: str,
        product_mapping_json: str,
        benefits_json: str,
        faq_json: str,
        objections_json: str,
        knowledge_bundle: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
            briefing_json=briefing_json,
            product_mapping_json=product_mapping_json,
            benefits_json=benefits_json,
            faq_json=faq_json,
            objections_json=objections_json,
            knowledge_bundle=knowledge_bundle,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(user_context=user_context)
        return body
