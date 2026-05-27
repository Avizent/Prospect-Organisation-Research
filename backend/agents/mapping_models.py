"""Pydantic schema for the product-mapping agent's structured output.

Stage 2, Step 1 of the pipeline produces a :class:`ProductMapping` —
the bridge between the approved :class:`Briefing` and the three Stage 2
writers. The mapping agent binds each :class:`RankedNeed` in the
approved briefing to one or more concrete ANS products and pre-filters
the knowledge base excerpts the writers will need.

Design choices worth calling out
--------------------------------

* **``ProductLine`` is a closed two-value enum.** Today's
  ``ans_knowledge/products/`` directory contains exactly two markdown
  files (``emulators.md`` and ``traffic_generators.md``). Closing the
  enum to those two values means a hallucinated third product line
  (e.g. ``"firewalls"``) is caught at schema-validation time and the
  retry loop fires — instead of leaking a non-existent product into a
  writer prompt. Widening the enum is a one-line change when ANS adds
  a new product line, and the existing tests pin the current shape.

* **``KnowledgeSource`` is a closed seven-value enum.** Same reasoning
  as ``ProductLine``: it mirrors the on-disk inventory under
  ``ans_knowledge/`` so the model can only reference excerpts that
  actually exist. The values are the relative paths from
  ``ans_knowledge/`` (e.g. ``"products/emulators.md"``,
  ``"case_studies.md"``) which makes them human-readable in the
  artefact and trivially mappable back to disk by the future knowledge
  loader.

* **``Confidence`` reused from :mod:`backend.agents.research_models`.**
  Single source of truth — the four levels (HIGH / MEDIUM / LOW /
  INFERRED) carry the same meaning whether the artefact is a finding,
  a contact, a needs call, a briefing claim, or a product mapping.

* **``NeedProductMatch.need_priority`` is a positional reference.** It
  points back to :attr:`Briefing.opportunity.ranked_needs[*].priority`
  (1 = highest, ascending). The schema does not deep-validate that the
  priority exists in the input briefing — that would require either
  passing the parsed :class:`Briefing` in (which conflicts with the
  agent's JSON-string convention) or post-validating in the agent
  layer. Soft contract enforced by the prompt; the critic agent (later
  step) is the appropriate validator.

* **Empty ``matches`` is legal.** A briefing whose
  :attr:`Opportunity.ranked_needs` list is empty (e.g. dossier was
  thin) yields a :class:`ProductMapping` with empty ``matches``;
  ``unmatched_needs`` may also be empty in that case. Surfacing
  "nothing to map" via the schema beats forcing the agent to invent
  matches just to satisfy a non-empty constraint.

* **``knowledge_excerpt_refs`` are positionally indexed.** Each
  :class:`ProductReference.knowledge_excerpt_refs` value is a 0-based
  index into :attr:`ProductMapping.knowledge_excerpts`. The model-level
  validator walks every reference and raises if any index is out of
  range — silently dropping out-of-range refs would hide a model
  hallucination from the downstream writers.

This module is import-safe for every test in ``tests/runaway/`` and
``tests/agents/`` — it imports only the standard library, Pydantic,
and the :class:`Confidence` enum from a sibling models module. No SDK,
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
    "ProductLine",
    "KnowledgeSource",
    "KnowledgeExcerptRef",
    "ProductReference",
    "NeedProductMatch",
    "UnmatchedNeed",
    "ProductMapping",
]


# ---------------------------------------------------------------------------
# ProductLine — closed enum over ans_knowledge/products/*.md
# ---------------------------------------------------------------------------

class ProductLine(str, Enum):
    """The closed set of ANS product lines today.

    Mirrors the files under ``ans_knowledge/products/``. Lower-case
    underscore form so values render cleanly in JSON without quoting
    rules surprising the renderer. Widen this enum (and the test that
    pins it) when ANS adds a new product line.
    """

    EMULATORS = "emulators"
    TRAFFIC_GENERATORS = "traffic_generators"


# ---------------------------------------------------------------------------
# KnowledgeSource — closed enum over ans_knowledge/*.md
# ---------------------------------------------------------------------------

class KnowledgeSource(str, Enum):
    """The closed set of ANS knowledge-base files today.

    Values are the relative paths from ``ans_knowledge/``. The future
    knowledge loader (a later step) can map a value back to disk with
    a single ``ans_knowledge / value`` join — no string surgery needed.
    """

    PRODUCTS_EMULATORS = "products/emulators.md"
    PRODUCTS_TRAFFIC_GENERATORS = "products/traffic_generators.md"
    COMPANY = "company.md"
    CASE_STUDIES = "case_studies.md"
    COMPETITIVE_LANDSCAPE = "competitive_landscape.md"
    KNOWN_OBJECTIONS = "known_objections.md"
    PRICING_TIERS = "pricing_tiers.md"


# ---------------------------------------------------------------------------
# KnowledgeExcerptRef
# ---------------------------------------------------------------------------

class KnowledgeExcerptRef(BaseModel):
    """A pointer to a single excerpt of ANS knowledge.

    Stage 2 writers receive a pre-filtered bundle built from these
    refs — the mapping agent decides *which* excerpts are relevant and
    *why*. The excerpt body itself is not stored here; the future
    knowledge loader resolves ``source_file`` + ``heading`` against
    the on-disk markdown.
    """

    model_config = ConfigDict(extra="forbid")

    source_file: KnowledgeSource
    heading: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=600)


# ---------------------------------------------------------------------------
# ProductReference
# ---------------------------------------------------------------------------

class ProductReference(BaseModel):
    """A single ANS product bound to a need, with knowledge pointers.

    ``product_name`` is free text (e.g. ``"Netropy 100G"``,
    ``"Linktropy Mini-G"``) — closing it to an enum would force a
    schema bump for every new SKU. ``product_line`` is the closed
    enum and is the load-bearing self-fence: a hallucinated product
    line fails validation, a hallucinated SKU within a real product
    line is a soft error the critic agent catches later.
    """

    model_config = ConfigDict(extra="forbid")

    product_line: ProductLine
    product_name: str = Field(min_length=1, max_length=200)
    knowledge_excerpt_refs: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "ProductReference":
        for idx in self.knowledge_excerpt_refs:
            if idx < 0:
                raise ValueError(
                    f"knowledge_excerpt_refs must be >= 0; got {idx}"
                )
        return self


# ---------------------------------------------------------------------------
# NeedProductMatch
# ---------------------------------------------------------------------------

class NeedProductMatch(BaseModel):
    """One match: a ranked need bound to >= 1 ANS product.

    ``need_priority`` is the positional reference back to
    :attr:`Briefing.opportunity.ranked_needs[*].priority`. ``products``
    must be non-empty — a "match" with zero products would be an
    :class:`UnmatchedNeed` instead.

    ``use_case_framing`` is the concrete framing the handover calls
    out (line 240: "need→product mappings with concrete use-case
    framing"). ``why_this_fits`` is the rationale the writers will
    quote from when motivating the recommendation in the deliverable.
    """

    model_config = ConfigDict(extra="forbid")

    need_priority: int = Field(ge=1)
    need_summary: str = Field(min_length=1, max_length=300)
    products: list[ProductReference] = Field(min_length=1)
    use_case_framing: str = Field(min_length=1, max_length=2000)
    why_this_fits: str = Field(min_length=1, max_length=2000)
    confidence: Confidence


# ---------------------------------------------------------------------------
# UnmatchedNeed
# ---------------------------------------------------------------------------

class UnmatchedNeed(BaseModel):
    """A ranked need the agent could not bind to any ANS product.

    Surfacing unmatched needs explicitly beats silently dropping them
    — the writers can then either acknowledge the gap honestly or, if
    the operator decides to course-correct, regenerate against a
    revised briefing.
    """

    model_config = ConfigDict(extra="forbid")

    need_priority: int = Field(ge=1)
    need_summary: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# ProductMapping
# ---------------------------------------------------------------------------

class ProductMapping(BaseModel):
    """Top-level payload returned by the product mapping agent.

    Mirrors the approved briefing's ``company_name`` / ``company_url``
    so the on-disk artefact is self-describing without the briefing
    loaded.

    The :meth:`_check_excerpt_refs_in_range` validator walks every
    :class:`ProductReference` and raises if any
    ``knowledge_excerpt_refs`` value points outside
    :attr:`knowledge_excerpts`. Out-of-range refs would render as
    broken citations in the writer prompts; silently dropping them
    would hide the error.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    mapped_at: date

    matches: list[NeedProductMatch] = Field(default_factory=list)
    unmatched_needs: list[UnmatchedNeed] = Field(default_factory=list)
    knowledge_excerpts: list[KnowledgeExcerptRef] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_excerpt_refs_in_range(self) -> "ProductMapping":
        n = len(self.knowledge_excerpts)
        for i, match in enumerate(self.matches):
            for j, product in enumerate(match.products):
                for idx in product.knowledge_excerpt_refs:
                    # Field-level non-negativity is enforced on
                    # ProductReference; re-check here so a future
                    # loosening of that guard still catches negatives
                    # at the top level.
                    if idx < 0:
                        raise ValueError(
                            f"matches[{i}].products[{j}]"
                            f".knowledge_excerpt_refs: index {idx} is "
                            "negative"
                        )
                    if idx >= n:
                        raise ValueError(
                            f"matches[{i}].products[{j}]"
                            f".knowledge_excerpt_refs: index {idx} out "
                            f"of range for knowledge_excerpts (length {n})"
                        )
        return self
