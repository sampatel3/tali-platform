"""Regression coverage for live related-role roster and scoring boundaries."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.sister_role_service import (
    ensure_sister_evaluations,
    text_fingerprint,
)
from tests.conftest import auth_headers


def _seed_related_role(db, *, organization_id: int) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name="Canonical ATS owner",
        source="workable",
        workable_job_id="RELATED-ROSTER-SAFETY",
        workable_job_data={"state": "published"},
        job_spec_text="Canonical role specification.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text="Independent related role specification with Python.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
    )
    db.add(related)
    db.flush()
    return owner, related


def _seed_application(
    db,
    *,
    organization_id: int,
    role_id: int,
    suffix: str,
    deleted: bool = False,
    candidate_deleted: bool = False,
) -> CandidateApplication:
    deleted_at = datetime.now(timezone.utc)
    candidate = Candidate(
        organization_id=organization_id,
        email=f"related-roster-{suffix}@example.com",
        full_name=f"Related roster {suffix}",
        cv_text="Python production systems experience.",
        deleted_at=deleted_at if candidate_deleted else None,
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role_id,
        source="workable",
        pipeline_stage="review",
        application_outcome="open",
        cv_text=candidate.cv_text,
        deleted_at=deleted_at if deleted else None,
    )
    db.add(application)
    db.flush()
    return application


def test_restored_excluded_evaluation_reactivates_without_fingerprint_change(
    client, db
):
    _, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(
        db, organization_id=user.organization_id
    )
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="restored",
    )
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=related.id,
        source_application_id=application.id,
        status="done",
        spec_fingerprint=text_fingerprint(related.job_spec_text),
        cv_fingerprint=text_fingerprint(application.cv_text),
        role_fit_score=91,
        summary="Prior result retained for audit.",
        details={"role_fit_score": 91},
        scored_at=datetime.now(timezone.utc),
    )
    db.add(evaluation)
    db.commit()

    application.deleted_at = datetime.now(timezone.utc)
    db.commit()
    ensure_sister_evaluations(db, related)
    assert evaluation.status == "excluded"
    assert evaluation.role_fit_score == 91

    application.deleted_at = None
    db.commit()
    counts = ensure_sister_evaluations(db, related)

    assert counts == {"total": 1, "pending": 1, "unscorable": 0}
    assert evaluation.status == "pending"
    assert evaluation.role_fit_score is None
    assert evaluation.last_error_code is None
    assert evaluation.history[-1]["role_fit_score"] == 91
    assert evaluation.history[-1]["summary"] == "Prior result retained for audit."


def test_scoring_status_top_candidates_excludes_invalid_live_roster_rows(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(
        db, organization_id=user.organization_id
    )
    reassigned_owner = Role(
        organization_id=user.organization_id,
        name="Different canonical role",
    )
    db.add(reassigned_owner)
    db.flush()

    valid = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="valid-top",
    )
    deleted_candidate = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="deleted-candidate",
        candidate_deleted=True,
    )
    deleted_application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="deleted-application",
        deleted=True,
    )
    reassigned = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=reassigned_owner.id,
        suffix="reassigned",
    )
    for application, score in (
        (valid, 80),
        (deleted_candidate, 99),
        (deleted_application, 98),
        (reassigned, 97),
    ):
        db.add(
            SisterRoleEvaluation(
                organization_id=user.organization_id,
                role_id=related.id,
                source_application_id=application.id,
                status="done",
                spec_fingerprint="spec",
                role_fit_score=score,
            )
        )
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/sister-scoring-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["top_candidates"] == [
        {
            "application_id": valid.id,
            "candidate_name": valid.candidate.full_name,
            "score": 80.0,
        }
    ]


@pytest.mark.parametrize("invalid_owner_state", ["deleted", "wrong_org", "related"])
def test_scoring_status_top_candidates_requires_live_standard_owner(
    client, db, invalid_owner_state
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(
        db, organization_id=user.organization_id
    )
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix=f"invalid-owner-{invalid_owner_state}",
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            source_application_id=application.id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=99,
        )
    )
    if invalid_owner_state == "deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    elif invalid_owner_state == "wrong_org":
        other = Organization(
            name="Other related-roster organization",
            slug=f"other-related-roster-{related.id}",
        )
        db.add(other)
        db.flush()
        owner.organization_id = other.id
    else:
        owner.role_kind = ROLE_KIND_SISTER
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/sister-scoring-status",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["top_candidates"] == []


def test_related_applications_applied_filter_includes_missing_evaluation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_related_role(
        db, organization_id=user.organization_id
    )
    application = _seed_application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="missing-evaluation",
    )
    db.commit()

    response = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"pipeline_stage": "applied"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [application.id]
    assert response.json()[0]["pipeline_stage"] == "applied"


@pytest.mark.parametrize(
    "revocation",
    [
        "application_deleted",
        "candidate_deleted",
        "application_reassigned",
        "application_wrong_org",
        "candidate_wrong_org",
        "owner_deleted",
        "owner_wrong_org",
        "owner_related",
        "evaluation_wrong_org",
    ],
)
def test_delayed_scoring_worker_excludes_rows_outside_live_roster(db, revocation):
    organization = Organization(
        name=f"Delayed related scoring {revocation}",
        slug=f"delayed-related-scoring-{revocation}-{id(db)}",
    )
    other_organization = Organization(
        name=f"Other delayed related scoring {revocation}",
        slug=f"other-delayed-related-scoring-{revocation}-{id(db)}",
    )
    db.add_all([organization, other_organization])
    db.flush()
    owner, related = _seed_related_role(
        db, organization_id=int(organization.id)
    )
    application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix=f"delayed-{revocation}",
    )
    evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(application.id),
        status="pending",
        spec_fingerprint="queued-before-roster-change",
    )
    db.add(evaluation)
    db.flush()

    if revocation == "application_deleted":
        application.deleted_at = datetime.now(timezone.utc)
    elif revocation == "candidate_deleted":
        application.candidate.deleted_at = datetime.now(timezone.utc)
    elif revocation == "application_reassigned":
        replacement_owner = Role(
            organization_id=int(organization.id),
            name="Replacement owner",
        )
        db.add(replacement_owner)
        db.flush()
        application.role_id = int(replacement_owner.id)
    elif revocation == "application_wrong_org":
        application.organization_id = int(other_organization.id)
    elif revocation == "candidate_wrong_org":
        application.candidate.organization_id = int(other_organization.id)
    elif revocation == "owner_deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    elif revocation == "owner_wrong_org":
        owner.organization_id = int(other_organization.id)
    elif revocation == "owner_related":
        owner.role_kind = ROLE_KIND_SISTER
    else:
        evaluation.organization_id = int(other_organization.id)
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch("app.cv_matching.holistic.run_holistic_match") as paid_call,
        patch(
            "app.services.claude_client_resolver.get_metered_client"
        ) as metered_client,
    ):
        result = score_sister_evaluation.run(int(evaluation.id))

    paid_call.assert_not_called()
    metered_client.assert_not_called()
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert result == {"status": "excluded", "evaluation_id": int(evaluation.id)}
    assert saved.status == "excluded"
    assert saved.last_error_code == "source_application_outside_owner_roster"
    assert saved.error_message == "Source application left the owner roster"
    assert saved.next_attempt_at is None
    assert saved.scored_at is not None


def test_role_wake_releases_only_authority_wait_and_preserves_provider_backoff(db):
    organization = Organization(
        name="Related retry wake safety",
        slug=f"related-retry-wake-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner, related = _seed_related_role(
        db, organization_id=int(organization.id)
    )
    authority_application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix="authority-wait",
    )
    provider_application = _seed_application(
        db,
        organization_id=int(organization.id),
        role_id=int(owner.id),
        suffix="provider-backoff",
    )
    future_retry = datetime.now(timezone.utc) + timedelta(hours=2)
    authority_evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(authority_application.id),
        status="retry_wait",
        spec_fingerprint="authority-wait",
        last_error_code="authority_blocked",
        next_attempt_at=future_retry,
    )
    provider_evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        source_application_id=int(provider_application.id),
        status="retry_wait",
        spec_fingerprint="provider-backoff",
        last_error_code="provider_scoring_failed",
        next_attempt_at=future_retry,
    )
    db.add_all([authority_evaluation, provider_evaluation])
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_role

    published: list[int] = []
    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async",
        side_effect=lambda *, args, queue: published.append(int(args[0])),
    ):
        result = score_sister_role.run(int(related.id))

    db.expire_all()
    saved_authority = db.get(SisterRoleEvaluation, int(authority_evaluation.id))
    saved_provider = db.get(SisterRoleEvaluation, int(provider_evaluation.id))
    assert result["queued"] == 1
    assert published == [int(authority_evaluation.id)]
    assert saved_authority.status == "pending"
    assert saved_authority.last_error_code is None
    assert saved_authority.next_attempt_at is None
    assert saved_provider.status == "retry_wait"
    assert saved_provider.last_error_code == "provider_scoring_failed"
    assert saved_provider.next_attempt_at is not None
    assert saved_provider.next_attempt_at.replace(tzinfo=timezone.utc) == future_retry
