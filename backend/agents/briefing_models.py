"""Pydantic schema for the briefing compiler's structured output.

Stage 1, Step 4 of the pipeline produces a :class:`Briefing` —
the merge of :class:`ResearchDossier`, :class:`ContactExtractionResult`,
and :class:`NeedsAssessment` into a single human-reviewable artefact.
Stage 2 reads only the *approved* briefing (handover line 236), never
the raw upstream inputs, which is why this is the load-bearing schema
for everything downstream.

Six design choices warrant calling out:

* **Six sections, one per attribute.** Handover §9.2 step 5 names
  Snapshot, Business context, IT & network landscape, Key people,
  ANS opportunity hypothesis, and Source register. Each is its own
  nested model with ``extra="forbid"``, so a section rename surfaces
  as a :class:`ValidationError` rather than silent drift.

* **Source register is the single canonical list of URLs.** Every
  claim-bearing field references entries positionally via
  ``source_indices`` (or ``source_index`` for single-source contact
  rows). Duplicating URLs at each claim would invite divergence after
  an inline edit on the approval screen; positional references keep
  the file small and self-consistent.

* **Indices are validated to be in range.** A ``source_index`` that
  points past the end of :attr:`SourceRegister.entries` would render as
  a broken citation in the briefing template, and silently dropping the
  index would hide the model's error. The model-level
  :meth:`Briefing._check_source_indices_in_range` validator walks every
  claim and raises if any index is out of range. Negative indices are
  already blocked at the field level via ``Field(ge=0)``.

* **``Confidence`` reused from :mod:`backend.agents.research_models`.**
  Single source of truth — the four levels (HIGH / MEDIUM / LOW /
  INFERRED) carry the same meaning whether the artefact is a finding,
  a contact, a needs call, or a briefing claim.

* **``LabMaturity`` reused from :mod:`backend.agents.needs_models`.**
  Same reasoning — the four-value enum is load-bearing for every Stage
  2 writer; a parallel definition here would create a silent drift
  surface.

* **GDPR validators are not duplicated.** Personal-email rejection and
  the function whitelist live in :mod:`backend.agents.contact_models`.
  By the time a contact reaches this schema it has already passed those
  checks; re-running them here would only invite the rules to drift.

This module is import-safe for every test in ``tests/runaway/`` and
``tests/agents/`` — it imports only the standard library, Pydantic,
and the :class:`Confidence` and :class:`LabMaturity` enums from sibling
models modules. No SDK, no Keychain, no I/O.
"""

from __future__ import annotations

from datetime import date

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from backend.agents.needs_models import LabMaturity  # single source of truth
from backend.agents.research_models import Confidence  # single source of truth

__all__ = [
    "Confidence",
    "LabMaturity",
    "SourceEntry",
    "SourceRegister",
    "BriefingClaim",
    "BriefingContact",
    "RankedNeed",
    "Snapshot",
    "BusinessContext",
    "ItLandscape",
    "KeyPeople",
    "Opportunity",
    "Briefing",
]


# ---------------------------------------------------------------------------
# SourceEntry / SourceRegister
# ---------------------------------------------------------------------------

class SourceEntry(BaseModel):
    """A single source URL backing one or more claims in the briefing.

    Mirrors the persistable fields of
    :class:`backend.agents.research_models.Source` but lives in the
    briefing namespace so the approval-screen template can render the
    source register without pulling the dossier back into scope.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: str | None = Field(default=None, max_length=500)
    retrieved_at: date
    confidence: Confidence


class SourceRegister(BaseModel):
    """The single canonical list of sources for the entire briefing.

    Every ``source_index`` / ``source_indices`` value elsewhere in the
    :class:`Briefing` is a positional index into :attr:`entries`. The
    range validation lives on :class:`Briefing` itself — keeping the
    check at the top level means we only walk the tree once and can
    surface a precise "index N out of range for M entries" error.

    ``gaps`` is the union of upstream gaps (dossier / contacts / needs)
    plus anything the compiler itself could not reconcile. Empty is a
    legal outcome.
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[SourceEntry] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# BriefingClaim — the recurring "evidence-backed sentence" record
# ---------------------------------------------------------------------------

class BriefingClaim(BaseModel):
    """A single evidence-backed sentence within a section.

    ``source_indices`` may be empty when ``confidence == INFERRED`` —
    an inference the compiler drew over the upstream inputs without a
    primary citation. For any other confidence level the prompt asks
    for at least one citation, but the schema does not enforce that
    (the critic agent in Step 10 catches over-confident uncited claims;
    rejecting them at the schema layer would just cause retry loops on
    cosmetic disagreements).
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=4000)
    confidence: Confidence
    source_indices: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _source_indices_non_negative(self) -> "BriefingClaim":
        for idx in self.source_indices:
            if idx < 0:
                raise ValueError(
                    f"source_indices must be >= 0; got {idx}"
                )
        return self


# ---------------------------------------------------------------------------
# BriefingContact — display row in the Key People section
# ---------------------------------------------------------------------------

class BriefingContact(BaseModel):
    """A contact row rendered into the briefing's Key People section.

    This is a *display* projection of a
    :class:`backend.agents.contact_models.ContactCandidate`. By the time
    a contact reaches this point, the contact extraction step has already
    validated function whitelist + personal-email block list (Step 8a);
    those validators are deliberately NOT duplicated here.

    ``source_index`` is optional only for the rare case of an inferred
    contact (e.g. an org-chart inference); the prompt asks for an index
    on every contact. The schema permits ``None`` so the absence of a
    citation is explicit rather than papered over by a default.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    job_title: str | None = Field(default=None, max_length=300)
    function: str = Field(min_length=1, max_length=120)
    seniority: str | None = Field(default=None, max_length=60)
    linkedin_url: HttpUrl | None = None
    email: str | None = Field(default=None, max_length=320)
    confidence: Confidence
    source_index: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# RankedNeed — display row in the Opportunity section
# ---------------------------------------------------------------------------

class RankedNeed(BaseModel):
    """A need rendered for the Opportunity section, with framing fields.

    ``suggested_products`` is intentionally a free-text list of product
    *categories* the compiler thinks fit (e.g. "SD-WAN replacement",
    "zero-trust segmentation"), not catalogue SKUs. The Stage 2 mapping
    agent (Step 10) binds these to the ANS knowledge base; the briefing
    compiler must not pre-empt that.

    ``priority`` mirrors :class:`backend.agents.needs_models.IdentifiedNeed`
    — positive integer, 1 = highest, ties tolerated.

    ``source_indices`` semantics match :class:`BriefingClaim`.
    """

    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=4000)
    suggested_products: list[str] = Field(default_factory=list)
    entry_angle: str = Field(min_length=1, max_length=2000)
    watch_outs: list[str] = Field(default_factory=list)
    confidence: Confidence
    source_indices: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _source_indices_non_negative(self) -> "RankedNeed":
        for idx in self.source_indices:
            if idx < 0:
                raise ValueError(
                    f"source_indices must be >= 0; got {idx}"
                )
        return self


# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------

class Snapshot(BaseModel):
    """Section 1 — high-level snapshot of the target.

    ``lab_maturity`` mirrors the needs assessment's headline call,
    propagated here so the approval-screen template doesn't need to
    cross-reference the upstream artefact at render time.
    """

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=400)
    one_line_desc: str = Field(min_length=1, max_length=400)
    sector: str | None = Field(default=None, max_length=200)
    headcount_band: str | None = Field(default=None, max_length=120)
    hq_country: str | None = Field(default=None, max_length=120)
    ownership: str | None = Field(default=None, max_length=200)
    lab_maturity: LabMaturity
    why_interesting_to_ans: str = Field(min_length=1, max_length=4000)
    confidence: Confidence


class BusinessContext(BaseModel):
    """Section 2 — news, M&A, financials, regulatory, risks."""

    model_config = ConfigDict(extra="forbid")

    news: list[BriefingClaim] = Field(default_factory=list)
    m_and_a: list[BriefingClaim] = Field(default_factory=list)
    financials: list[BriefingClaim] = Field(default_factory=list)
    regulatory: list[BriefingClaim] = Field(default_factory=list)
    risks: list[BriefingClaim] = Field(default_factory=list)


class ItLandscape(BaseModel):
    """Section 3 — tech stack, architecture, vendors, transformation
    programmes, lab maturity reasoning, team shape, tooling."""

    model_config = ConfigDict(extra="forbid")

    tech_stack: list[BriefingClaim] = Field(default_factory=list)
    architecture_notes: list[BriefingClaim] = Field(default_factory=list)
    vendors: list[BriefingClaim] = Field(default_factory=list)
    transformation_programmes: list[BriefingClaim] = Field(default_factory=list)
    lab_maturity_reasoning: str = Field(min_length=1, max_length=4000)
    team_shape: list[BriefingClaim] = Field(default_factory=list)
    tooling: list[BriefingClaim] = Field(default_factory=list)


class KeyPeople(BaseModel):
    """Section 4 — named contacts and hiring signals."""

    model_config = ConfigDict(extra="forbid")

    contacts: list[BriefingContact] = Field(default_factory=list)
    hiring_signals: list[BriefingClaim] = Field(default_factory=list)


class Opportunity(BaseModel):
    """Section 5 — ANS opportunity hypothesis.

    ``recommended_angle`` is required (handover line 292: "recommended
    angle" is part of the one-liner the approval screen surfaces).
    """

    model_config = ConfigDict(extra="forbid")

    ranked_needs: list[RankedNeed] = Field(default_factory=list)
    buying_cycle_stage: str | None = Field(default=None, max_length=200)
    recommended_angle: str = Field(min_length=1, max_length=2000)
    watch_outs: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------

class Briefing(BaseModel):
    """Top-level payload returned by the briefing compiler.

    The six sections map one-to-one to handover §9.2 step 5. Every
    section is required (the briefing always has all six, even if the
    inner lists are empty) so the renderer can rely on the shape.

    ``user_context`` is the operator's "Add context" textarea
    (handover line 228). The agent emits this as ``None``; the approval
    screen (Step 12) writes the operator-supplied value at edit time.
    Including the field on the schema now keeps the round-trip clean
    when the editor PATCHes it.

    The :meth:`_check_source_indices_in_range` validator walks every
    claim/contact/need and raises if any ``source_index`` points outside
    :attr:`sources.entries`. Out-of-range indices would render as broken
    citations in the briefing template; silently dropping them would
    hide the error.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    compiled_at: date
    user_context: str | None = Field(default=None, max_length=4000)

    snapshot: Snapshot
    business_context: BusinessContext
    it_landscape: ItLandscape
    key_people: KeyPeople
    opportunity: Opportunity
    sources: SourceRegister

    @model_validator(mode="after")
    def _check_source_indices_in_range(self) -> "Briefing":
        n = len(self.sources.entries)

        def _check(indices: list[int], location: str) -> None:
            for idx in indices:
                # Field(ge=0) and the per-model validators already
                # block negatives at field-validation time; check again
                # here so a future schema change that loosens those
                # guards still catches negatives at the top level.
                if idx < 0:
                    raise ValueError(
                        f"{location}: source index {idx} is negative"
                    )
                if idx >= n:
                    raise ValueError(
                        f"{location}: source index {idx} out of range "
                        f"for sources.entries (length {n})"
                    )

        # BusinessContext claims
        for field_name in (
            "news",
            "m_and_a",
            "financials",
            "regulatory",
            "risks",
        ):
            claims: list[BriefingClaim] = getattr(
                self.business_context, field_name
            )
            for i, claim in enumerate(claims):
                _check(
                    claim.source_indices,
                    f"business_context.{field_name}[{i}].source_indices",
                )

        # ItLandscape claims
        for field_name in (
            "tech_stack",
            "architecture_notes",
            "vendors",
            "transformation_programmes",
            "team_shape",
            "tooling",
        ):
            claims = getattr(self.it_landscape, field_name)
            for i, claim in enumerate(claims):
                _check(
                    claim.source_indices,
                    f"it_landscape.{field_name}[{i}].source_indices",
                )

        # KeyPeople — contacts (single index) and hiring_signals (list)
        for i, contact in enumerate(self.key_people.contacts):
            if contact.source_index is not None:
                _check(
                    [contact.source_index],
                    f"key_people.contacts[{i}].source_index",
                )
        for i, claim in enumerate(self.key_people.hiring_signals):
            _check(
                claim.source_indices,
                f"key_people.hiring_signals[{i}].source_indices",
            )

        # Opportunity.ranked_needs
        for i, need in enumerate(self.opportunity.ranked_needs):
            _check(
                need.source_indices,
                f"opportunity.ranked_needs[{i}].source_indices",
            )

        return self
