"""Pydantic schema for the objections writer's structured output.

Stage 2, Step 17 of the pipeline produces an :class:`ObjectionsRegister`
— the headline deliverable feeding the objections XLSX. The objections
writer is the third of three Stage 2 writers (handover §9.2 step 8);
benefits landed in Step 15 and FAQ in Step 16.

Design choices worth calling out
--------------------------------

* **Six-column row shape, fixed.** Handover §9.4 step 8 / build
  prompt step 8 both pin the XLSX columns exactly: Category |
  Objection | Underlying concern | Response | Supporting evidence |
  Escalation path. The schema names match those columns one-to-one,
  with the single nuance that "Supporting evidence" is split into
  two ref lists (see below).

* **``ObjectionCategory`` is a closed eight-value enum.** The
  handover does NOT name a closed list, so the values are chosen
  from standard B2B-sales objection taxonomies: PRICE, TIMING,
  TECHNICAL_FIT, COMPETITION, AUTHORITY, TRUST, INTEGRATION,
  COMPLIANCE. A hallucinated category (``"PRICING_AND_PROCUREMENT"``)
  fails validation at parse time and the wrapper's retry loop fires.
  Widening the enum requires a deliberate schema bump and a test
  update — that is the point.

* **No hard per-category minimum.** With 15-20 total rows across
  eight categories, a per-category floor would force the writer to
  fabricate a thin-coverage category rather than admit it via
  ``gaps``. Same rationale as :class:`FAQDocument`.

* **``Confidence`` reused from :mod:`backend.agents.research_models`.**
  Same single-source-of-truth pattern as benefits / FAQ / mapping.

* **Two ref lists per row.** ``knowledge_excerpt_refs`` point into
  the upstream :attr:`ProductMapping.knowledge_excerpts` list;
  ``briefing_source_refs`` point into the upstream
  :attr:`Briefing.sources.entries`. They are semantically different
  pools (ANS-side product evidence vs. company-side source evidence)
  so collapsing them into a single tagged list would lose
  information the XLSX renderer needs. The renderer must concatenate
  the two pools into the single "Supporting evidence" column at
  render time — a future move to a single tagged list would be a
  breaking schema bump.

* **Refs are validated non-negative-only at the writer schema
  level.** The writer never sees the upstream artefacts as parsed
  objects (only as opaque JSON text per the agent's signature), so
  cross-artefact range validation cannot fire here. The critic agent
  (later step) is the appropriate validator; this matches the soft
  contract already used by :class:`BenefitClaim` and
  :class:`FAQEntry`.

* **``rows`` is cardinality-bounded at 15-20.** Mirrors the handover
  spec exactly. Below 15 or above 20 fails parse so the writer
  cannot accidentally ship an XLSX that misses the contract or blows
  past the planned row budget.

* **``escalation_path`` is required and non-empty.** Column 6 carries
  the "who do I bring in when the prospect pushes back" answer; a
  blank cell would silently break the XLSX contract. ``min_length=1``
  is the load-bearing fence.

* **``gaps`` is optional with default ``[]``.** Honest coverage
  admissions surface here — same release valve used by every other
  Stage 1/2 artefact.

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
    "ObjectionCategory",
    "ObjectionRow",
    "ObjectionsRegister",
]


# ---------------------------------------------------------------------------
# ObjectionCategory — closed eight-value enum
# ---------------------------------------------------------------------------

class ObjectionCategory(str, Enum):
    """The closed set of objection groupings.

    The handover does not name a fixed list; these values are drawn
    from standard B2B-sales objection taxonomies and pinned here so
    the writer's category field gets the same retry self-fence as
    :class:`FAQCategory` and :class:`Audience`. Widen the enum (and
    the test that pins it) only when a stakeholder asks for a new
    bucket.
    """

    PRICE = "PRICE"
    TIMING = "TIMING"
    TECHNICAL_FIT = "TECHNICAL_FIT"
    COMPETITION = "COMPETITION"
    AUTHORITY = "AUTHORITY"
    TRUST = "TRUST"
    INTEGRATION = "INTEGRATION"
    COMPLIANCE = "COMPLIANCE"


# ---------------------------------------------------------------------------
# ObjectionRow
# ---------------------------------------------------------------------------

class ObjectionRow(BaseModel):
    """One row of the objections XLSX.

    Field names map one-to-one onto the handover's six XLSX columns:
    ``category`` → Category, ``objection`` → Objection,
    ``underlying_concern`` → Underlying concern, ``response`` →
    Response, ``knowledge_excerpt_refs`` + ``briefing_source_refs``
    → Supporting evidence (concatenated at render time),
    ``escalation_path`` → Escalation path. Citation refs are
    positional indices into the upstream bundles — the writer never
    sees those bundles as parsed objects, so range validation is
    deferred to the critic.
    """

    model_config = ConfigDict(extra="forbid")

    category: ObjectionCategory
    objection: str = Field(min_length=1, max_length=400)
    underlying_concern: str = Field(min_length=1, max_length=600)
    response: str = Field(min_length=1, max_length=2000)
    knowledge_excerpt_refs: list[int] = Field(default_factory=list)
    briefing_source_refs: list[int] = Field(default_factory=list)
    escalation_path: str = Field(min_length=1, max_length=400)
    confidence: Confidence

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "ObjectionRow":
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
# ObjectionsRegister
# ---------------------------------------------------------------------------

class ObjectionsRegister(BaseModel):
    """Top-level payload returned by the objections writer agent.

    Mirrors the approved briefing's ``company_name`` / ``company_url``
    so the on-disk artefact (when persistence lands in a later step)
    is self-describing without the briefing loaded. ``written_at``
    is set by the agent; a future orchestrator step may override it
    deterministically (same shape as ``mapped_at`` in
    :class:`ProductMapping` and ``written_at`` in
    :class:`BenefitsBrief` / :class:`FAQDocument`).

    ``rows`` is a flat list of 15-20 :class:`ObjectionRow` records.
    Each row's ``category`` slots it into one of the eight
    :class:`ObjectionCategory` values for the renderer; the schema
    does NOT enforce a per-category minimum (see module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    written_at: date

    rows: list[ObjectionRow] = Field(min_length=15, max_length=20)

    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _refs_non_negative(self) -> "ObjectionsRegister":
        """Re-check ref negativity at the top level.

        Field-level non-negativity is already enforced inside
        :class:`ObjectionRow`; re-checking here means a future
        loosening of the per-row guard still catches negatives
        before the artefact leaves the agent. Range-against-upstream
        validation is deferred to the critic — see the module
        docstring.
        """
        for i, row in enumerate(self.rows):
            for idx in row.knowledge_excerpt_refs:
                if idx < 0:
                    raise ValueError(
                        f"rows[{i}].knowledge_excerpt_refs: "
                        f"index {idx} is negative"
                    )
            for idx in row.briefing_source_refs:
                if idx < 0:
                    raise ValueError(
                        f"rows[{i}].briefing_source_refs: "
                        f"index {idx} is negative"
                    )
        return self
