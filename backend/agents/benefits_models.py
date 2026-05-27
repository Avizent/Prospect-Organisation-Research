"""Pydantic schema for the benefits writer's structured output.

Stage 2, Step 15 of the pipeline produces a :class:`BenefitsBrief` —
the headline deliverable feeding the benefits PDF. The writer is the
first of three Stage 2 writers (handover §9.2 step 8); FAQ and
objections follow in later steps and will share the same enum
re-use / non-negative-refs convention.

Design choices worth calling out
--------------------------------

* **Three named sections, not a free list.** Executive summary,
  technical fit, and business case are the audience-tiered structure
  the handover (line 243) and build prompt (line 385) call out. Naming
  them as fixed fields means the schema enforces the tiering instead
  of relying on prompt discipline; reordering would be a future
  schema bump rather than a soft prompt change.

* **``Audience`` is a closed three-value enum.** Locks the three
  reader profiles (``CTO_CIO``, ``ENGINEERS``, ``IT_DIRECTOR``)
  matching the handover. A hallucinated audience (``"CFO"``) fails
  validation at parse time and the wrapper's retry loop fires.

* **``Confidence`` reused from :mod:`backend.agents.research_models`.**
  Same rationale as the mapping schema — a single source of truth for
  the four-level scale across every artefact.

* **Two ref lists per claim.** ``knowledge_excerpt_refs`` point into
  the upstream :attr:`ProductMapping.knowledge_excerpts` list;
  ``briefing_source_refs`` point into the upstream
  :attr:`Briefing.sources.entries`. They are semantically different
  pools (ANS-side product evidence vs. company-side source evidence)
  so collapsing them into a single tagged list would lose information
  the renderer needs.

* **Refs are validated non-negative-only at the writer schema
  level.** The writer never sees the upstream artefacts as parsed
  objects (only as opaque JSON text per the agent's signature), so
  cross-artefact range validation cannot fire here. The critic agent
  (later step) is the appropriate validator; this matches the soft
  contract already used for :attr:`NeedProductMatch.need_priority`
  in :mod:`backend.agents.mapping_models`. A model-level validator
  re-checks negativity at the top level so a future loosening of the
  field-level guard still catches negatives end-to-end.

* **``body`` is non-empty on every section.** A section with zero
  claims is meaningless — drop the section or move its content
  elsewhere. Mirrors :attr:`NeedProductMatch.products` ``min_length=1``.

* **``gaps`` is optional with default ``[]``.** Surfacing honest
  coverage admissions ("no datasheet detail on Netropy 100G jitter
  floor") is the same self-fence used by every other Stage 1/2
  artefact.

This module is import-safe for every test in ``tests/agents/`` —
it imports only the standard library, Pydantic, and the
:class:`Confidence` enum from a sibling models module. No SDK,
no Keychain, no I/O, no SQLAlchemy.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from backend.agents.research_models import Confidence  # single source of truth

__all__ = [
    "Confidence",
    "Audience",
    "BenefitClaim",
    "BenefitsSection",
    "BenefitsBrief",
]


# ---------------------------------------------------------------------------
# Audience — closed three-value enum
# ---------------------------------------------------------------------------

class Audience(str, Enum):
    """The closed set of reader profiles the benefits brief targets.

    Mirrors the handover's tiered structure: executive summary speaks
    to the CTO/CIO, technical fit speaks to the engineering audience,
    business case speaks to the IT Director. Widen the enum (and the
    test that pins it) only when a stakeholder asks for a new tier.
    """

    CTO_CIO = "CTO_CIO"
    ENGINEERS = "ENGINEERS"
    IT_DIRECTOR = "IT_DIRECTOR"


# ---------------------------------------------------------------------------
# BenefitClaim
# ---------------------------------------------------------------------------

class BenefitClaim(BaseModel):
    """One concrete benefit statement targeted at a section's audience.

    ``claim`` is the headline; ``detail`` is the supporting body;
    ``why_it_matters`` is the so-what tailored to the section's
    reader. Citation refs are positional indices into the upstream
    bundles — the writer never sees those bundles as parsed objects,
    so range validation is deferred to the critic.
    """

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=400)
    detail: str = Field(min_length=1, max_length=2000)
    why_it_matters: str = Field(min_length=1, max_length=800)
    knowledge_excerpt_refs: list[int] = Field(default_factory=list)
    briefing_source_refs: list[int] = Field(default_factory=list)
    confidence: Confidence

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "BenefitClaim":
        for idx in self.knowledge_excerpt_refs:
            if idx < 0:
                raise ValueError(
                    f"knowledge_excerpt_refs must be >= 0; got {idx}"
                )
        for idx in self.briefing_source_refs:
            if idx < 0:
                raise ValueError(
                    f"briefing_source_refs must be >= 0; got {idx}"
                )
        return self


# ---------------------------------------------------------------------------
# BenefitsSection
# ---------------------------------------------------------------------------

class BenefitsSection(BaseModel):
    """One audience-tiered section of the brief.

    ``audience`` is the closed-enum tier; ``heading`` is the
    free-text section title; ``summary`` is a one-paragraph tl;dr;
    ``body`` is the non-empty list of claims. Forbidding an empty
    ``body`` means a "section" without claims is rejected at parse
    time — that would be an editorial bug, not a real artefact.
    """

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=1, max_length=200)
    audience: Audience
    summary: str = Field(min_length=1, max_length=600)
    body: list[BenefitClaim] = Field(min_length=1)


# ---------------------------------------------------------------------------
# BenefitsBrief
# ---------------------------------------------------------------------------

class BenefitsBrief(BaseModel):
    """Top-level payload returned by the benefits writer agent.

    Mirrors the approved briefing's ``company_name`` / ``company_url``
    so the on-disk artefact (when persistence lands in a later step)
    is self-describing without the briefing loaded. ``written_at`` is
    set by the agent; a future orchestrator step may override it
    deterministically (same shape as ``mapped_at`` in
    :class:`ProductMapping`).

    The three audience-tiered sections are fixed by name and order:
    executive summary → technical fit → business case. The
    audience-enum value carried by each section is also fixed at the
    schema level — the writer cannot mismatch ``executive_summary``
    with ``audience = ENGINEERS`` because the validator pins it.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    written_at: date

    executive_summary: BenefitsSection
    technical_fit: BenefitsSection
    business_case: BenefitsSection

    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sections_have_correct_audience(self) -> "BenefitsBrief":
        """Pin each named section to its audience.

        The handover names the tiering: executive summary → CTO/CIO,
        technical fit → engineers, business case → IT Director. The
        schema makes that contract load-bearing — a writer that emits
        a swapped audience fails parse, and the wrapper retries.
        """
        expected = (
            ("executive_summary", self.executive_summary, Audience.CTO_CIO),
            ("technical_fit", self.technical_fit, Audience.ENGINEERS),
            ("business_case", self.business_case, Audience.IT_DIRECTOR),
        )
        for field_name, section, want in expected:
            if section.audience is not want:
                raise ValueError(
                    f"{field_name}.audience must be {want.value!r}, "
                    f"got {section.audience.value!r}"
                )
        return self

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "BenefitsBrief":
        """Re-check ref negativity at the top level.

        Field-level non-negativity is already enforced inside
        :class:`BenefitClaim`; re-checking here means a future
        loosening of the per-claim guard still catches negatives
        before the artefact leaves the agent. Range-against-upstream
        validation is deferred to the critic — see the module
        docstring.
        """
        for section_name, section in (
            ("executive_summary", self.executive_summary),
            ("technical_fit", self.technical_fit),
            ("business_case", self.business_case),
        ):
            for i, claim in enumerate(section.body):
                for idx in claim.knowledge_excerpt_refs:
                    if idx < 0:
                        raise ValueError(
                            f"{section_name}.body[{i}]"
                            f".knowledge_excerpt_refs: index {idx} is "
                            "negative"
                        )
                for idx in claim.briefing_source_refs:
                    if idx < 0:
                        raise ValueError(
                            f"{section_name}.body[{i}]"
                            f".briefing_source_refs: index {idx} is "
                            "negative"
                        )
        return self
