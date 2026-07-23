"""Grounded analytics truth for ordinary and related logical memberships."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _roles(db, organization_id: int, suffix: str) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name=f"Owner {suffix}",
        source="workable",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name=f"Related {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    return owner, related


def _application(
    db,
    organization_id: int,
    role: Role,
    suffix: str,
    *,
    pipeline_stage: str = "applied",
    outcome: str = "open",
    external_stage: str | None = None,
    cv_score: float | None = None,
    taali_score: float | None = None,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        email=f"related-analytics-{suffix}@example.test",
        full_name=f"Candidate {suffix}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        status="applied",
        pipeline_stage=pipeline_stage,
        application_outcome=outcome,
        external_stage_normalized=external_stage,
        workable_stage=external_stage,
        cv_match_score=cv_score,
        taali_score_cache_100=taali_score,
    )
    db.add(application)
    db.flush()
    return application


def _membership(
    db,
    organization_id: int,
    role: Role,
    application: CandidateApplication,
    *,
    stage: str,
    outcome: str,
    score: float,
    deleted_at: datetime | None = None,
) -> SisterRoleEvaluation:
    evaluation = SisterRoleEvaluation(
        organization_id=organization_id,
        role_id=role.id,
        candidate_id=application.candidate_id,
        source_application_id=application.id,
        ats_application_id=application.id,
        status="done",
        pipeline_stage=stage,
        application_outcome=outcome,
        membership_source="test_ground_truth",
        spec_fingerprint=f"spec-{role.id}-{application.id}",
        role_fit_score=score,
        deleted_at=deleted_at,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def _decision(
    db,
    organization_id: int,
    role: Role,
    application: CandidateApplication,
    suffix: str,
) -> None:
    db.add(
        AgentDecision(
            organization_id=organization_id,
            role_id=role.id,
            application_id=application.id,
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="approved",
            reasoning="ground truth",
            confidence=0.9,
            model_version="test",
            prompt_version="test",
            idempotency_key=f"related-analytics:{suffix}",
        )
    )
    db.flush()


def _funnel_by_key(payload: dict) -> dict[str, int]:
    return {row["key"]: row["count"] for row in payload["funnel"]}


def test_reporting_funnel_uses_related_role_local_truth(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).one().organization_id
    owner, related = _roles(db, org_id, "reporting")
    now = datetime.now(timezone.utc)

    snapshot = _application(
        db,
        org_id,
        owner,
        "report-snapshot",
        pipeline_stage="advanced",
        outcome="rejected",
        external_stage="hired",
        cv_score=25,
    )
    _membership(
        db,
        org_id,
        related,
        snapshot,
        stage="applied",
        outcome="open",
        score=88,
    )

    direct = _application(
        db,
        org_id,
        related,
        "report-direct",
        pipeline_stage="applied",
        cv_score=12,
    )
    _membership(
        db,
        org_id,
        related,
        direct,
        stage="review",
        outcome="open",
        score=72,
    )

    deleted_evidence = _application(
        db,
        org_id,
        owner,
        "report-deleted-evidence",
    )
    deleted_evidence.deleted_at = now
    _membership(
        db,
        org_id,
        related,
        deleted_evidence,
        stage="advanced",
        outcome="open",
        score=65,
    )

    deleted_membership_source = _application(
        db,
        org_id,
        owner,
        "report-deleted-membership",
    )
    _membership(
        db,
        org_id,
        related,
        deleted_membership_source,
        stage="review",
        outcome="rejected",
        score=99,
        deleted_at=now,
    )
    db.commit()

    related_payload = client.get(
        f"/api/v1/analytics/reporting-summary?role_id={related.id}",
        headers=headers,
    ).json()
    owner_payload = client.get(
        f"/api/v1/analytics/reporting-summary?role_id={owner.id}",
        headers=headers,
    ).json()
    org_payload = client.get(
        "/api/v1/analytics/reporting-summary", headers=headers
    ).json()

    assert _funnel_by_key(related_payload) == {
        "applied": 0,
        "scored": 1,
        "invited": 0,
        "completed": 1,
        "advanced": 1,
        "rejected": 0,
    }
    assert _funnel_by_key(owner_payload)["applied"] == 1
    assert _funnel_by_key(owner_payload)["rejected"] == 1
    assert _funnel_by_key(org_payload)["scored"] == 1
    assert _funnel_by_key(org_payload)["completed"] == 1
    assert _funnel_by_key(org_payload)["advanced"] == 1
    assert _funnel_by_key(org_payload)["rejected"] == 1


def test_decisions_breakdown_uses_decision_role_and_local_state(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).one().organization_id
    owner, related = _roles(db, org_id, "decisions")
    now = datetime.now(timezone.utc)

    shared = _application(
        db,
        org_id,
        owner,
        "decision-shared",
        pipeline_stage="advanced",
        outcome="hired",
        external_stage="hired",
        taali_score=30,
    )
    _membership(
        db,
        org_id,
        related,
        shared,
        stage="review",
        outcome="rejected",
        score=88,
    )
    _decision(db, org_id, owner, shared, "owner-shared")
    _decision(db, org_id, related, shared, "related-shared")

    direct = _application(
        db,
        org_id,
        related,
        "decision-direct",
        pipeline_stage="applied",
        outcome="open",
        external_stage="applied",
        taali_score=5,
    )
    _membership(
        db,
        org_id,
        related,
        direct,
        stage="advanced",
        outcome="hired",
        score=70,
    )
    _decision(db, org_id, related, direct, "related-direct")

    deleted_evidence = _application(
        db,
        org_id,
        owner,
        "decision-deleted-evidence",
        external_stage="offer",
        taali_score=10,
    )
    deleted_evidence.deleted_at = now
    _membership(
        db,
        org_id,
        related,
        deleted_evidence,
        stage="invited",
        outcome="open",
        score=60,
    )
    db.commit()

    payload = client.get(
        "/api/v1/analytics/decisions-breakdown", headers=headers
    ).json()
    roles = {row["role_id"]: row for row in payload["roles"]}

    assert payload["totals"]["decisions"]["total"] == 3
    assert roles[owner.id]["decisions"]["total"] == 1
    assert roles[related.id]["decisions"]["total"] == 2
    assert payload["totals"]["workable_stages"] == {
        "hired": 1,
        "review": 1,
        "advanced": 1,
        "invited": 1,
    }
    assert roles[related.id]["workable_stages"] == {
        "review": 1,
        "advanced": 1,
        "invited": 1,
    }
    assert roles[owner.id]["score_stats"] == {
        "count": 1,
        "avg": 30.0,
        "median": 30.0,
        "min": 30.0,
        "max": 30.0,
        "p25": 30.0,
        "p75": 30.0,
    }
    assert roles[related.id]["score_stats"]["count"] == 3
    assert roles[related.id]["score_stats"]["avg"] == 72.7

    conversion = payload["totals"]["advance_conversion"]
    assert conversion["advanced_total"] == 3
    assert conversion["hired"] == 2
    assert conversion["rejected"] == 1
    assert conversion["by_stage"] == {"hired": 1, "review": 1, "advanced": 1}
    assert roles[related.id]["advance_conversion"]["by_stage"] == {
        "review": 1,
        "advanced": 1,
    }

    scoped = client.get(
        f"/api/v1/analytics/decisions-breakdown?role_id={related.id}",
        headers=headers,
    ).json()
    assert scoped["totals"]["decisions"]["total"] == 2
    assert scoped["totals"]["workable_stages"] == {
        "review": 1,
        "advanced": 1,
        "invited": 1,
    }
