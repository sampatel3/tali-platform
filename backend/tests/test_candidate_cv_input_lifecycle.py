"""Effective shared-CV invalidation and per-application spend authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.candidate_cv_input_lifecycle import (
    capture_candidate_cv_input_snapshot,
    invalidate_changed_candidate_cv_inputs,
)


def _role_pair(db, *, organization_id: int, suffix: str) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name=f"Owner {suffix}",
        source="manual",
        job_spec_text=f"Owner specification {suffix}",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name=f"Related {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text=f"Related specification {suffix}",
        agentic_mode_enabled=True,
    )
    db.add(related)
    db.flush()
    return owner, related


def test_explicit_cv_authority_is_scoped_to_one_changed_application(db):
    org = Organization(name="CV authority", slug="cv-authority")
    db.add(org)
    db.flush()
    old_uploaded_at = datetime.now(timezone.utc) - timedelta(days=1)
    candidate = Candidate(
        organization_id=org.id,
        email="shared-cv@example.test",
        cv_text="Old shared candidate CV",
        cv_uploaded_at=old_uploaded_at,
    )
    db.add(candidate)
    db.flush()
    owner_a, related_a = _role_pair(
        db, organization_id=org.id, suffix="target"
    )
    owner_b, related_b = _role_pair(
        db, organization_id=org.id, suffix="sibling"
    )
    target = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner_a.id,
        pipeline_stage="review",
        application_outcome="open",
        cv_text="Old shared candidate CV",
        cv_uploaded_at=old_uploaded_at,
        cv_match_score=80.0,
    )
    sibling = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner_b.id,
        pipeline_stage="review",
        application_outcome="open",
        # This application consumes Candidate.cv_text as its effective fallback.
        cv_text=None,
        cv_match_score=75.0,
    )
    db.add_all([target, sibling])
    db.flush()
    target_evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related_a.id,
        source_application_id=target.id,
        status="done",
        pipeline_stage="review",
        spec_fingerprint="old-target-spec",
        cv_fingerprint="old-target-cv",
        role_fit_score=84.0,
        scored_at=old_uploaded_at,
    )
    sibling_evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related_b.id,
        source_application_id=sibling.id,
        status="done",
        pipeline_stage="review",
        spec_fingerprint="old-sibling-spec",
        cv_fingerprint="old-sibling-cv",
        role_fit_score=82.0,
        scored_at=old_uploaded_at,
    )
    db.add_all([target_evaluation, sibling_evaluation])
    db.commit()

    before = capture_candidate_cv_input_snapshot(
        db,
        candidate=candidate,
        organization_id=int(org.id),
    )
    now = datetime.now(timezone.utc)
    candidate.cv_text = "Replacement shared candidate CV"
    candidate.cv_uploaded_at = now
    target.cv_text = candidate.cv_text
    target.cv_uploaded_at = now

    result = invalidate_changed_candidate_cv_inputs(
        db,
        candidate=candidate,
        before=before,
        reason="candidate_cv_replaced",
        queue_related_application_ids={int(target.id)},
    )
    db.commit()

    db.expire_all()
    assert result.changed_application_ids == (target.id, sibling.id)
    assert db.get(SisterRoleEvaluation, target_evaluation.id).status == "pending"
    assert (
        db.get(SisterRoleEvaluation, sibling_evaluation.id).status
        == "stale_held"
    )
    assert {
        row.application_id
        for row in db.query(CvScoreJob)
        .filter(CvScoreJob.application_id.in_((target.id, sibling.id)))
        .all()
        if row.status == "stale"
    } == {target.id, sibling.id}


def test_same_text_reupload_timestamp_is_a_real_owner_input_change(db):
    org = Organization(name="CV timestamp", slug="cv-timestamp")
    db.add(org)
    db.flush()
    old_uploaded_at = datetime.now(timezone.utc) - timedelta(days=1)
    candidate = Candidate(
        organization_id=org.id,
        email="same-text@example.test",
        cv_text="Identical extracted text",
        cv_uploaded_at=old_uploaded_at,
    )
    owner, _related = _role_pair(
        db, organization_id=org.id, suffix="same-text"
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        pipeline_stage="review",
        application_outcome="open",
        cv_text=candidate.cv_text,
        cv_uploaded_at=old_uploaded_at,
        cv_match_score=79.0,
    )
    db.add(application)
    db.commit()

    before = capture_candidate_cv_input_snapshot(
        db,
        candidate=candidate,
        organization_id=int(org.id),
    )
    now = datetime.now(timezone.utc)
    candidate.cv_uploaded_at = now
    application.cv_uploaded_at = now
    result = invalidate_changed_candidate_cv_inputs(
        db,
        candidate=candidate,
        before=before,
        reason="candidate_cv_replaced",
    )

    assert result.changed_application_ids == (application.id,)
    assert (
        db.query(CvScoreJob)
        .filter(
            CvScoreJob.application_id == application.id,
            CvScoreJob.status == "stale",
        )
        .count()
        == 1
    )
