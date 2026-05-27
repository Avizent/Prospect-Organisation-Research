"""Pydantic schema for the needs-inference agent's structured output.

Stage 1, Step 3 of the pipeline produces a :class:`NeedsAssessment` —
the load-bearing input for every Stage 2 writer. Four design choices
warrant calling out:

* **``lab_maturity`` is a frozen four-value enum.** Handover §9.3 step
  4 names the canonical set: ``none_visible | basic | mature |
  modernisation_in_progress``. Stage 2 writers branch on this; a rename
  silently breaks every writer's prioritisation. The string form is
  lower-case underscore so it can drop straight into the
  ``companies.lab_maturity`` ``TEXT`` column.

* **Confidence reused from :mod:`backend.agents.research_models`.** The
  enum's four levels (HIGH / MEDIUM / LOW / INFERRED) carry the same
  meaning whether the artefact is a finding, a contact, or a needs
  call. Re-using the single enum keeps the schemas from drifting.

* **EvidencePointer is intentionally separate from
  :class:`backend.agents.research_models.Source`.** The pointer carries
  a ``summary`` (short quote / paraphrase) field that the briefing
  compiler renders directly without re-fetching the dossier. ``Source``
  is the researcher's source-of-truth record; ``EvidencePointer`` is
  the needs agent's citation back to that source.

* **Priority is a positive int, not an enum.** ``Field(ge=1)`` with
  "1 = highest, ascending" semantics in the prompt. Uniqueness is not
  schema-enforced — ties are recoverable by the briefing compiler and
  shouldn't trigger a retry loop on cosmetic disagreements.

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
)

from backend.agents.research_models import Confidence  # single source of truth

__all__ = [
    "Confidence",
    "LabMaturity",
    "EvidencePointer",
    "IdentifiedNeed",
    "NeedsAssessment",
]


# ---------------------------------------------------------------------------
# LabMaturity — frozen four-value enum (handover §9.3 step 4)
# ---------------------------------------------------------------------------

class LabMaturity(str, Enum):
    """Lab maturity hypothesis for the target company.

    Exactly the four values named in handover §9.3 step 4 / BUILD-PROMPT
    line 352. Lower-case underscore form so the value persists directly
    into ``companies.lab_maturity`` (``TEXT``) without translation.

    Semantics (paraphrased from the handover):

    * ``none_visible`` — no public evidence of a network / infrastructure
      lab function at all.
    * ``basic`` — minimal in-house infrastructure team, mostly
      off-the-shelf vendors, no transformation programme.
    * ``mature`` — established in-house team, named senior leadership,
      stable vendor footprint, no obvious modernisation pressure.
    * ``modernisation_in_progress`` — public evidence of an active
      transformation programme (job postings for cloud / SDN / zero-trust
      roles, vendor case studies dated within 18 months, press releases
      on infrastructure refresh).
    """

    NONE_VISIBLE = "none_visible"
    BASIC = "basic"
    MATURE = "mature"
    MODERNISATION_IN_PROGRESS = "modernisation_in_progress"


# ---------------------------------------------------------------------------
# EvidencePointer
# ---------------------------------------------------------------------------

class EvidencePointer(BaseModel):
    """A citation back to a dossier source, carrying a short quote.

    The briefing compiler renders this directly into the briefing
    document's source register. The ``summary`` field is the
    one-sentence quote or paraphrase that backs the claim — so a writer
    agent can cite evidence without the dossier loaded.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    source_url: HttpUrl
    source_title: str | None = Field(default=None, max_length=500)
    retrieved_at: date
    confidence: Confidence


# ---------------------------------------------------------------------------
# IdentifiedNeed
# ---------------------------------------------------------------------------

class IdentifiedNeed(BaseModel):
    """A single inferred need at the target company.

    ``priority`` is a positive integer where 1 = highest; the prompt
    instructs the model to emit needs in ascending priority order with
    no ties. Ties are not schema-enforced — the briefing compiler can
    re-rank if necessary, and rejecting ties at validation time would
    cause retry loops on cosmetic disagreements.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=4000)
    priority: int = Field(ge=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)
    confidence: Confidence


# ---------------------------------------------------------------------------
# NeedsAssessment
# ---------------------------------------------------------------------------

class NeedsAssessment(BaseModel):
    """Top-level payload returned by the needs inference agent.

    The headline output is :attr:`lab_maturity` — a single enum value
    that Stage 2 writers branch on. The supporting structure
    (``lab_maturity_reasoning``, ``lab_maturity_evidence``,
    ``lab_maturity_confidence``) lets a reviewer see *why* the call was
    made.

    The ``needs`` list is ranked by ``priority``; the prompt instructs
    "1 = highest, ascending, no ties," but ties are tolerated by the
    schema. An empty list is a legal outcome — surface what was looked
    for via ``gaps``.

    ``lab_maturity_evidence`` may legitimately be empty when
    ``lab_maturity == NONE_VISIBLE`` (the absence of evidence is the
    call); the prompt says so. The schema allows empty lists for both
    fields to keep the validation surface simple.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    lab_maturity: LabMaturity
    lab_maturity_reasoning: str = Field(min_length=1, max_length=4000)
    lab_maturity_evidence: list[EvidencePointer] = Field(default_factory=list)
    lab_maturity_confidence: Confidence
    needs: list[IdentifiedNeed] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
