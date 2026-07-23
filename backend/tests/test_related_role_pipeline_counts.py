"""Regression coverage for related-role pipeline aggregates."""

from __future__ import annotations

from datetime import datetime, timezone

from app.domains.assessments_runtime.pipeline_service import role_pipeline_counts
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.role_candidate_metrics import load_role_candidate_metrics
from app.services.sister_role_service import related_role_pipeline_counts_bulk
from tests.conftest import auth_headers


def _application(
    db,
    *,
    organization_id: int,
    role_id: int,
    suffix: str,
    outcome: str = "open",
    pipeline_stage: str = "applied",
    deleted_at: datetime | None = None,
    workable_disqualified: bool | None = None,
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=organization_id,
        email=f"related-counts-{suffix}@example.com",
        full_name=f"Related Counts {suffix}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization_id,
        candidate_id=candidate.id,
        role_id=role_id,
        source="workable",
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="sync",
        application_outcome=outcome,
        deleted_at=deleted_at,
        workable_disqualified=workable_disqualified,
    )
    db.add(application)
    db.flush()
    return application


def _related_role(db, *, organization_id: int, owner_id: int, suffix: str) -> Role:
    role = Role(
        organization_id=organization_id,
        name=f"Related {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner_id,
    )
    db.add(role)
    db.flush()
    return role


def _evaluation(
    db,
    *,
    organization_id: int,
    role_id: int,
    application_id: int,
    status: str,
    pipeline_stage: str = "applied",
    application_outcome: str = "open",
) -> None:
    application = db.get(CandidateApplication, application_id)
    assert application is not None
    db.add(
        SisterRoleEvaluation(
            organization_id=organization_id,
            role_id=role_id,
            candidate_id=application.candidate_id,
            source_application_id=application_id,
            ats_application_id=application_id,
            status=status,
            pipeline_stage=pipeline_stage,
            application_outcome=application_outcome,
            membership_source="test_explicit",
            spec_fingerprint=f"spec-{role_id}-{application_id}",
        )
    )


def test_related_counts_use_only_explicit_live_memberships(db):
    organization = Organization(name="Related count parity")
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=organization.id,
        name="Owner role",
        source="workable",
        workable_job_id="RELATED-COUNT-PARITY",
    )
    db.add(owner)
    db.flush()
    related_with_partial_evaluations = _related_role(
        db, organization_id=organization.id, owner_id=owner.id, suffix="Partial"
    )
    related_without_evaluations = _related_role(
        db, organization_id=organization.id, owner_id=owner.id, suffix="Missing"
    )

    evaluated_open = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="evaluated-open",
    )
    _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="missing-open",
    )
    in_assessment = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="in-assessment",
        pipeline_stage="in_assessment",
    )
    reviewed = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="reviewed",
        pipeline_stage="review",
    )
    _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="sourced-without-evaluation",
        pipeline_stage="sourced",
    )
    _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="advanced-without-evaluation",
        pipeline_stage="advanced",
    )
    _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="missing-rejected",
        outcome="rejected",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related_with_partial_evaluations.id,
        application_id=evaluated_open.id,
        status="done",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related_with_partial_evaluations.id,
        application_id=in_assessment.id,
        status="pending",
        pipeline_stage="in_assessment",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related_with_partial_evaluations.id,
        application_id=reviewed.id,
        status="pending",
        pipeline_stage="review",
    )
    db.commit()

    owner_counts = role_pipeline_counts(
        db, organization_id=organization.id, role_id=owner.id
    )
    related_counts = related_role_pipeline_counts_bulk(
        db,
        [related_with_partial_evaluations.id, related_without_evaluations.id],
    )

    partial = related_counts[related_with_partial_evaluations.id]
    assert partial["sourced"] == 0
    assert partial["applied"] == 0
    assert partial["scored"] == 1
    assert partial["invited"] == 1
    assert partial["in_assessment"] == 1
    assert partial["completed"] == 1
    assert partial["advanced"] == 0
    assert partial["rejected"] == 0

    missing = related_counts[related_without_evaluations.id]
    assert all(value == 0 for value in missing.values())

    assert owner_counts["sourced"] == 1
    assert owner_counts["rejected"] == 1
    assert partial["invited_delivered"] == 1
    assert partial["invited_opened"] == 1


def test_related_counts_use_local_outcomes_and_keep_explicit_deleted_source_membership(
    db,
):
    organization = Organization(name="Related outcome semantics")
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=organization.id,
        name="Owner outcomes",
        source="workable",
        workable_job_id="RELATED-COUNT-OUTCOMES",
    )
    db.add(owner)
    db.flush()
    related = _related_role(
        db, organization_id=organization.id, owner_id=owner.id, suffix="Outcomes"
    )

    rejected = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="rejected",
        outcome="rejected",
    )
    hired = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="hired",
        outcome="hired",
    )
    _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="withdrawn",
        outcome="withdrawn",
    )
    deleted_rejected = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="deleted-rejected",
        outcome="rejected",
        deleted_at=datetime.now(timezone.utc),
    )
    open_disqualified = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="open-disqualified",
        workable_disqualified=True,
    )
    erased_person = _application(
        db,
        organization_id=organization.id,
        role_id=owner.id,
        suffix="erased-person",
        pipeline_stage="advanced",
    )
    erased_person.candidate.deleted_at = datetime.now(timezone.utc)
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related.id,
        application_id=rejected.id,
        status="pending",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related.id,
        application_id=hired.id,
        status="pending",
        application_outcome="rejected",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related.id,
        application_id=deleted_rejected.id,
        status="pending",
        pipeline_stage="review",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related.id,
        application_id=open_disqualified.id,
        status="pending",
        pipeline_stage="advanced",
    )
    _evaluation(
        db,
        organization_id=organization.id,
        role_id=related.id,
        application_id=erased_person.id,
        status="done",
        pipeline_stage="advanced",
    )
    db.commit()

    owner_counts = role_pipeline_counts(
        db, organization_id=organization.id, role_id=owner.id
    )
    counts = related_role_pipeline_counts_bulk(
        db,
        [related.id],
        organization_id=int(organization.id),
    )[related.id]

    assert owner_counts["rejected"] == 1
    assert counts["applied"] == 1
    assert counts["completed"] == 1
    assert counts["advanced"] == 1
    assert counts["rejected"] == 1
    assert sum(counts.values()) == 4

    metrics = load_role_candidate_metrics(
        db,
        roles=[owner, related],
        organization_id=int(organization.id),
        include_pipeline_stats=True,
    )
    # Ordinary membership is the live application, so the soft-deleted source
    # is absent there; the related role keeps that evidence row as long as the
    # person is live. The erased person is absent from both.
    assert metrics.application_counts[int(owner.id)] == 4
    assert metrics.active_counts[int(owner.id)] == 1
    assert metrics.application_counts[int(related.id)] == 4
    assert metrics.active_counts[int(related.id)] == 3
    assert metrics.stage_counts[int(owner.id)] == owner_counts
    assert metrics.stage_counts[int(related.id)] == counts


def test_role_list_and_detail_expose_the_same_explicit_local_counts(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="API count owner",
        source="workable",
        workable_job_id="RELATED-COUNT-API",
    )
    db.add(owner)
    db.flush()
    related = _related_role(
        db,
        organization_id=user.organization_id,
        owner_id=owner.id,
        suffix="API",
    )
    open_without_evaluation = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="api-open-without-evaluation",
    )
    open_disqualified = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="api-open-disqualified",
        workable_disqualified=True,
    )
    rejected = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="api-rejected-without-evaluation",
        outcome="rejected",
    )
    hired = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="api-hired-without-evaluation",
        outcome="hired",
    )
    _evaluation(
        db,
        organization_id=user.organization_id,
        role_id=related.id,
        application_id=open_disqualified.id,
        status="pending",
    )
    _evaluation(
        db,
        organization_id=user.organization_id,
        role_id=related.id,
        application_id=rejected.id,
        status="pending",
    )
    _evaluation(
        db,
        organization_id=user.organization_id,
        role_id=related.id,
        application_id=hired.id,
        status="pending",
        application_outcome="rejected",
    )
    db.commit()

    listing = client.get(
        "/api/v1/roles",
        params={"include_pipeline_stats": True, "limit": 200},
        headers=headers,
    )
    detail = client.get(f"/api/v1/roles/{related.id}", headers=headers)

    assert listing.status_code == 200, listing.text
    assert detail.status_code == 200, detail.text
    listed_role = next(
        role for role in listing.json() if role["id"] == related.id
    )
    detail_role = detail.json()
    listed_counts = listed_role["stage_counts"]
    detail_counts = detail_role["stage_counts"]
    assert listed_role["applications_count"] == 3
    assert listed_role["active_candidates_count"] == 2
    assert listed_role["last_candidate_activity_at"] is not None
    assert detail_role["applications_count"] == 3
    assert listed_counts == detail_counts
    assert listed_counts["applied"] == 2
    assert listed_counts["rejected"] == 1

    open_rows = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"application_outcome": "open"},
        headers=headers,
    )
    rejected_rows = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"application_outcome": "rejected"},
        headers=headers,
    )
    hired_rows = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"application_outcome": "hired"},
        headers=headers,
    )
    assert open_rows.status_code == 200, open_rows.text
    assert rejected_rows.status_code == 200, rejected_rows.text
    assert hired_rows.status_code == 200, hired_rows.text
    open_by_id = {row["id"]: row for row in open_rows.json()}
    assert set(open_by_id) == {open_disqualified.id, rejected.id}
    assert open_by_id[open_disqualified.id]["application_outcome"] == "open"
    assert open_by_id[open_disqualified.id]["related_role_availability"] == "disqualified"
    assert open_by_id[rejected.id]["application_outcome"] == "open"
    assert open_without_evaluation.id not in open_by_id
    assert [row["id"] for row in rejected_rows.json()] == [hired.id]
    assert rejected_rows.json()[0]["application_outcome"] == "rejected"
    assert rejected_rows.json()[0]["related_role_availability"] == "disqualified"
    assert hired_rows.json() == []


def test_related_pipeline_endpoint_uses_explicit_membership_and_local_stages(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="Pipeline endpoint owner",
        source="workable",
        workable_job_id="RELATED-PIPELINE-ENDPOINT",
    )
    db.add(owner)
    db.flush()
    related = _related_role(
        db,
        organization_id=user.organization_id,
        owner_id=owner.id,
        suffix="Pipeline endpoint",
    )
    missing_applied = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="pipeline-missing-applied",
    )
    local_apps = {}
    for stage in ("invited", "in_assessment", "review"):
        application = _application(
            db,
            organization_id=user.organization_id,
            role_id=owner.id,
            suffix=f"pipeline-local-{stage}",
        )
        local_apps[stage] = application
        _evaluation(
            db,
            organization_id=user.organization_id,
            role_id=related.id,
            application_id=application.id,
            status="done",
            pipeline_stage=stage,
        )
    source_advanced = _application(
        db,
        organization_id=user.organization_id,
        role_id=owner.id,
        suffix="pipeline-source-advanced",
        pipeline_stage="advanced",
    )
    _evaluation(
        db,
        organization_id=user.organization_id,
        role_id=related.id,
        application_id=source_advanced.id,
        status="done",
    )
    db.commit()

    pipeline = client.get(
        f"/api/v1/roles/{related.id}/pipeline",
        headers=headers,
    )
    assert pipeline.status_code == 200, pipeline.text
    payload = pipeline.json()
    assert payload["stage_counts"] == {
        "all": 4,
        "applied": 1,
        "invited": 1,
        "in_assessment": 1,
        "review": 1,
    }
    assert {row["id"]: row["pipeline_stage"] for row in payload["items"]} == {
        local_apps["invited"].id: "invited",
        local_apps["in_assessment"].id: "in_assessment",
        local_apps["review"].id: "review",
        source_advanced.id: "applied",
    }
    assert missing_applied.id not in {row["id"] for row in payload["items"]}

    filtered_pipeline = client.get(
        f"/api/v1/roles/{related.id}/pipeline",
        params={"stage": "applied"},
        headers=headers,
    )
    filtered_list = client.get(
        f"/api/v1/roles/{related.id}/applications",
        params={"pipeline_stage": "applied", "application_outcome": "open"},
        headers=headers,
    )
    assert filtered_pipeline.status_code == 200, filtered_pipeline.text
    assert filtered_list.status_code == 200, filtered_list.text
    assert [row["id"] for row in filtered_pipeline.json()["items"]] == [
        source_advanced.id
    ]
    assert [row["id"] for row in filtered_list.json()] == [source_advanced.id]
