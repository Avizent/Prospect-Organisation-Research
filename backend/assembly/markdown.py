"""Deterministic local Markdown assembly for an approved job.

Step 29 of the pipeline produces two terminal artefacts under
``~/.ans-tool/jobs/{job_id}/``:

* ``prospect_brief.md``      — a human-readable Markdown rollup of the
                               approved briefing and the Stage 2 writer
                               artefacts (mapping, benefits, FAQ,
                               objections, critic report).
* ``document_manifest.json`` — provenance/integrity record for the
                               ``.md`` and its input artefacts.

Strict scope (per Step 29 direction)
------------------------------------

This module is a *pure renderer* over on-disk Pydantic artefacts plus
two ``write_*`` calls into :mod:`backend.jobs.storage`. It must NOT
import or call:

* any HTTP client / network library
* the Anthropic SDK, the Keychain, ``backend.credentials``,
  ``backend.cost_control.cloud_client``
* any frontend, route, orchestrator, agent, or state-machine code
* any DOCX / PDF / M365 / delivery / email helper

It must NOT mutate any source artefact, append any transition, change
any job status, or stamp ``last_error``. Assembly is side-effect-free
with respect to the job state machine — the only files it touches are
the two new terminal artefacts above.

Determinism
-----------

The Markdown body is byte-identical across re-runs over identical
inputs. To preserve that contract:

* Section order is fixed (the nine keys in :data:`_SECTION_KEYS`).
* Iterables are rendered in their on-disk order (positional lists are
  load-bearing in every artefact schema — re-sorting would change the
  meaning of source/excerpt indices).
* The Markdown body never embeds a timestamp. The manifest carries
  ``generated_at`` as the single authoritative wall-clock value, which
  tests freeze via the ``now`` parameter on :func:`assemble_job`.

INTERNAL DRAFT watermark
------------------------

Hard rule #7 in :doc:`CLAUDE.md` requires every generated document to
carry an INTERNAL DRAFT watermark. The Markdown does so on the very
first line so the watermark survives a copy/paste of any prefix of the
document.

Critic verdict handling
-----------------------

The critic verdict is advisory at this layer — assembly always runs
to completion when the briefing is readable, regardless of verdict:

* ``READY``               → no banner.
* ``READY_WITH_WARNINGS`` → soft warning banner near the top.
* ``NEEDS_REVISION``      → prominent "do not release" banner near the
                            top, in addition to the INTERNAL DRAFT
                            watermark.
* critic artefact missing or malformed → no banner; warning recorded
  in the manifest.

Fatal vs. non-fatal artefact errors
-----------------------------------

The briefing is mandatory — it carries ``company_name`` /
``company_url`` and the source register every other section may cite.
Missing or malformed briefing raises :class:`BriefingRequiredError`
and writes nothing.

The five non-briefing artefacts are optional at this layer: missing or
malformed renders a stub section and appends a warning to the manifest.
The downstream consumer (the user) decides what to do.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.agents.benefits_models import BenefitsBrief, BenefitsSection
from backend.agents.briefing_models import Briefing
from backend.agents.critic_models import (
    CriticIssue,
    Severity,
    Stage2CriticReport,
    Verdict,
)
from backend.agents.faq_models import FAQDocument
from backend.agents.mapping_models import ProductMapping
from backend.agents.objections_models import ObjectionsRegister
from backend.jobs.storage import (
    JobNotFound,
    read_benefits,
    read_briefing,
    read_critic_report,
    read_document_manifest,
    read_faq,
    read_objections,
    read_product_mapping,
    write_document_manifest,
    write_prospect_brief_markdown,
)

__all__ = [
    "BriefingRequiredError",
    "AssemblyResult",
    "assemble_job",
]


# ---------------------------------------------------------------------------
# Exceptions / dataclasses
# ---------------------------------------------------------------------------

class BriefingRequiredError(RuntimeError):
    """Raised when the briefing artefact is missing or malformed.

    Subclasses :class:`RuntimeError` (not ``FileNotFoundError`` or
    ``ValueError``) so callers can branch on assembly-specific
    failures without conflating them with the storage layer's
    ``JobNotFound`` / ``pydantic.ValidationError``. The original
    exception is preserved via ``__cause__`` when available.
    """


@dataclass(frozen=True)
class AssemblyResult:
    """Return value of :func:`assemble_job`.

    Carries paths to both written files plus the manifest dict so the
    caller can inspect provenance without re-reading the manifest from
    disk. The Markdown text is intentionally NOT included — callers
    that want the body can read the file.
    """

    markdown_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


# ---------------------------------------------------------------------------
# Section identifiers — fixed order
# ---------------------------------------------------------------------------

_SECTION_HEADER = "header"
_SECTION_SNAPSHOT = "briefing_snapshot"
_SECTION_OPPORTUNITY = "opportunity"
_SECTION_PRODUCT_MAPPING = "product_mapping"
_SECTION_BENEFITS = "benefits"
_SECTION_FAQ = "faq"
_SECTION_OBJECTIONS = "objections"
_SECTION_CRITIC = "critic_report"
_SECTION_SOURCES = "sources_and_gaps"

# The Markdown is rendered in exactly this order. Adding or reordering
# a section is a deliberate schema bump — the manifest mirrors this
# list so a downstream consumer can diff it.
_SECTION_KEYS: tuple[str, ...] = (
    _SECTION_HEADER,
    _SECTION_SNAPSHOT,
    _SECTION_OPPORTUNITY,
    _SECTION_PRODUCT_MAPPING,
    _SECTION_BENEFITS,
    _SECTION_FAQ,
    _SECTION_OBJECTIONS,
    _SECTION_CRITIC,
    _SECTION_SOURCES,
)


_INPUT_ARTEFACT_KEYS: tuple[tuple[str, str], ...] = (
    ("briefing", "briefing.json"),
    ("product_mapping", "product_mapping.json"),
    ("benefits", "benefits.json"),
    ("faq", "faq.json"),
    ("objections", "objections.json"),
    ("critic_report", "critic_report.json"),
)


_WATERMARK = "**INTERNAL DRAFT — not for external release.**"

_BANNER_READY_WITH_WARNINGS = (
    "> :warning: Critic verdict: READY_WITH_WARNINGS — "
    "review the warnings in the critic report section before sending."
)

_BANNER_NEEDS_REVISION = (
    "> :warning: Critic verdict: NEEDS_REVISION — "
    "at least one BLOCKING issue. INTERNAL DRAFT, do not release."
)


# ---------------------------------------------------------------------------
# Manifest construction helpers
# ---------------------------------------------------------------------------

def _initial_section_records() -> list[dict[str, Any]]:
    """Seed the manifest's per-section records as 'rendered=True'.

    Each section flips its own record to ``rendered=False`` (with a
    reason) if its source artefact is missing or malformed. The
    header / opportunity / sources sections live on the briefing and
    are always rendered once the briefing is in hand.
    """
    return [
        {"key": key, "rendered": True, "reason": None}
        for key in _SECTION_KEYS
    ]


def _set_section_skipped(
    sections: list[dict[str, Any]], key: str, reason: str
) -> None:
    for rec in sections:
        if rec["key"] == key:
            rec["rendered"] = False
            rec["reason"] = reason
            return


def _iso(dt: datetime) -> str:
    """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ``.

    Mirrors ``backend.jobs.storage._iso`` but reimplemented locally
    so the assembler does not import a private symbol from a sibling
    module. Same single-format policy: tz-aware → UTC; naive
    assumed UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Markdown rendering helpers
# ---------------------------------------------------------------------------

def _md_escape_inline(text: str) -> str:
    """Minimal inline escaping for table cells.

    Markdown tables use ``|`` as the column separator; an unescaped
    ``|`` inside a cell would silently spawn a new column. Newlines in
    a cell would break the row. We replace both with safer surrogates
    so a tampered or unusually-shaped briefing string cannot corrupt
    the table shape.
    """
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _format_confidence(confidence: Any) -> str:
    """Render a ``Confidence`` enum (or any ``.value`` carrier) as text."""
    value = getattr(confidence, "value", None)
    return str(value) if value is not None else str(confidence)


def _stub_section(heading: str, *, reason: str) -> list[str]:
    """One-liner stub used when an artefact is missing or malformed."""
    return [
        f"## {heading}",
        "",
        f"> _Section not rendered — {reason}._",
        "",
    ]


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_header(briefing: Briefing, verdict: Verdict | None) -> list[str]:
    """Lines 0..N for the watermark, title, URL, and verdict banner.

    Banner appears immediately after the title block so it is visible
    in any preview that truncates after the first ~200 chars. The
    INTERNAL DRAFT watermark sits on line 1 so it survives a copy of
    any prefix.
    """
    company_name = briefing.company_name
    company_url = str(briefing.company_url)

    lines: list[str] = [
        _WATERMARK,
        "",
        f"# {company_name}",
        "",
        f"- **Company URL:** <{company_url}>",
        f"- **Briefing compiled:** {briefing.compiled_at.isoformat()}",
    ]
    if briefing.user_context:
        lines.append(
            f"- **Operator context:** {briefing.user_context}"
        )
    lines.append("")

    if verdict is Verdict.NEEDS_REVISION:
        lines.append(_BANNER_NEEDS_REVISION)
        lines.append("")
    elif verdict is Verdict.READY_WITH_WARNINGS:
        lines.append(_BANNER_READY_WITH_WARNINGS)
        lines.append("")

    return lines


def _render_snapshot(briefing: Briefing) -> list[str]:
    s = briefing.snapshot
    lines = [
        "## Briefing snapshot",
        "",
        f"**Headline:** {s.headline}",
        "",
        f"**Description:** {s.one_line_desc}",
        "",
    ]
    fields = (
        ("Sector", s.sector),
        ("Headcount band", s.headcount_band),
        ("HQ country", s.hq_country),
        ("Ownership", s.ownership),
        ("Lab maturity", s.lab_maturity.value),
        ("Confidence", _format_confidence(s.confidence)),
    )
    for label, value in fields:
        if value is None or value == "":
            continue
        lines.append(f"- **{label}:** {value}")
    lines.append("")
    lines.append(f"**Why interesting to ANS:** {s.why_interesting_to_ans}")
    lines.append("")
    return lines


def _render_opportunity(briefing: Briefing) -> list[str]:
    opp = briefing.opportunity
    lines = [
        "## Opportunity summary",
        "",
        f"**Recommended angle:** {opp.recommended_angle}",
        "",
    ]
    if opp.buying_cycle_stage:
        lines.append(f"- **Buying-cycle stage:** {opp.buying_cycle_stage}")
        lines.append("")
    if opp.watch_outs:
        lines.append("**Watch-outs:**")
        lines.append("")
        for w in opp.watch_outs:
            lines.append(f"- {w}")
        lines.append("")
    if opp.ranked_needs:
        lines.append("### Ranked needs")
        lines.append("")
        for need in opp.ranked_needs:
            lines.append(
                f"#### Priority {need.priority} — {need.summary}"
            )
            lines.append("")
            lines.append(need.detail)
            lines.append("")
            lines.append(f"- **Entry angle:** {need.entry_angle}")
            if need.suggested_products:
                lines.append(
                    "- **Suggested product categories:** "
                    + ", ".join(need.suggested_products)
                )
            if need.watch_outs:
                lines.append("- **Watch-outs:**")
                for w in need.watch_outs:
                    lines.append(f"    - {w}")
            lines.append(
                f"- **Confidence:** {_format_confidence(need.confidence)}"
            )
            if need.source_indices:
                lines.append(
                    "- **Source indices:** "
                    + ", ".join(str(i) for i in need.source_indices)
                )
            lines.append("")
    else:
        lines.append("_No ranked needs recorded._")
        lines.append("")
    return lines


def _render_product_mapping(mapping: ProductMapping) -> list[str]:
    lines = ["## Product mapping", ""]
    lines.append(f"- **Mapped at:** {mapping.mapped_at.isoformat()}")
    lines.append("")
    if mapping.matches:
        lines.append("### Matches")
        lines.append("")
        for match in mapping.matches:
            lines.append(
                f"#### Need priority {match.need_priority} — "
                f"{match.need_summary}"
            )
            lines.append("")
            lines.append(f"**Use-case framing:** {match.use_case_framing}")
            lines.append("")
            lines.append(f"**Why this fits:** {match.why_this_fits}")
            lines.append("")
            lines.append(
                f"- **Confidence:** {_format_confidence(match.confidence)}"
            )
            lines.append("- **Products:**")
            for product in match.products:
                refs = (
                    ", ".join(str(i) for i in product.knowledge_excerpt_refs)
                    if product.knowledge_excerpt_refs else "—"
                )
                lines.append(
                    f"    - {product.product_name} "
                    f"({product.product_line.value}) — excerpt refs: {refs}"
                )
            lines.append("")
    else:
        lines.append("_No need-to-product matches recorded._")
        lines.append("")
    if mapping.unmatched_needs:
        lines.append("### Unmatched needs")
        lines.append("")
        for um in mapping.unmatched_needs:
            lines.append(
                f"- Priority {um.need_priority} — {um.need_summary}: "
                f"{um.reason}"
            )
        lines.append("")
    if mapping.knowledge_excerpts:
        lines.append("### Knowledge excerpts referenced")
        lines.append("")
        for i, exc in enumerate(mapping.knowledge_excerpts):
            lines.append(
                f"{i}. `{exc.source_file.value}` — **{exc.heading}**: "
                f"{exc.rationale}"
            )
        lines.append("")
    if mapping.gaps:
        lines.append("### Mapping gaps")
        lines.append("")
        for g in mapping.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


def _render_benefits_section(
    title: str, section: BenefitsSection
) -> list[str]:
    lines = [
        f"### {title} — {section.heading}",
        "",
        f"_Audience: {section.audience.value}_",
        "",
        section.summary,
        "",
    ]
    for i, claim in enumerate(section.body):
        lines.append(f"- **Claim {i + 1}:** {claim.claim}")
        lines.append(f"    - **Detail:** {claim.detail}")
        lines.append(f"    - **Why it matters:** {claim.why_it_matters}")
        lines.append(
            f"    - **Confidence:** {_format_confidence(claim.confidence)}"
        )
        if claim.knowledge_excerpt_refs:
            lines.append(
                "    - **Knowledge excerpt refs:** "
                + ", ".join(str(i) for i in claim.knowledge_excerpt_refs)
            )
        if claim.briefing_source_refs:
            lines.append(
                "    - **Briefing source refs:** "
                + ", ".join(str(i) for i in claim.briefing_source_refs)
            )
    lines.append("")
    return lines


def _render_benefits(brief: BenefitsBrief) -> list[str]:
    lines = ["## Benefits", ""]
    lines.append(f"- **Written at:** {brief.written_at.isoformat()}")
    lines.append("")
    lines.extend(_render_benefits_section(
        "Executive summary", brief.executive_summary
    ))
    lines.extend(_render_benefits_section(
        "Technical fit", brief.technical_fit
    ))
    lines.extend(_render_benefits_section(
        "Business case", brief.business_case
    ))
    if brief.gaps:
        lines.append("### Benefits gaps")
        lines.append("")
        for g in brief.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


def _render_faq(faq: FAQDocument) -> list[str]:
    lines = ["## FAQ", ""]
    lines.append(f"- **Written at:** {faq.written_at.isoformat()}")
    lines.append("")
    for i, entry in enumerate(faq.entries):
        lines.append(
            f"### Q{i + 1}. {entry.question} _({entry.category.value})_"
        )
        lines.append("")
        lines.append(entry.answer)
        lines.append("")
        lines.append(
            f"- **Confidence:** {_format_confidence(entry.confidence)}"
        )
        if entry.knowledge_excerpt_refs:
            lines.append(
                "- **Knowledge excerpt refs:** "
                + ", ".join(str(i) for i in entry.knowledge_excerpt_refs)
            )
        if entry.briefing_source_refs:
            lines.append(
                "- **Briefing source refs:** "
                + ", ".join(str(i) for i in entry.briefing_source_refs)
            )
        lines.append("")
    if faq.gaps:
        lines.append("### FAQ gaps")
        lines.append("")
        for g in faq.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


def _render_objections(register: ObjectionsRegister) -> list[str]:
    lines = ["## Objections", ""]
    lines.append(f"- **Written at:** {register.written_at.isoformat()}")
    lines.append("")
    lines.append(
        "| # | Category | Objection | Underlying concern | Response | "
        "Escalation path | Confidence |"
    )
    lines.append(
        "|---|----------|-----------|--------------------|----------|"
        "-----------------|------------|"
    )
    for i, row in enumerate(register.rows):
        lines.append(
            "| {n} | {cat} | {obj} | {concern} | {resp} | {esc} | {conf} |"
            .format(
                n=i + 1,
                cat=row.category.value,
                obj=_md_escape_inline(row.objection),
                concern=_md_escape_inline(row.underlying_concern),
                resp=_md_escape_inline(row.response),
                esc=_md_escape_inline(row.escalation_path),
                conf=_format_confidence(row.confidence),
            )
        )
    lines.append("")
    if register.gaps:
        lines.append("### Objections gaps")
        lines.append("")
        for g in register.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


def _render_critic(report: Stage2CriticReport) -> list[str]:
    lines = ["## Critic report summary", ""]
    lines.append(f"- **Reviewed at:** {report.reviewed_at.isoformat()}")
    lines.append(f"- **Verdict:** {report.verdict.value}")
    lines.append("")
    lines.append("**Summary:**")
    lines.append("")
    lines.append(report.summary)
    lines.append("")

    if report.issues:
        # Group by severity in the fixed order BLOCKING → WARNING →
        # INFO so the verdict-aligned issues sit at the top. Within a
        # severity, render in original on-disk order.
        groups: dict[Severity, list[tuple[int, CriticIssue]]] = {
            Severity.BLOCKING: [],
            Severity.WARNING: [],
            Severity.INFO: [],
        }
        for i, issue in enumerate(report.issues):
            groups[issue.severity].append((i, issue))

        lines.append("### Issues")
        lines.append("")
        for severity in (Severity.BLOCKING, Severity.WARNING, Severity.INFO):
            bucket = groups[severity]
            if not bucket:
                continue
            lines.append(f"#### {severity.value}")
            lines.append("")
            for original_index, issue in bucket:
                lines.append(
                    f"- **[{issue.artefact.value}] "
                    f"{issue.category.value}** — `{issue.locator}`: "
                    f"{issue.description}"
                )
                if issue.suggested_fix:
                    lines.append(
                        f"    - **Suggested fix:** {issue.suggested_fix}"
                    )
                # Keep the original-index reference so a downstream
                # consumer can cross-link back to the JSON.
                lines.append(
                    f"    - _Issue index in critic_report.json: "
                    f"{original_index}_"
                )
            lines.append("")
    else:
        lines.append("_No issues raised._")
        lines.append("")

    if report.gaps:
        lines.append("### Critic gaps")
        lines.append("")
        for g in report.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


def _render_sources_and_gaps(briefing: Briefing) -> list[str]:
    lines = ["## Sources and gaps", ""]
    if briefing.sources.entries:
        lines.append("### Source register")
        lines.append("")
        for i, entry in enumerate(briefing.sources.entries):
            title = entry.title or "(untitled)"
            lines.append(
                f"{i}. [{title}]({entry.url}) — retrieved "
                f"{entry.retrieved_at.isoformat()} "
                f"(confidence: {_format_confidence(entry.confidence)})"
            )
        lines.append("")
    else:
        lines.append("_No sources recorded._")
        lines.append("")
    if briefing.sources.gaps:
        lines.append("### Briefing gaps")
        lines.append("")
        for g in briefing.sources.gaps:
            lines.append(f"- {g}")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Read-with-fallback helpers for the five optional artefacts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _LoadOutcome:
    """Result of a guarded artefact read.

    ``status`` is one of ``"present"``, ``"missing"``, ``"malformed"``.
    ``model`` is the parsed Pydantic model when status is ``"present"``,
    otherwise ``None``. ``reason`` is a short human string for the
    manifest warning when status is not ``"present"``.
    """

    status: str
    model: Any
    reason: str | None


def _load_optional(reader, job_id: str, label: str) -> _LoadOutcome:
    """Run ``reader(job_id)`` and translate failures into outcomes.

    ``JobNotFound``                  → ``"missing"``
    ``json.JSONDecodeError``         → ``"malformed"`` (JSON error)
    ``pydantic.ValidationError``     → ``"malformed"`` (schema error)
    """
    try:
        model = reader(job_id)
    except JobNotFound:
        return _LoadOutcome(
            status="missing",
            model=None,
            reason=f"{label} artefact missing",
        )
    except json.JSONDecodeError as exc:
        return _LoadOutcome(
            status="malformed",
            model=None,
            reason=f"{label} artefact malformed: JSONDecodeError",
        )
    except ValidationError:
        return _LoadOutcome(
            status="malformed",
            model=None,
            reason=f"{label} artefact malformed: ValidationError",
        )
    return _LoadOutcome(status="present", model=model, reason=None)


def _stub_reason_for(status: str, label: str) -> str:
    if status == "missing":
        return f"{label} artefact missing"
    return f"{label} artefact malformed"


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def assemble_job(
    job_id: str,
    *,
    now: datetime | None = None,
) -> AssemblyResult:
    """Render ``prospect_brief.md`` and ``document_manifest.json``.

    Reads every artefact via the storage layer, renders the
    nine-section Markdown, and atomically writes both files. Returns
    an :class:`AssemblyResult` with paths and the manifest dict.

    ``now`` is the wall-clock injected for determinism in tests. The
    default is :func:`datetime.now` in UTC.

    Raises :class:`BriefingRequiredError` if the briefing artefact is
    missing or malformed. In that case nothing is written.
    """
    # ------------------------------------------------------------------
    # Briefing is mandatory — surface failures as a single typed error.
    # ------------------------------------------------------------------
    try:
        briefing = read_briefing(job_id)
    except JobNotFound as exc:
        raise BriefingRequiredError(
            f"briefing.json is missing for job {job_id}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BriefingRequiredError(
            f"briefing.json is malformed for job {job_id}"
        ) from exc
    except ValidationError as exc:
        raise BriefingRequiredError(
            f"briefing.json fails schema validation for job {job_id}"
        ) from exc

    # ------------------------------------------------------------------
    # Five optional artefacts.
    # ------------------------------------------------------------------
    mapping_outcome = _load_optional(
        read_product_mapping, job_id, "product_mapping"
    )
    benefits_outcome = _load_optional(read_benefits, job_id, "benefits")
    faq_outcome = _load_optional(read_faq, job_id, "faq")
    objections_outcome = _load_optional(
        read_objections, job_id, "objections"
    )
    critic_outcome = _load_optional(
        read_critic_report, job_id, "critic_report"
    )

    verdict: Verdict | None = (
        critic_outcome.model.verdict
        if critic_outcome.status == "present"
        else None
    )

    # ------------------------------------------------------------------
    # Build the manifest skeleton — sections and artefacts default to
    # "rendered/present"; per-section guards flip them to skipped.
    # ------------------------------------------------------------------
    sections: list[dict[str, Any]] = _initial_section_records()
    warnings: list[str] = []
    artefacts: dict[str, dict[str, Any]] = {}
    for key, filename in _INPUT_ARTEFACT_KEYS:
        artefacts[key] = {
            "present": True,
            "filename": filename,
            "status": "present",
        }

    # Briefing is present by definition here.
    artefacts["briefing"]["status"] = "present"

    # ------------------------------------------------------------------
    # Render the Markdown body section-by-section.
    # ------------------------------------------------------------------
    body: list[str] = []

    body.extend(_render_header(briefing, verdict))
    body.extend(_render_snapshot(briefing))
    body.extend(_render_opportunity(briefing))

    # Product mapping
    if mapping_outcome.status == "present":
        body.extend(_render_product_mapping(mapping_outcome.model))
    else:
        artefacts["product_mapping"]["present"] = (
            mapping_outcome.status != "missing"
        )
        artefacts["product_mapping"]["status"] = mapping_outcome.status
        warnings.append(mapping_outcome.reason or "product_mapping artefact unavailable")
        _set_section_skipped(
            sections,
            _SECTION_PRODUCT_MAPPING,
            f"artefact_{mapping_outcome.status}",
        )
        body.extend(_stub_section(
            "Product mapping",
            reason=_stub_reason_for(
                mapping_outcome.status, "product_mapping"
            ),
        ))

    # Benefits
    if benefits_outcome.status == "present":
        body.extend(_render_benefits(benefits_outcome.model))
    else:
        artefacts["benefits"]["present"] = (
            benefits_outcome.status != "missing"
        )
        artefacts["benefits"]["status"] = benefits_outcome.status
        warnings.append(benefits_outcome.reason or "benefits artefact unavailable")
        _set_section_skipped(
            sections,
            _SECTION_BENEFITS,
            f"artefact_{benefits_outcome.status}",
        )
        body.extend(_stub_section(
            "Benefits",
            reason=_stub_reason_for(benefits_outcome.status, "benefits"),
        ))

    # FAQ
    if faq_outcome.status == "present":
        body.extend(_render_faq(faq_outcome.model))
    else:
        artefacts["faq"]["present"] = faq_outcome.status != "missing"
        artefacts["faq"]["status"] = faq_outcome.status
        warnings.append(faq_outcome.reason or "faq artefact unavailable")
        _set_section_skipped(
            sections, _SECTION_FAQ, f"artefact_{faq_outcome.status}"
        )
        body.extend(_stub_section(
            "FAQ",
            reason=_stub_reason_for(faq_outcome.status, "faq"),
        ))

    # Objections
    if objections_outcome.status == "present":
        body.extend(_render_objections(objections_outcome.model))
    else:
        artefacts["objections"]["present"] = (
            objections_outcome.status != "missing"
        )
        artefacts["objections"]["status"] = objections_outcome.status
        warnings.append(objections_outcome.reason or "objections artefact unavailable")
        _set_section_skipped(
            sections,
            _SECTION_OBJECTIONS,
            f"artefact_{objections_outcome.status}",
        )
        body.extend(_stub_section(
            "Objections",
            reason=_stub_reason_for(
                objections_outcome.status, "objections"
            ),
        ))

    # Critic report
    if critic_outcome.status == "present":
        body.extend(_render_critic(critic_outcome.model))
    else:
        artefacts["critic_report"]["present"] = (
            critic_outcome.status != "missing"
        )
        artefacts["critic_report"]["status"] = critic_outcome.status
        warnings.append(critic_outcome.reason or "critic_report artefact unavailable")
        _set_section_skipped(
            sections,
            _SECTION_CRITIC,
            f"artefact_{critic_outcome.status}",
        )
        body.extend(_stub_section(
            "Critic report summary",
            reason=_stub_reason_for(
                critic_outcome.status, "critic_report"
            ),
        ))

    body.extend(_render_sources_and_gaps(briefing))

    # ------------------------------------------------------------------
    # Compose final text — LF endings, single trailing newline, no BOM.
    # ------------------------------------------------------------------
    markdown_text = "\n".join(body).rstrip("\n") + "\n"
    markdown_bytes = markdown_text.encode("utf-8")
    markdown_sha256 = hashlib.sha256(markdown_bytes).hexdigest()

    # ------------------------------------------------------------------
    # Write both files.
    # ------------------------------------------------------------------
    when = now if now is not None else datetime.now(timezone.utc)

    md_path = write_prospect_brief_markdown(job_id, markdown_text)

    # Step 35: bump schema_version 1 → 2 and reserve the additive ``exports``
    # field as an empty list. The assembler never populates ``exports`` —
    # that is exclusively the deterministic export layer's responsibility
    # (``backend.exporters``). Step 37 amendment: a re-assembly must
    # PRESERVE any existing ``exports[]`` entries from a previously-written
    # manifest so the operator does not silently lose lifecycle
    # information (the on-disk PDF survives the re-assembly, so its
    # provenance entry must survive too — the lifecycle layer will then
    # surface it as ``source_entry_drift`` if the Markdown changed).
    #
    # Architectural note (carried for future): manifest schema evolution
    # is likely to become its own concern. No action here, but resist
    # accumulating schema-versioning logic inside ``markdown.py``.
    preserved_exports: list[dict[str, Any]] = []
    try:
        existing = read_document_manifest(job_id)
    except (JobNotFound, json.JSONDecodeError):
        existing = None
    if isinstance(existing, dict):
        prior = existing.get("exports")
        if isinstance(prior, list):
            for entry in prior:
                if isinstance(entry, dict):
                    preserved_exports.append(dict(entry))

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "job_id": job_id,
        "company_name": briefing.company_name,
        "company_url": str(briefing.company_url),
        "generated_at": _iso(when),
        "markdown_filename": "prospect_brief.md",
        "markdown_sha256": markdown_sha256,
        "markdown_byte_length": len(markdown_bytes),
        "sections": sections,
        "artefacts": artefacts,
        "outputs": [
            {"key": "markdown", "filename": "prospect_brief.md"},
            {"key": "manifest", "filename": "document_manifest.json"},
        ],
        "critic_verdict": verdict.value if verdict is not None else None,
        "warnings": warnings,
        "exports": preserved_exports,
    }

    manifest_path = write_document_manifest(job_id, manifest)

    return AssemblyResult(
        markdown_path=md_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )
