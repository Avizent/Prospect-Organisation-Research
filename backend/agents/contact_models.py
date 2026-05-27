"""Pydantic schema for the contact extraction agent's structured output.

Stage 1, Step 2 of the pipeline produces a :class:`ContactExtractionResult`
— a list of structured contact candidates pulled from the research
dossier. Four design choices warrant calling out:

* **Confidence reused from :mod:`backend.agents.research_models`.** The
  enum's four levels (HIGH / MEDIUM / LOW / INFERRED) carry the same
  meaning whether the artefact is a finding or a contact. Re-using the
  single enum keeps the two schemas from drifting apart.

* **Function is a constrained enum, not a free string.** Handover §15
  ("Compliance"): "Only network/infra/IT-exec/security-exec/CTO/CIO
  roles". Enum-typing the field makes an off-function row a
  Pydantic-level rejection — the prompt rule has a schema-level
  backstop.

* **No DOB / age field.** Handover requires "no contacts under 21" but
  the ``contacts`` table holds no age column, and the model cannot
  reliably prove age from public research. The rule lives in the
  system prompt only — adding an ``age_attestation`` field would
  invite fabricated compliance data. (Step 8a design note.)

* **Personal-domain emails are rejected by validator.** Handover §15:
  "No personal email domains (gmail/hotmail/yahoo)". The validator
  blocks the canonical list of free-mail providers so a contact with
  ``alice@gmail.com`` cannot enter the pipeline under any
  circumstance, even if the prompt instruction were somehow ignored.

This module is import-safe for every test in ``tests/runaway/`` and
``tests/agents/`` — it imports only the standard library, Pydantic, and
the :class:`Confidence` enum from a sibling models module. No SDK, no
Keychain, no I/O.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

from backend.agents.research_models import Confidence  # single source of truth

__all__ = [
    "Confidence",
    "Seniority",
    "Function",
    "ContactCandidate",
    "ContactExtractionResult",
    "PERSONAL_EMAIL_DOMAINS",
]


# ---------------------------------------------------------------------------
# Personal-email-domain block list — handover §15
# ---------------------------------------------------------------------------

#: Free-mail providers a B2B contact must not use. Lower-cased; the
#: validator lower-cases the candidate domain before lookup. The list is
#: intentionally short — the goal is to catch the canonical free-mail
#: domains the handover names plus the obvious country and provider
#: variants, not to maintain a global registry of consumer mail hosts.
PERSONAL_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "hotmail.co.uk",
    "hotmail.fr",
    "hotmail.de",
    "yahoo.com",
    "yahoo.co.uk",
    "ymail.com",
    "outlook.com",
    "live.com",
    "live.co.uk",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "aol.co.uk",
    "protonmail.com",
    "proton.me",
    "gmx.com",
    "gmx.co.uk",
    "gmx.de",
})


# ---------------------------------------------------------------------------
# Seniority — coarse taxonomy mirroring the contacts.seniority TEXT column
# ---------------------------------------------------------------------------

class Seniority(str, Enum):
    """Coarse seniority bucket.

    Six values is enough for downstream filtering (e.g. "show me only
    C-level contacts") without forcing the model to make implausibly
    fine distinctions. ``None`` is permitted on
    :class:`ContactCandidate` for cases where the title is known but
    the seniority isn't obvious.
    """

    C_LEVEL = "C_LEVEL"
    VP = "VP"
    DIRECTOR = "DIRECTOR"
    HEAD = "HEAD"
    MANAGER = "MANAGER"
    INDIVIDUAL_CONTRIBUTOR = "INDIVIDUAL_CONTRIBUTOR"


# ---------------------------------------------------------------------------
# Function — exactly the six roles permitted by handover §15
# ---------------------------------------------------------------------------

class Function(str, Enum):
    """Permitted job function for a recorded contact.

    Handover §15 ("Compliance"): only network, infrastructure,
    IT-executive, security-executive, CTO, or CIO functions may be
    recorded. Any candidate whose function does not map to one of
    these six values must be omitted from the extraction at the prompt
    level; this enum is the schema-level backstop.
    """

    NETWORK = "NETWORK"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    IT_EXECUTIVE = "IT_EXECUTIVE"
    SECURITY_EXECUTIVE = "SECURITY_EXECUTIVE"
    CTO = "CTO"
    CIO = "CIO"


# ---------------------------------------------------------------------------
# ContactCandidate
# ---------------------------------------------------------------------------

class ContactCandidate(BaseModel):
    """A single extracted contact, ready for persistence by the
    orchestrator (Step 8b/8c). The schema mirrors the persistable
    subset of the ``contacts`` table; operator-controlled columns
    (``do_not_contact``, ``dnc_set_at``, ``dnc_reason``, timestamps)
    are not emitted by the agent.

    Required: ``name``, ``function``, ``source_url``, ``retrieved_at``,
    ``confidence``. Every recorded contact must carry provenance — the
    handover ("no source, no contact") is non-negotiable.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    job_title: str | None = Field(default=None, max_length=300)
    country: str | None = Field(default=None, max_length=120)
    linkedin_url: HttpUrl | None = None
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=60)
    mobile: str | None = Field(default=None, max_length=60)
    seniority: Seniority | None = None
    function: Function
    source_url: HttpUrl
    source_title: str | None = Field(default=None, max_length=500)
    retrieved_at: date
    confidence: Confidence
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def _reject_personal_email_domains(
        cls, value: str | None
    ) -> str | None:
        """Reject any address whose domain is in
        :data:`PERSONAL_EMAIL_DOMAINS`.

        Comparison is case-insensitive after stripping, so
        ``Alice@GMAIL.com`` is rejected the same as ``alice@gmail.com``.
        Whitespace-only values normalise to ``None`` — the agent often
        emits an empty string when no address is available, and treating
        that as "absent" is friendlier than failing the whole record.
        """
        if value is None:
            return value
        candidate = value.strip()
        if not candidate:
            return None
        if "@" not in candidate:
            raise ValueError(
                "email must contain '@' if provided"
            )
        local, _, domain = candidate.rpartition("@")
        if not local or not domain:
            raise ValueError(
                "email must have a non-empty local part and domain"
            )
        if domain.lower() in PERSONAL_EMAIL_DOMAINS:
            raise ValueError(
                f"personal email domain {domain!r} is not permitted "
                "for a recorded contact"
            )
        return candidate


# ---------------------------------------------------------------------------
# ContactExtractionResult
# ---------------------------------------------------------------------------

class ContactExtractionResult(BaseModel):
    """Top-level payload returned by the contact extraction agent.

    Carries the candidate list and an explicit ``gaps`` array mirroring
    :class:`backend.agents.research_models.ResearchDossier.gaps` so the
    user-facing review screen can surface "we found no security
    executives" the same way it surfaces missing research priorities.
    """

    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=300)
    contacts: list[ContactCandidate] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
