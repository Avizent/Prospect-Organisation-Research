"""Tests for :func:`backend.jobs.approval.apply_briefing_edit`.

The function merges a typed :class:`BriefingPatch` into the
on-disk briefing and re-validates the merged result. Four
properties must hold:

* The edit only applies when the job is in
  :attr:`JobState.USER_EDITING` — every other state raises
  :class:`EditNotAllowed`.
* Top-level fields present in the patch *replace* the prior value;
  fields absent from the patch are left untouched.
* A patch that introduces an out-of-range ``source_indices`` is
  rejected by the top-level :class:`Briefing` validator
  (:class:`pydantic.ValidationError`), and the on-disk briefing is
  *not* mutated.
* Edits never apply a state transition — multiple successive edits
  within one ``user_editing`` session stay in ``user_editing``.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from backend.agents.briefing_models import (
    Briefing,
    BriefingClaim,
    BusinessContext,
    Confidence,
    LabMaturity,
    Snapshot,
)
from backend.jobs.approval import (
    BriefingPatch,
    EditNotAllowed,
    apply_briefing_edit,
)
from backend.jobs.state import JobState
from backend.jobs.storage import (
    JobNotFound,
    read_briefing,
    read_state,
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_user_context_patch_round_trips(editing_job: str) -> None:
    """A patch that sets only ``user_context`` leaves every other
    section identical to the pre-edit briefing."""
    before = read_briefing(editing_job)
    patch = BriefingPatch(user_context="Lead with the manufacturing angle.")

    updated = apply_briefing_edit(job_id=editing_job, patch=patch)

    assert updated.user_context == "Lead with the manufacturing angle."
    # Sections untouched.
    assert updated.snapshot == before.snapshot
    assert updated.business_context == before.business_context
    assert updated.it_landscape == before.it_landscape
    assert updated.key_people == before.key_people
    assert updated.opportunity == before.opportunity
    assert updated.sources == before.sources

    # On-disk briefing now matches the returned object.
    reloaded = read_briefing(editing_job)
    assert reloaded.user_context == "Lead with the manufacturing angle."


def test_section_replacement_replaces_wholesale(editing_job: str) -> None:
    """Setting ``snapshot`` on the patch replaces the snapshot section."""
    new_snapshot = Snapshot(
        headline="Acme Ltd — fully edited headline",
        one_line_desc="Industrial controls manufacturer (edited).",
        sector="Industrial manufacturing",
        headcount_band="1000-5000",
        hq_country="United Kingdom",
        ownership="Private",
        lab_maturity=LabMaturity.MATURE,
        why_interesting_to_ans="Operator-supplied angle.",
        confidence=Confidence.MEDIUM,
    )
    patch = BriefingPatch(snapshot=new_snapshot)

    updated = apply_briefing_edit(job_id=editing_job, patch=patch)

    assert updated.snapshot.headline == "Acme Ltd — fully edited headline"
    assert updated.snapshot.lab_maturity is LabMaturity.MATURE
    assert updated.snapshot.confidence is Confidence.MEDIUM


def test_edit_does_not_change_job_state(editing_job: str) -> None:
    before = read_state(editing_job)
    apply_briefing_edit(
        job_id=editing_job,
        patch=BriefingPatch(user_context="x"),
    )
    after = read_state(editing_job)
    assert after.current_state is JobState.USER_EDITING
    # No transition record was appended either.
    assert len(after.transitions) == len(before.transitions)


def test_multiple_successive_edits_compose(editing_job: str) -> None:
    """Two edits within one ``user_editing`` session both apply."""
    apply_briefing_edit(
        job_id=editing_job,
        patch=BriefingPatch(user_context="first edit"),
    )
    apply_briefing_edit(
        job_id=editing_job,
        patch=BriefingPatch(
            opportunity=read_briefing(editing_job).opportunity.model_copy(
                update={"recommended_angle": "Edited recommended angle."}
            ),
        ),
    )
    final = read_briefing(editing_job)
    assert final.user_context == "first edit"
    assert final.opportunity.recommended_angle == "Edited recommended angle."


# ---------------------------------------------------------------------------
# Guards — state machine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name",
    [
        "created_job",
        "researching_job",
        "briefing_ready_job",
        "regenerating_job",
        "approved_job",
    ],
)
def test_illegal_state_raises(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    job_id: str = request.getfixturevalue(fixture_name)
    with pytest.raises(EditNotAllowed) as exc_info:
        apply_briefing_edit(
            job_id=job_id,
            patch=BriefingPatch(user_context="should not apply"),
        )
    assert exc_info.value.operation == "apply_briefing_edit"


# ---------------------------------------------------------------------------
# Guards — patch type
# ---------------------------------------------------------------------------

def test_non_briefing_patch_raises_typeerror(editing_job: str) -> None:
    """The function only accepts :class:`BriefingPatch` — a raw dict
    or any other type is a programming error."""
    with pytest.raises(TypeError):
        apply_briefing_edit(
            job_id=editing_job,
            patch={"user_context": "x"},  # type: ignore[arg-type]
        )


def test_patch_extra_field_rejected_at_construction() -> None:
    """``BriefingPatch`` is ``extra='forbid'`` — typos surface early."""
    with pytest.raises(ValidationError):
        BriefingPatch(extra_field="oops")  # type: ignore[call-arg]


def test_patch_omits_identity_fields() -> None:
    """``company_name`` / ``company_url`` / ``compiled_at`` are not on
    the patch — editing them would lie about provenance."""
    with pytest.raises(ValidationError):
        BriefingPatch(company_name="Different Co")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Revalidation — out-of-range source_indices
# ---------------------------------------------------------------------------

def test_out_of_range_source_index_rejected(editing_job: str) -> None:
    """A patch that references a source index beyond
    ``sources.entries`` is rejected by the top-level :class:`Briefing`
    validator and the on-disk briefing is not mutated."""
    before = read_briefing(editing_job)
    n = len(before.sources.entries)  # 1 entry → index 1 is out of range

    bad_business = BusinessContext(
        news=[
            BriefingClaim(
                summary="bogus citation",
                detail="references a source that does not exist",
                confidence=Confidence.HIGH,
                source_indices=[n],  # out of range
            )
        ],
    )
    patch = BriefingPatch(business_context=bad_business)

    with pytest.raises(ValidationError):
        apply_briefing_edit(job_id=editing_job, patch=patch)

    # On-disk briefing must be unchanged.
    after = read_briefing(editing_job)
    assert after.business_context == before.business_context


# ---------------------------------------------------------------------------
# Reload errors propagate
# ---------------------------------------------------------------------------

def test_missing_briefing_file_raises_job_not_found(
    editing_job: str, isolated_jobs_root
) -> None:
    """If briefing.json is missing the state guard passes (the state
    file lives separately) and :func:`read_briefing` raises
    :class:`JobNotFound`. We re-raise unchanged."""
    briefing_path = isolated_jobs_root / editing_job / "briefing.json"
    briefing_path.unlink()
    with pytest.raises(JobNotFound):
        apply_briefing_edit(
            job_id=editing_job,
            patch=BriefingPatch(user_context="x"),
        )


def test_compiled_at_preserved_across_edit(editing_job: str) -> None:
    """Identity fields are not on the patch, so they must be unchanged
    after a successful edit."""
    before = read_briefing(editing_job)
    apply_briefing_edit(
        job_id=editing_job,
        patch=BriefingPatch(user_context="anything"),
    )
    after = read_briefing(editing_job)
    assert after.company_name == before.company_name
    assert after.company_url == before.company_url
    assert after.compiled_at == before.compiled_at
    # Sanity check — the fixture's compiled_at is a real date.
    assert isinstance(after.compiled_at, date)
