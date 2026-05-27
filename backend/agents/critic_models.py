"""Pydantic schema for the Stage 2 critic agent's structured output.

Stage 2, Step 19 of the pipeline produces a :class:`Stage2CriticReport`
— the critic's verdict on the three writer artefacts (benefits, FAQ,
objections) against the approved briefing and the ANS knowledge
bundle. The critic is the third reasoning agent in Stage 2 (after the
product-mapping agent and the three writers).

Design choices worth calling out
--------------------------------

* **Normalised ``issues`` list, not seven parallel buckets.** The
  natural shape is "a flat list of issues, each carrying its severity
  and category enums". A renderer or downstream consumer can group by
  severity or category at print time. Carrying seven parallel
  top-level lists (``blocking_issues``, ``warnings``,
  ``suggested_revisions``, ``evidence_gaps``,
  ``unsupported_claims``, ``citation_ref_issues``, plus the verdict)
  would force the same issue to be split across buckets and let the
  verdict drift away from issue counts. One issue == one record.

* **Three closed enums.** :class:`Verdict`, :class:`Severity` and
  :class:`IssueCategory` are all closed sets. A hallucinated value
  (e.g. ``verdict = "MAYBE_READY"``) fails parse and the wrapper's
  retry loop fires before the bad value reaches any consumer.
  Widening any enum (and the test that pins it) is a deliberate
  schema bump — that is the point.

* **``CitedArtefact`` is a closed three-value enum.** Locks the
  three writer artefacts the critic can refer to. A future fourth
  artefact (e.g. case studies) requires widening this enum and the
  test that pins it.

* **Cross-field verdict/severity validator.** The verdict must agree
  with the severity distribution of the issues:

      - ``READY`` ⇒ no ``BLOCKING`` and no ``WARNING`` issues
      - ``READY_WITH_WARNINGS`` ⇒ no ``BLOCKING`` issues; at least
        one ``WARNING`` issue
      - ``NEEDS_REVISION`` ⇒ at least one ``BLOCKING`` issue

  This is the load-bearing self-fence: a model that emits a
  ``BLOCKING`` issue but stamps ``verdict = READY`` will fail parse
  and be retried with the strict-JSON reminder. Without this rule a
  later orchestrator step would have to re-derive the verdict from
  the issues to be safe — better to make the schema authoritative.

* **``suggested_fix`` is optional.** Not every issue has an obvious
  remediation (e.g. a tone observation may just be a flag). Making
  it optional matches reality and avoids forcing the model to
  fabricate a fix.

* **``description`` and ``locator`` are required and non-empty.**
  A blank description or locator is useless to the operator and the
  renderer. ``min_length=1`` is the load-bearing fence.

* **``summary`` is required and non-empty.** The top-level summary
  is the operator's at-a-glance read of the critic's verdict. Even
  a READY verdict needs the one-paragraph "why I think so" framing
  so the operator can spot a confidently-wrong critic.

* **``gaps`` is optional with default ``[]``.** Surfacing honest
  coverage admissions ("could not assess Netropy jitter without
  datasheet") is the same self-fence used by every other Stage 1/2
  artefact.

* **``issues.max_length = 50``.** A generous ceiling — three writer
  artefacts × ~17 rows each ≈ 51 cells; one issue per cell is the
  worst case. Above 50 is a sign the critic has gone off the rails
  and should be retried.

* **No ``Confidence`` field on individual issues.** The critic is
  the judgement layer; per-issue confidence would be circular
  ("how confident am I that I'm right?"). The verdict captures the
  overall posture; severity captures the per-issue weight.

This module is import-safe for every test in ``tests/agents/`` —
it imports only the standard library and Pydantic. No SDK, no
Keychain, no I/O, no SQLAlchemy.
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

__all__ = [
    "Verdict",
    "Severity",
    "IssueCategory",
    "CitedArtefact",
    "CriticIssue",
    "Stage2CriticReport",
]


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    """The critic's overall readiness verdict on the three writer
    artefacts.

    Three values, deliberately coarse. Finer granularity (e.g. a
    separate ``FAIL`` for "the artefacts are unsalvageable") belongs
    at the orchestrator layer, not the critic — the critic's job is
    to surface issues, not to halt the pipeline.
    """

    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NEEDS_REVISION = "NEEDS_REVISION"


class Severity(str, Enum):
    """Per-issue weight. Drives the cross-field verdict validator.

    ``BLOCKING`` issues force a ``NEEDS_REVISION`` verdict.
    ``WARNING`` issues block a ``READY`` verdict (must be at least
    ``READY_WITH_WARNINGS``). ``INFO`` issues are noise-floor
    observations that do not affect the verdict.
    """

    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class IssueCategory(str, Enum):
    """Closed taxonomy of issue categories the critic can raise.

    Drawn from the four checks named in the handover (claims about
    the target company not supported by the briefing; claims about
    ANS products not in the knowledge base; factual inconsistencies;
    off-brand tone) plus two additional categories that surface
    naturally during review (broken citation refs and evidence
    gaps). ``OTHER`` is a last-resort hatch — every other category
    should be tried first.
    """

    UNSUPPORTED_COMPANY_CLAIM = "UNSUPPORTED_COMPANY_CLAIM"
    UNSUPPORTED_PRODUCT_CLAIM = "UNSUPPORTED_PRODUCT_CLAIM"
    FACTUAL_INCONSISTENCY = "FACTUAL_INCONSISTENCY"
    OFF_BRAND_TONE = "OFF_BRAND_TONE"
    CITATION_REF_ISSUE = "CITATION_REF_ISSUE"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    OTHER = "OTHER"


class CitedArtefact(str, Enum):
    """The three writer artefacts the critic reviews.

    Closed set; widening (e.g. for a future case-studies artefact)
    requires a deliberate schema bump and a test update. Same
    self-fence pattern as :class:`Audience` /
    :class:`ObjectionCategory` / :class:`FAQCategory`.
    """

    BENEFITS = "BENEFITS"
    FAQ = "FAQ"
    OBJECTIONS = "OBJECTIONS"


# ---------------------------------------------------------------------------
# CriticIssue
# ---------------------------------------------------------------------------

class CriticIssue(BaseModel):
    """One issue raised by the critic against one writer artefact.

    ``artefact`` names which writer artefact the issue is against;
    ``locator`` is a human-readable path into that artefact
    (e.g. ``"executive_summary.body[0].claim"`` for the benefits
    brief). ``severity`` and ``category`` are the closed enums that
    drive the verdict validator and any downstream filtering.
    ``description`` is the body; ``suggested_fix`` is an optional
    remediation hint.
    """

    model_config = ConfigDict(extra="forbid")

    artefact: CitedArtefact
    locator: str = Field(min_length=1, max_length=300)
    severity: Severity
    category: IssueCategory
    description: str = Field(min_length=1, max_length=600)
    suggested_fix: str | None = Field(default=None, max_length=600)


# ---------------------------------------------------------------------------
# Stage2CriticReport
# ---------------------------------------------------------------------------

class Stage2CriticReport(BaseModel):
    """Top-level payload returned by the Stage 2 critic agent.

    Mirrors the approved briefing's ``company_name`` / ``company_url``
    so the artefact (when persistence lands in a later step) is
    self-describing without the briefing loaded. ``reviewed_at`` is
    set by the agent; a future orchestrator step may override it
    deterministically (same shape as ``mapped_at`` in
    :class:`ProductMapping` and ``written_at`` in the writer
    artefacts).

    ``issues`` is a flat list of :class:`CriticIssue` records, bounded
    above at 50 so a runaway critic cannot blow past a sensible
    ceiling without tripping the schema. ``verdict`` and ``summary``
    are required; ``gaps`` carries honest "could not assess" notes.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    reviewed_at: date

    verdict: Verdict
    issues: list[CriticIssue] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=2000)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_matches_issue_severity(self) -> "Stage2CriticReport":
        """Pin the verdict to the severity distribution of the issues.

        Three rules:

        * ``READY`` ⇒ no ``BLOCKING`` and no ``WARNING`` issues
        * ``READY_WITH_WARNINGS`` ⇒ no ``BLOCKING`` issues; at least
          one ``WARNING`` issue
        * ``NEEDS_REVISION`` ⇒ at least one ``BLOCKING`` issue

        Without these rules a downstream consumer (the future
        revision-pass orchestrator) would have to re-derive the
        verdict from the issues to be safe — better to make the
        schema authoritative so a confident-but-inconsistent critic
        is caught by the retry loop.
        """
        has_blocking = any(
            i.severity is Severity.BLOCKING for i in self.issues
        )
        has_warning = any(
            i.severity is Severity.WARNING for i in self.issues
        )

        if self.verdict is Verdict.NEEDS_REVISION and not has_blocking:
            raise ValueError(
                "verdict=NEEDS_REVISION requires at least one "
                "BLOCKING issue"
            )
        if has_blocking and self.verdict is not Verdict.NEEDS_REVISION:
            raise ValueError(
                f"a BLOCKING issue requires verdict=NEEDS_REVISION; "
                f"got verdict={self.verdict.value!r}"
            )
        if self.verdict is Verdict.READY_WITH_WARNINGS and not has_warning:
            raise ValueError(
                "verdict=READY_WITH_WARNINGS requires at least one "
                "WARNING issue"
            )
        if self.verdict is Verdict.READY and has_warning:
            raise ValueError(
                "verdict=READY does not allow WARNING issues; use "
                "verdict=READY_WITH_WARNINGS instead"
            )
        return self
