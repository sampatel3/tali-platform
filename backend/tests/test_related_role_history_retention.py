"""Retention and validity contracts for related-role source rosters."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import (
    SISTER_EVAL_DONE,
    SISTER_EVAL_EXCLUDED,
    SisterRoleEvaluation,
)
from app.models.user import User
from app.services.sister_role_service import (
    ensure_sister_evaluations,
    related_role_pipeline_counts_bulk,
)
from tests.conftest import auth_headers


def _roles(db, *, organization_id: int) -> tuple[Role, Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name="Retention owner",
        source="workable",
        workable_job_id="related-retention-owner",
        job_spec_text="Canonical owner specification.",
    )
    alternate = Role(
        organization_id=organization_id,
        name="Reassignment destination",
        source="workable",
        workable_job_id="related-retention-alternate",
        job_spec_text="A different canonical role.",
    )
    db.add_all([owner, alternate])
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Retention related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        job_spec_text="Independent related-role specification.",
    )
    db.add(related)
    db.flush()
    return owner, alternate, related


def _source_application(
    db,
    *,
    owner: Role,
    suffix: str,
) -> tuple[Candidate, CandidateApplication]:
    candidate = Candidate(
        organization_id=int(owner.organization_id),
        email=f"related-retention-{suffix}@example.com",
        full_name=f"Retention {suffix}",
        cv_text=f"CV text for {suffix}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(owner.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="workable",
        workable_candidate_id=f"related-retention-{suffix}",
        cv_text=f"Application CV text for {suffix}",
        application_outcome="open",
    )
    db.add(application)
    db.flush()
    return candidate, application


def _evaluation(
    db,
    *,
    related: Role,
    application: CandidateApplication,
    suffix: str,
    stage: str = "review",
) -> SisterRoleEvaluation:
    evaluation = SisterRoleEvaluation(
        organization_id=int(related.organization_id),
        role_id=int(related.id),
        source_application_id=int(application.id),
        status=SISTER_EVAL_DONE,
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        pipeline_stage_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        spec_fingerprint=f"spec-{suffix}",
        cv_fingerprint=f"cv-{suffix}",
        role_fit_score=87.5,
        summary="Strong independent fit",
        details={"strengths": ["retained"]},
        history=[{"status": "done", "role_fit_score": 82.0}],
        model_version="retention-model",
        prompt_version="retention-prompt",
        trace_id=f"retention-trace-{suffix}",
        cache_hit=True,
        attempts=2,
        queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        scored_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
        dispatch_attempted_at=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        next_attempt_at=datetime(2026, 1, 1, 3, tzinfo=timezone.utc),
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def _retained_state(evaluation: SisterRoleEvaluation) -> dict[str, object]:
    return {
        "pipeline_stage": evaluation.pipeline_stage,
        "pipeline_stage_source": evaluation.pipeline_stage_source,
        "pipeline_stage_updated_at": evaluation.pipeline_stage_updated_at,
        "spec_fingerprint": evaluation.spec_fingerprint,
        "cv_fingerprint": evaluation.cv_fingerprint,
        "role_fit_score": evaluation.role_fit_score,
        "summary": evaluation.summary,
        "details": deepcopy(evaluation.details),
        "history": deepcopy(evaluation.history),
        "model_version": evaluation.model_version,
        "prompt_version": evaluation.prompt_version,
        "trace_id": evaluation.trace_id,
        "cache_hit": evaluation.cache_hit,
        "attempts": evaluation.attempts,
        "queued_at": evaluation.queued_at,
        "started_at": evaluation.started_at,
        "scored_at": evaluation.scored_at,
        "dispatch_attempted_at": evaluation.dispatch_attempted_at,
        "next_attempt_at": evaluation.next_attempt_at,
        "created_at": evaluation.created_at,
    }


@pytest.mark.parametrize(
    "roster_change",
    ["application_soft_deleted", "candidate_soft_deleted", "reassigned"],
)
def test_roster_changes_exclude_without_deleting_or_erasing_history(
    client,
    db,
    roster_change,
):
    _headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, alternate, related = _roles(
        db,
        organization_id=int(user.organization_id),
    )
    candidate, application = _source_application(
        db,
        owner=owner,
        suffix=roster_change,
    )
    evaluation = _evaluation(
        db,
        related=related,
        application=application,
        suffix=roster_change,
    )
    evaluation_id = int(evaluation.id)
    db.commit()

    if roster_change == "application_soft_deleted":
        application.deleted_at = datetime.now(timezone.utc)
    elif roster_change == "candidate_soft_deleted":
        candidate.deleted_at = datetime.now(timezone.utc)
    else:
        application.role_id = int(alternate.id)
    db.commit()
    db.refresh(evaluation)
    before = _retained_state(evaluation)

    counts = ensure_sister_evaluations(db, related)
    db.commit()
    db.expire_all()

    saved = db.get(SisterRoleEvaluation, evaluation_id)
    assert saved is not None
    assert db.query(SisterRoleEvaluation).filter_by(id=evaluation_id).count() == 1
    assert saved.status == SISTER_EVAL_EXCLUDED
    assert saved.last_error_code == "source_application_outside_owner_roster"
    assert saved.error_message == "Source application left the owner roster"
    assert _retained_state(saved) == before
    assert counts == {"total": 0, "pending": 0, "unscorable": 0}


def test_bulk_counts_ignore_deleted_candidates_deleted_apps_and_reassignments(
    client,
    db,
):
    _headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, alternate, related = _roles(
        db,
        organization_id=int(user.organization_id),
    )

    _valid_candidate, valid_application = _source_application(
        db,
        owner=owner,
        suffix="valid",
    )
    _evaluation(
        db,
        related=related,
        application=valid_application,
        suffix="valid",
        stage="in_assessment",
    )

    _deleted_app_candidate, deleted_application = _source_application(
        db,
        owner=owner,
        suffix="deleted-application",
    )
    _evaluation(
        db,
        related=related,
        application=deleted_application,
        suffix="deleted-application",
        stage="applied",
    )
    deleted_application.deleted_at = datetime.now(timezone.utc)

    deleted_candidate, deleted_candidate_application = _source_application(
        db,
        owner=owner,
        suffix="deleted-candidate",
    )
    _evaluation(
        db,
        related=related,
        application=deleted_candidate_application,
        suffix="deleted-candidate",
        stage="review",
    )
    deleted_candidate.deleted_at = datetime.now(timezone.utc)

    _reassigned_candidate, reassigned_application = _source_application(
        db,
        owner=owner,
        suffix="reassigned",
    )
    _evaluation(
        db,
        related=related,
        application=reassigned_application,
        suffix="reassigned",
        stage="advanced",
    )
    reassigned_application.role_id = int(alternate.id)
    db.commit()

    counts = related_role_pipeline_counts_bulk(db, [int(related.id)])[int(related.id)]

    assert counts == {
        "sourced": 0,
        "applied": 0,
        "scored": 0,
        "invited": 1,
        "in_assessment": 1,
        "completed": 0,
        "advanced": 0,
        "rejected": 0,
        "not_yet_decided": 0,
        "invited_delivered": 1,
        "invited_opened": 1,
    }
