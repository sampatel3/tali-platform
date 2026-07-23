"""Real-PostgreSQL truth gate for mixed logical application memberships."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.candidate_search.logical_application_scope import (
    resolve_logical_application_selection,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation


def test_postgres_logical_membership_union_preserves_independent_role_state(
    postgres_search_db,
) -> None:
    db = postgres_search_db
    marker = uuid4().hex
    organization = Organization(name=f"Logical scope {marker}", slug=marker)
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name="Owner",
        source="manual",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(organization.id),
        name="Related",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
    )
    db.add(related)
    db.flush()

    def application(label: str, role: Role, *, deleted: bool = False):
        candidate = Candidate(
            organization_id=int(organization.id),
            email=f"{label}-{marker}@test.invalid",
            full_name=label,
            cv_text=label,
        )
        db.add(candidate)
        db.flush()
        row = CandidateApplication(
            organization_id=int(organization.id),
            candidate_id=int(candidate.id),
            role_id=int(role.id),
            source="manual",
            status="applied",
            pipeline_stage="advanced",
            pipeline_stage_updated_at=datetime.now(timezone.utc),
            pipeline_stage_source="recruiter",
            application_outcome="open",
            application_outcome_updated_at=datetime.now(timezone.utc),
            taali_score_cache_100=95,
            pre_screen_score_100=95,
        )
        if deleted:
            row.deleted_at = datetime.now(timezone.utc)
        db.add(row)
        db.flush()
        return candidate, row

    shared_candidate, shared = application("shared", owner)
    soft_candidate, soft = application("soft", owner, deleted=True)
    direct_candidate, direct = application("direct", related)
    _, related_without_membership = application("related-orphan", related)
    _, owner_only = application("owner-only", owner)
    for candidate, source, stage, score, membership_source in (
        (shared_candidate, shared, "review", 41, "initial_snapshot"),
        (soft_candidate, soft, "applied", 88, "initial_snapshot"),
        (direct_candidate, direct, "invited", 77, "direct_application"),
    ):
        db.add(
            SisterRoleEvaluation(
                organization_id=int(organization.id),
                role_id=int(related.id),
                candidate_id=int(candidate.id),
                source_application_id=int(source.id),
                ats_application_id=(
                    int(source.id) if int(source.role_id) == int(owner.id) else None
                ),
                status="done",
                pipeline_stage=stage,
                pipeline_stage_source="recruiter",
                application_outcome="open",
                application_outcome_source="recruiter",
                membership_source=membership_source,
                spec_fingerprint=f"spec-{marker}",
                role_fit_score=score,
            )
        )
    db.flush()

    # Conflicting owner/related assessment scores prove the logical selection
    # keys technical and blended scores by the selected role, not by the
    # physical evidence application.
    db.add_all(
        [
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(shared_candidate.id),
                role_id=int(owner.id),
                application_id=int(shared.id),
                status=AssessmentStatus.COMPLETED,
                assessment_score=99,
                is_voided=False,
            ),
            Assessment(
                organization_id=int(organization.id),
                candidate_id=int(shared_candidate.id),
                role_id=int(related.id),
                application_id=int(shared.id),
                status=AssessmentStatus.COMPLETED,
                assessment_score=79,
                is_voided=False,
            ),
        ]
    )
    db.flush()

    selection = resolve_logical_application_selection(
        db,
        organization_id=int(organization.id),
        role_ids=[int(owner.id), int(related.id)],
    )
    query = selection.apply_membership(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization.id)
        )
    )
    rows = query.with_entities(
        CandidateApplication.id,
        selection.logical_role_id_expression(),
        selection.pipeline_stage_expression(),
        selection.score_expression("taali_score_cache_100"),
        selection.score_expression("assessment_score_cache_100"),
    ).all()
    truth = {
        (int(role_id), int(application_id)): (
            str(stage),
            float(score),
            float(assessment_score) if assessment_score is not None else None,
        )
        for application_id, role_id, stage, score, assessment_score in rows
    }

    assert len(rows) == len(truth) == 5
    assert truth[(int(owner.id), int(shared.id))] == ("advanced", 95.0, None)
    assert truth[(int(related.id), int(shared.id))] == ("review", 60.0, 79.0)
    assert truth[(int(related.id), int(soft.id))] == ("applied", 88.0, None)
    assert truth[(int(related.id), int(direct.id))] == ("invited", 77.0, None)
    assert truth[(int(owner.id), int(owner_only.id))] == ("advanced", 95.0, None)
    assert (int(related.id), int(related_without_membership.id)) not in truth
