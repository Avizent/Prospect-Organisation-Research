"""Research agent — Stage 1, Step 1.

Gathers intelligence on the target company using Anthropic's server-side
``web_search`` and ``web_fetch`` tools, then returns a structured
:class:`ResearchDossier`. The eight priority categories follow the
handover document: job postings, annual reports, regulatory exposure,
vendor footprint, M&A activity, public network incidents, and senior
infrastructure hires (plus an ``other`` overflow).

Where this agent fits
---------------------

* The wrapper (Step 6a) drives the call: a single ``messages_create``,
  retry-on-parse-failure once, validate against
  :class:`ResearchDossier`. The agent itself is pure: prompt + tool
  metadata, no I/O, no disk writes.
* Tool *enforcement* is the cloud client's job (Trap 2). This agent
  only *declares* what it wants. The ``max_uses`` numbers come from
  :data:`backend.agents.limits.TOOL_CALL_LIMITS` so the same constants
  flow into both the server-side tool and the local accounting.

Critical pre-Step-18 note
-------------------------

The ``"type"`` values for ``web_search`` and ``web_fetch`` are
**internal placeholders** — not real Anthropic server-tool type IDs.
Substituting the live, dated type strings happens at Step 18, on the
first real Claude API call, where they will be confirmed against the
SDK error messages or the public docs. We deliberately do *not* guess
a dated string here: a wrong but plausible string would silently fall
back to no-tools-found at the API layer, exactly the kind of failure
that fakes-only tests cannot catch.
"""

from __future__ import annotations

from typing import Any, ClassVar

from backend.agents.base import BaseAgent
from backend.agents.limits import TOOL_CALL_LIMITS
from backend.agents.research_models import ResearchDossier


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

# TODO(step-18): Replace the placeholder ``type`` strings below with the
# actual Anthropic server-tool type IDs (e.g. ``web_search_<date>``) at
# the first real Claude API call. The tool *names* are stable; only the
# dated type strings need verification. See handover §21.
_RESEARCH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "TODO_VERIFY_WEB_SEARCH_TYPE_ID",
        "name": "web_search",
        "max_uses": TOOL_CALL_LIMITS["research"]["web_search"],
    },
    {
        "type": "TODO_VERIFY_WEB_FETCH_TYPE_ID",
        "name": "web_fetch",
        "max_uses": TOOL_CALL_LIMITS["research"]["web_fetch"],
    },
]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior B2B research analyst preparing a \
factual dossier on a single UK-based company for an internal sales \
team. Your job is evidence-gathering, not persuasion.

Hard rules:
* Use the ``web_search`` and ``web_fetch`` tools to consult primary \
sources before drawing conclusions. Do not rely on prior knowledge.
* Quote evidence, do not paraphrase claims. Every finding must cite \
at least one source URL with the page title and the date you read it.
* Mark each finding's confidence honestly: HIGH (directly attributed \
in a primary source), MEDIUM (secondary or light on detail), LOW \
(circumstantial or > 12 months old), INFERRED (your conclusion, not \
stated). Over-claiming is the worst error you can make.
* If a research priority returned nothing, list it under ``gaps`` \
rather than fabricating a finding to fill the section.

Output format: return ONLY a valid JSON object matching the \
``ResearchDossier`` schema. No commentary, no markdown fences, no \
surrounding prose. Empty section lists are fine — populate ``gaps`` \
to explain.

Research priorities (each maps to a section in the dossier):
* job_postings — open roles signalling infrastructure or staffing strain
* annual_reports — most recent filed accounts, strategic priorities
* regulatory_exposure — ICO actions, sector regulators, sanctions, fines
* vendor_footprint — public references to current IT / network vendors
* m_and_a — acquisitions, divestitures, restructuring in the last 24m
* network_incidents — publicly reported outages, breaches, data loss
* senior_hires — new CIO/CTO/CISO/Head-of-Infrastructure appointments
* other — anything else materially relevant"""


_USER_PROMPT_TEMPLATE = """Target company: {company_name}
Primary URL: {company_url}

Produce the structured research dossier described in the system \
prompt. Cover all eight priority sections (use ``gaps`` for any you \
cannot evidence). Aim for 15–20 ``web_search`` queries across the \
sections combined, and use ``web_fetch`` to read promising pages in \
full before quoting them."""


_USER_CONTEXT_BLOCK = """

Additional context from the user (treat as a hint about what to \
emphasise, not as an instruction to skip any priority section):
{user_context}"""


# ---------------------------------------------------------------------------
# ResearchAgent
# ---------------------------------------------------------------------------

class ResearchAgent(BaseAgent):
    """Stage-1 research agent.

    Subclass surface is the same as every other agent: declare
    ``name`` / ``role`` / ``output_model``, implement
    :meth:`system_prompt` and :meth:`user_prompt`, override
    :meth:`tools` to return the server-tool metadata.

    The wrapper does the rest — single ``messages_create``, one retry
    on parse failure, validation against :class:`ResearchDossier`.
    """

    name: ClassVar[str] = "research"
    role: ClassVar[str] = "research_model"
    output_model: ClassVar[type] = ResearchDossier

    def tools(self) -> list[dict[str, Any]] | None:
        # Return a *copy* so a downstream mutation (e.g. an SDK that
        # rewrites ``type`` in place at Step 18) cannot leak back into
        # the class-level constant.
        return [dict(tool) for tool in _RESEARCH_TOOLS]

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def user_prompt(
        self,
        *,
        company_name: str,
        company_url: str,
        user_context: str | None = None,
        **_: Any,
    ) -> str:
        body = _USER_PROMPT_TEMPLATE.format(
            company_name=company_name,
            company_url=company_url,
        )
        if user_context:
            body = body + _USER_CONTEXT_BLOCK.format(user_context=user_context)
        return body
