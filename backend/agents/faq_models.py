"""Pydantic schema for the FAQ writer's structured output.

Stage 2, Step 16 of the pipeline produces an :class:`FAQDocument` —
the headline deliverable feeding the FAQ PDF. The FAQ writer is the
second of three Stage 2 writers (handover §9.2 step 8); benefits
landed in Step 15 and objections will follow.

Design choices worth calling out
--------------------------------

* **Five named categories, flat list of entries.** Handover §9.4 step
  8 says "12-15 Q&A pairs grouped: About ANS, Products,
  Implementation, Commercial, Support". A flat ``entries`` list with
  a closed :class:`FAQCategory` enum on each entry is the natural
  shape — the renderer groups by category at print time, and the
  schema keeps cardinality enforcement (``min_length=12``,
  ``max_length=15``) in one place rather than scattered across five
  per-category sections.

* **``FAQCategory`` is a closed five-value enum.** Locks the
  category names the handover pins. A hallucinated category
  (``"PRICING"``) fails validation at parse time and the wrapper's
  retry loop fires.

* **No hard per-category minimum.** With only 12-15 total entries
  across five categories, a per-category floor of one would force
  the writer to fabricate a thin-coverage category rather than admit
  it via ``gaps``. The prompt requests balanced coverage; ``gaps``
  carries the honesty when knowledge is thin for a category. A
  future tightening to a model-level "every category present" rule
  is a soft schema bump.

* **``Confidence`` reused from :mod:`backend.agents.research_models`.**
  Same rationale as the benefits / mapping schemas — a single source
  of truth for the four-level scale across every artefact.

* **Two ref lists per entry.** ``knowledge_excerpt_refs`` point into
  the upstream :attr:`ProductMapping.knowledge_excerpts` list;
  ``briefing_source_refs`` point into the upstream
  :attr:`Briefing.sources.entries`. They are semantically different
  pools (ANS-side product evidence vs. company-side source evidence)
  so collapsing them into a single tagged list would lose
  information the renderer needs. Same shape as
  :class:`backend.agents.benefits_models.BenefitClaim`.

* **Refs are validated non-negative-only at the writer schema
  level.** The writer never sees the upstream artefacts as parsed
  objects (only as opaque JSON text per the agent's signature), so
  cross-artefact range validation cannot fire here. The critic agent
  (later step) is the appropriate validator; this matches the soft
  contract already used by :class:`BenefitClaim` and
  :class:`NeedProductMatch`. A model-level validator re-checks
  negativity at the top level so a future loosening of the
  field-level guard still catches negatives end-to-end.

* **``entries`` is cardinality-bounded at 12-15.** Mirrors the
  handover spec exactly. Below 12 or above 15 fails parse so the
  writer cannot accidentally ship a brief that misses the contract.

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
    "FAQCategory",
    "FAQEntry",
    "FAQDocument",
]


# ---------------------------------------------------------------------------
# FAQCategory — closed five-value enum
# ---------------------------------------------------------------------------

class FAQCategory(str, Enum):
    """The closed set of FAQ groupings the handover pins.

    Mirrors handover §9.4 step 8 wording: "12-15 Q&A pairs grouped:
    About ANS, Products, Implementation, Commercial, Support". Widen
    the enum (and the test that pins it) only when a stakeholder asks
    for a new category.
    """

    ABOUT_ANS = "ABOUT_ANS"
    PRODUCTS = "PRODUCTS"
    IMPLEMENTATION = "IMPLEMENTATION"
    COMMERCIAL = "COMMERCIAL"
    SUPPORT = "SUPPORT"


# ---------------------------------------------------------------------------
# FAQEntry
# ---------------------------------------------------------------------------

class FAQEntry(BaseModel):
    """One Q&A pair targeting a single FAQ category.

    ``question`` is the headline; ``answer`` is the response body;
    ``category`` slots it into one of the five enum buckets. Citation
    refs are positional indices into the upstream bundles — the
    writer never sees those bundles as parsed objects, so range
    validation is deferred to the critic.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=400)
    answer: str = Field(min_length=1, max_length=2000)
    category: FAQCategory
    knowledge_excerpt_refs: list[int] = Field(default_factory=list)
    briefing_source_refs: list[int] = Field(default_factory=list)
    confidence: Confidence

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "FAQEntry":
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
# FAQDocument
# ---------------------------------------------------------------------------

class FAQDocument(BaseModel):
    """Top-level payload returned by the FAQ writer agent.

    Mirrors the approved briefing's ``company_name`` / ``company_url``
    so the on-disk artefact (when persistence lands in a later step)
    is self-describing without the briefing loaded. ``written_at`` is
    set by the agent; a future orchestrator step may override it
    deterministically (same shape as ``mapped_at`` in
    :class:`ProductMapping` and ``written_at`` in
    :class:`BenefitsBrief`).

    ``entries`` is a flat list of 12-15 Q&A pairs. Each entry's
    ``category`` slots it into one of the five
    :class:`FAQCategory` values for the renderer; the schema does
    NOT enforce a per-category minimum (see module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    written_at: date

    entries: list[FAQEntry] = Field(min_length=12, max_length=15)

    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "FAQDocument":
        """Re-check ref negativity at the top level.

        Field-level non-negativity is already enforced inside
        :class:`FAQEntry`; re-checking here means a future loosening
        of the per-entry guard still catches negatives before the
        artefact leaves the agent. Range-against-upstream validation
        is deferred to the critic — see the module docstring.
        """
        for i, entry in enumerate(self.entries):
            for idx in entry.knowledge_excerpt_refs:
                if idx < 0:
                    raise ValueError(
                        f"entries[{i}].knowledge_excerpt_refs: "
                        f"index {idx} is negative"
                    )
            for idx in entry.briefing_source_refs:
                if idx < 0:
                    raise ValueError(
                        f"entries[{i}].briefing_source_refs: "
                        f"index {idx} is negative"
                    )
        return self
