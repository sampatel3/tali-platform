"""Regression contracts for independent related-role funnel projections."""

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.sister_role_service import (
    project_sister_application,
    related_role_pipeline_counts,
)
from tests.conftest import auth_headers


def _role_pair(db, *, organization_id: int) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name="Canonical ATS role",
        source="workable",
        workable_job_id="projection-contract",
        job_spec_text="Canonical role specification for projection contract tests.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        job_spec_text="Independent related-role specification for contract tests.",
    )
    db.add(related)
    db.flush()
    return owner, related


def _evaluation(
    db,
    *,
    owner: Role,
    related: Role,
    suffix: str,
    outcome: str,
    stage: str,
) -> SisterRoleEvaluation:
    candidate = Candidate(
        organization_id=int(owner.organization_id),
        email=f"projection-{suffix}@example.com",
        full_name=f"Projection {suffix}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(owner.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="workable",
        workable_candidate_id=f"projection-{suffix}",
        application_outcome=outcome,
    )
    db.add(application)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=int(owner.organization_id),
        role_id=int(related.id),
        source_application_id=int(application.id),
        status="done",
        pipeline_stage=stage,
        spec_fingerprint=f"spec-{suffix}",
        role_fit_score=80.0,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def test_related_projection_preserves_each_terminal_outcome(client, db):
    _headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _role_pair(db, organization_id=int(user.organization_id))
    hired = _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="hired",
        outcome="hired",
        stage="advanced",
    )
    withdrawn = _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="withdrawn",
        outcome="withdrawn",
        stage="review",
    )
    rejected = _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="rejected",
        outcome="rejected",
        stage="review",
    )

    for evaluation, outcome, availability in (
        (hired, "hired", "closed"),
        (withdrawn, "withdrawn", "closed"),
        (rejected, "rejected", "disqualified"),
    ):
        projected = project_sister_application(
            {
                "application_outcome": outcome,
                "score_summary": {},
                "taali_score": 70.0,
                "workable_disqualified": outcome == "rejected",
            },
            sister_role=related,
            owner_role=owner,
            evaluation=evaluation,
        )
        assert projected["application_outcome"] == outcome
        assert projected["related_role_availability"] == availability


def test_related_funnel_nests_in_progress_without_counting_other_closures_as_rejected(
    client, db
):
    _headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _role_pair(db, organization_id=int(user.organization_id))
    _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="in-progress",
        outcome="open",
        stage="in_assessment",
    )
    _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="hired-count",
        outcome="hired",
        stage="advanced",
    )
    _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="withdrawn-count",
        outcome="withdrawn",
        stage="review",
    )
    _evaluation(
        db,
        owner=owner,
        related=related,
        suffix="rejected-count",
        outcome="rejected",
        stage="review",
    )
    db.commit()

    counts = related_role_pipeline_counts(db, related)

    assert counts["invited"] == 1
    assert counts["in_assessment"] == 1
    assert counts["invited_delivered"] == 1
    assert counts["invited_opened"] == 1
    assert counts["rejected"] == 1
