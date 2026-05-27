"""Pydantic schema for the research agent's structured output.

Stage 1, Step 1 of the pipeline produces a :class:`ResearchDossier` —
the raw evidence base that subsequent agents (needs, briefing
compiler, mapping, writers) read from. Two design choices warrant
calling out:

* **Named sections, not a flat tag list.** The handover lists seven
  research priorities (job postings, annual reports, regulatory
  exposure, vendor footprint, M&A, network incidents, senior hires)
  plus an explicit overflow bucket. We model those as eight named
  ``list[Finding]`` fields. Downstream agents can read by attribute
  name instead of filtering a flat list by string tag — a string tag
  is too easy to silently drift.

* **Confidence is required, not defaulted.** Every :class:`Finding`
  and :class:`Source` must declare its confidence. Defaulting to
  ``HIGH`` (or any value) would let a writer agent quote weak
  evidence as if it were strong. Make the model think about it.

This module is import-safe for every test in
``tests/runaway/`` and ``tests/agents/`` — it imports only the
standard library and Pydantic. No SDK, no Keychain, no I/O.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


# ---------------------------------------------------------------------------
# Confidence enum
# ---------------------------------------------------------------------------

class Confidence(str, Enum):
    """How load-bearing a single finding or source is.

    ``HIGH``       — directly attributed in a primary source we fetched.
    ``MEDIUM``     — secondary source, or attributed but light on detail.
    ``LOW``        — circumstantial; named in passing or older than 12 months.
    ``INFERRED``   — not stated; the model is drawing a conclusion. The
                     critic agent (Step 10) treats ``INFERRED`` as soft.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFERRED = "INFERRED"


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class Source(BaseModel):
    """A single source URL backing one or more :class:`Finding` items.

    ``retrieved_at`` is the calendar date the research agent reports
    having read the page on. Strict ISO-8601 dates (no times) keep the
    downstream renderer simple.
    """

    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    retrieved_at: date
    confidence: Confidence


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """A single observation about the target company.

    ``summary`` is a one-line headline; ``detail`` is the supporting
    paragraph. Both are required so a writer never has to ask "what did
    the researcher actually mean by that?".
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=4000)
    confidence: Confidence
    sources: list[Source] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ResearchDossier
# ---------------------------------------------------------------------------

class ResearchDossier(BaseModel):
    """Top-level payload returned by the research agent.

    The eight ``list[Finding]`` fields map one-to-one to the research
    priorities in the handover, plus an ``other`` overflow bucket for
    findings that don't fit. ``gaps`` is the agent's own admission of
    where it could not find evidence — surfaced to the user on the
    Stage-1 review screen.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    company_url: HttpUrl
    retrieved_at: date

    job_postings: list[Finding] = Field(default_factory=list)
    annual_reports: list[Finding] = Field(default_factory=list)
    regulatory_exposure: list[Finding] = Field(default_factory=list)
    vendor_footprint: list[Finding] = Field(default_factory=list)
    m_and_a: list[Finding] = Field(default_factory=list)
    network_incidents: list[Finding] = Field(default_factory=list)
    senior_hires: list[Finding] = Field(default_factory=list)
    other: list[Finding] = Field(default_factory=list)

    gaps: list[str] = Field(default_factory=list)
