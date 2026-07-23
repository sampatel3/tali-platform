"""Ground-truth contract for independent related-role candidate pools."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.actions import create_application
from app.actions.types import Actor
from app.candidate_search.role_scope import (
    RelatedRoleSearchApplication,
    build_top_candidate_role_scope,
    resolve_candidate_role_scope,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.agent_decision import AGENT_DECISION_ACTIVE_STATUSES, AgentDecision
from app.models.assessment import Assessment, AssessmentStatus
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.mcp import handlers
from app.mcp.payloads import application_summary
from app.domains.assessments_runtime.applications_routes import (
    get_application_detail,
)
from app.services.related_role_service import (
    RelatedRoleError,
    create_related_role,
    preview_related_role,
)
from app.services.related_role_direct_membership import (
    create_direct_related_membership,
)
from app.services.sister_role_projection import project_sister_application
from app.services.sister_role_service import (
    ensure_application_sister_evaluations,
    ensure_sister_evaluations,
)


def _roles(db, *, suffix: str) -> tuple[Organization, Role, Role]:
    organization = Organization(
        name=f"Membership contract {suffix}",
        slug=f"membership-contract-{suffix}-{id(db)}",
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name=f"Owner {suffix}",
        source="workable",
        workable_job_id=f"MEMBERSHIP-{suffix}",
        workable_job_data={"state": "published"},
        job_spec_text="Owner role with a complete production engineering specification.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(organization.id),
        name=f"Related {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(owner.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text=(
            "Independent related role requiring production engineering, "
            "reliable delivery, and measurable operating outcomes."
        ),
    )
    db.add(related)
    db.flush()
    return organization, owner, related


def _owner_application(
    db,
    *,
    organization: Organization,
    owner: Role,
    suffix: str,
    pipeline_stage: str = "applied",
    application_outcome: str = "open",
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=int(organization.id),
        email=f"membership-{suffix}-{id(db)}@example.com",
        full_name=f"Membership {suffix}",
        cv_text="Python, distributed systems, and production ML delivery.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(candidate.id),
        role_id=int(owner.id),
        source="workable",
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="sync",
        application_outcome=application_outcome,
        cv_text=candidate.cv_text,
        workable_candidate_id=f"workable-{suffix}-{id(db)}",
    )
    db.add(application)
    db.flush()
    return application


def test_related_scope_contains_only_explicit_live_memberships(db):
    organization, owner, related = _roles(db, suffix="scope")
    included = _owner_application(
        db, organization=organization, owner=owner, suffix="included"
    )
    _owner_application(
        db, organization=organization, owner=owner, suffix="owner-only"
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related.id),
            candidate_id=int(included.candidate_id),
            source_application_id=int(included.id),
            ats_application_id=int(included.id),
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint="scope-membership",
            role_fit_score=82,
        )
    )
    db.commit()
    included.deleted_at = datetime.now(timezone.utc)
    db.commit()

    scope = resolve_candidate_role_scope(
        db,
        organization_id=int(organization.id),
        role_id=int(related.id),
    )
    rows = scope.scope_roster(
        db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == int(organization.id),
        )
    ).all()
    top_scope = build_top_candidate_role_scope(
        db,
        scope=scope,
        rank_by="taali",
        score_field="taali_score_cache_100",
    )

    assert [row.id for row in rows] == [included.id]
    assert [row.id for row in top_scope.base_query.all()] == [included.id]
    assert scope.roster_size(db) == 1


def test_seed_is_one_time_and_refresh_never_adds_or_deletes_membership(db):
    organization, owner, related = _roles(db, suffix="seed")
    first = _owner_application(
        db, organization=organization, owner=owner, suffix="first"
    )
    second = _owner_application(
        db, organization=organization, owner=owner, suffix="second"
    )

    seeded = ensure_sister_evaluations(db, related, seed_missing=True)
    db.commit()
    assert seeded["total"] == 2

    later = _owner_application(
        db, organization=organization, owner=owner, suffix="later"
    )
    assert ensure_application_sister_evaluations(
        db, later, sister_roles=[related]
    ) == []
    refreshed = ensure_sister_evaluations(db, related, reset_existing=True)
    db.commit()

    memberships = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .order_by(SisterRoleEvaluation.source_application_id.asc())
        .all()
    )
    assert refreshed["total"] == 2
    assert {row.source_application_id for row in memberships} == {
        first.id,
        second.id,
    }

    first.deleted_at = datetime.now(timezone.utc)
    ensure_sister_evaluations(db, related, reset_existing=True)
    db.commit()
    assert (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .count()
        == 2
    )


def test_explicit_seed_restores_legacy_rolling_compatibility_shadow(db):
    organization, owner, related = _roles(db, suffix="rolling-shadow")
    application = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="rolling-shadow",
    )
    shadow = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        ats_application_id=None,
        status="excluded",
        pipeline_stage="applied",
        application_outcome="open",
        membership_source="legacy_compat_shadow",
        spec_fingerprint="legacy-shadow",
        cv_fingerprint="legacy-shadow",
        last_error_code="legacy_inferred_membership_ignored",
        error_message="Ignored legacy inferred membership during rolling migration",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(shadow)
    db.commit()
    shadow_id = int(shadow.id)

    counts = ensure_sister_evaluations(db, related, seed_missing=True)
    db.commit()

    restored = db.get(SisterRoleEvaluation, shadow_id)
    assert counts["total"] == 1
    assert restored is not None
    assert restored.deleted_at is None
    assert restored.membership_source == "initial_snapshot"
    assert restored.status == "pending"
    assert restored.last_error_code is None
    assert restored.source_application_id == int(application.id)
    assert restored.ats_application_id == int(application.id)
    assert (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.candidate_id == int(application.candidate_id),
        )
        .count()
        == 1
    )


def test_related_from_related_snapshots_only_its_logical_roster_and_local_state(db):
    """The source role, not its ATS owner's broader pool, is the oracle."""

    organization, owner, source = _roles(db, suffix="nested-source")
    first = _owner_application(
        db, organization=organization, owner=owner, suffix="nested-first"
    )
    second = _owner_application(
        db, organization=organization, owner=owner, suffix="nested-second"
    )
    owner_only = _owner_application(
        db, organization=organization, owner=owner, suffix="nested-owner-only"
    )
    first_membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(source.id),
        candidate_id=int(first.candidate_id),
        source_application_id=int(first.id),
        ats_application_id=int(first.id),
        status="done",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        application_outcome_source="recruiter",
        membership_source="initial_snapshot",
        spec_fingerprint="nested-first",
        role_fit_score=88,
    )
    second_membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(source.id),
        candidate_id=int(second.candidate_id),
        source_application_id=int(second.id),
        ats_application_id=int(second.id),
        status="done",
        pipeline_stage="advanced",
        pipeline_stage_source="recruiter",
        application_outcome="rejected",
        application_outcome_source="recruiter",
        membership_source="initial_snapshot",
        spec_fingerprint="nested-second",
        role_fit_score=72,
    )
    direct_candidate = Candidate(
        organization_id=int(organization.id),
        email=f"nested-direct-{id(db)}@example.com",
        full_name="Nested direct member",
        cv_text="Direct related-role evidence for production Python systems.",
    )
    db.add(direct_candidate)
    db.flush()
    direct_application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(direct_candidate.id),
        role_id=int(source.id),
        source="manual",
        pipeline_stage="invited",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        cv_text=direct_candidate.cv_text,
    )
    db.add_all([first_membership, second_membership, direct_application])
    db.commit()

    # Evidence-row soft deletion must not erase the source's explicit member.
    second.deleted_at = datetime.now(timezone.utc)
    db.commit()
    preview = preview_related_role(
        db,
        role_id=int(source.id),
        organization_id=int(organization.id),
    )
    assert preview["candidates_total"] == 3
    assert preview["candidates_with_cv"] == 3

    user = User(
        organization_id=int(organization.id),
        email=f"nested-creator-{id(db)}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    complete_spec = (
        "A complete nested related-role specification requiring production Python, "
        "distributed systems, operational reliability, and measurable delivery."
    )
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        target, counts = create_related_role(
            db,
            role_id=int(source.id),
            organization_id=int(organization.id),
            creator_user_id=int(user.id),
            name="Nested related target",
            job_spec_text=complete_spec,
        )
    dispatch.assert_called_once()
    assert counts["total"] == 3
    assert target.related_source_role_id == source.id
    assert target.ats_owner_role_id == owner.id

    rows = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(target.id),
            SisterRoleEvaluation.deleted_at.is_(None),
        )
        .all()
    )
    by_candidate = {int(row.candidate_id): row for row in rows}
    assert set(by_candidate) == {
        int(first.candidate_id),
        int(second.candidate_id),
        int(direct_candidate.id),
    }
    assert int(owner_only.candidate_id) not in by_candidate
    assert by_candidate[int(first.candidate_id)].pipeline_stage == "review"
    assert by_candidate[int(first.candidate_id)].application_outcome == "open"
    assert by_candidate[int(second.candidate_id)].source_application_id == second.id
    assert by_candidate[int(second.candidate_id)].pipeline_stage == "advanced"
    assert by_candidate[int(second.candidate_id)].application_outcome == "rejected"
    assert by_candidate[int(second.candidate_id)].status == "excluded"
    assert (
        by_candidate[int(direct_candidate.id)].source_application_id
        == direct_application.id
    )
    assert by_candidate[int(direct_candidate.id)].pipeline_stage == "invited"


def test_native_source_and_ownerless_related_source_form_one_time_snapshots(db):
    organization = Organization(
        name="Native related source",
        slug=f"native-related-source-{id(db)}",
    )
    db.add(organization)
    db.flush()
    native = Role(
        organization_id=int(organization.id),
        name="Native AI role",
        source="manual",
        job_spec_text="Native role specification without any ATS transport.",
    )
    creator = User(
        organization_id=int(organization.id),
        email=f"native-related-creator-{id(db)}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add_all([native, creator])
    db.flush()
    source_application = _owner_application(
        db,
        organization=organization,
        owner=native,
        suffix="native-source",
    )
    db.commit()

    preview = preview_related_role(
        db,
        role_id=int(native.id),
        organization_id=int(organization.id),
    )
    assert preview["source_ats_provider"] is None
    assert preview["candidates_total"] == 1

    spec = (
        "A complete ownerless related role specification covering production AI, "
        "Python services, operational reliability, and measurable delivery."
    )
    source_application.cv_text = (
        f"{source_application.cv_text} Newly added grounded evidence."
    )
    db.commit()
    with pytest.raises(RelatedRoleError, match="snapshot changed"):
        create_related_role(
            db,
            role_id=int(native.id),
            organization_id=int(organization.id),
            creator_user_id=int(creator.id),
            name="Stale ownerless related role",
            job_spec_text=spec,
            expected_source_snapshot_fingerprint=preview[
                "source_snapshot_fingerprint"
            ],
        )
    refreshed_preview = preview_related_role(
        db,
        role_id=int(native.id),
        organization_id=int(organization.id),
    )
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        first, first_counts = create_related_role(
            db,
            role_id=int(native.id),
            organization_id=int(organization.id),
            creator_user_id=int(creator.id),
            name="First ownerless related role",
            job_spec_text=spec,
            expected_source_snapshot_fingerprint=refreshed_preview[
                "source_snapshot_fingerprint"
            ],
        )
        first_membership = (
            db.query(SisterRoleEvaluation)
            .filter(SisterRoleEvaluation.role_id == int(first.id))
            .one()
        )
        first_membership.pipeline_stage = "review"
        first_membership.pipeline_stage_source = "recruiter"
        db.commit()
        nested_preview = preview_related_role(
            db,
            role_id=int(first.id),
            organization_id=int(organization.id),
        )
        second, second_counts = create_related_role(
            db,
            role_id=int(first.id),
            organization_id=int(organization.id),
            creator_user_id=int(creator.id),
            name="Second ownerless related role",
            job_spec_text=spec,
            expected_source_snapshot_fingerprint=nested_preview[
                "source_snapshot_fingerprint"
            ],
        )

    assert dispatch.call_count == 2
    assert first_counts["total"] == second_counts["total"] == 1
    assert first.related_source_role_id == native.id
    assert first.ats_owner_role_id is None
    assert second.related_source_role_id == first.id
    assert second.ats_owner_role_id is None
    second_membership = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(second.id))
        .one()
    )
    assert second_membership.candidate_id == source_application.candidate_id
    assert second_membership.pipeline_stage == "review"
    assert second_membership.ats_application_id is None


def test_nested_related_search_survives_soft_deleted_or_absent_ats_owner(db):
    organization, owner, related = _roles(db, suffix="optional-owner")
    evidence = _owner_application(
        db, organization=organization, owner=owner, suffix="optional-owner"
    )
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(evidence.candidate_id),
        source_application_id=int(evidence.id),
        ats_application_id=int(evidence.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="optional-owner",
        role_fit_score=89,
    )
    db.add(membership)
    db.commit()
    owner.deleted_at = datetime.now(timezone.utc)
    evidence.deleted_at = datetime.now(timezone.utc)
    db.commit()

    other_organization = Organization(
        name="Untrusted owner organization",
        slug=f"untrusted-owner-{id(db)}",
    )
    db.add(other_organization)
    db.flush()
    cross_org_owner = Role(
        organization_id=int(other_organization.id),
        name="Cross-organization owner",
        source="workable",
        workable_job_id=f"CROSS-ORG-{id(db)}",
    )
    db.add(cross_org_owner)
    reader = User(
        organization_id=int(organization.id),
        email=f"optional-owner-reader-{id(db)}@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(reader)
    db.commit()

    for owner_id in (int(owner.id), None, int(cross_org_owner.id)):
        related.ats_owner_role_id = owner_id
        db.commit()
        scope = resolve_candidate_role_scope(
            db,
            organization_id=int(organization.id),
            role_id=int(related.id),
        )
        assert scope.application_role is None
        visible = scope.scope_visible_roster(
            db.query(CandidateApplication).filter(
                CandidateApplication.organization_id == int(organization.id)
            )
        ).all()
        assert [int(row.id) for row in visible] == [int(evidence.id)]
        assert scope.roster_size(db) == 1
        top_scope = build_top_candidate_role_scope(
            db,
            scope=scope,
            rank_by="taali",
            score_field="taali_score_cache_100",
        )
        assert [int(row.id) for row in top_scope.base_query.all()] == [
            int(evidence.id)
        ]
        detail = handlers.get_role_candidate(
            db,
            reader,
            role_id=int(related.id),
            application_id=int(evidence.id),
            include_cv_text=True,
        )
        assert detail["role_id"] == int(related.id)
        assert detail["pipeline_stage"] == "review"
        assert detail["taali_score"] == 89
        api_detail = get_application_detail(
            int(evidence.id),
            include_cv_text=True,
            view_role_id=int(related.id),
            db=db,
            current_user=reader,
        ).model_dump(mode="python")
        assert api_detail["role_id"] == int(related.id)
        assert api_detail["pipeline_stage"] == "review"
        assert api_detail["taali_score"] == 89
        assert api_detail["operational_role_id"] is None


def test_nested_related_snapshot_never_fans_out_later_source_members(db):
    organization, owner, source = _roles(db, suffix="nested-no-fanout")
    first = _owner_application(
        db, organization=organization, owner=owner, suffix="nested-existing"
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(source.id),
            candidate_id=int(first.candidate_id),
            source_application_id=int(first.id),
            ats_application_id=int(first.id),
            status="done",
            pipeline_stage="applied",
            application_outcome="open",
            membership_source="initial_snapshot",
            spec_fingerprint="nested-existing",
            role_fit_score=81,
        )
    )
    target = Role(
        organization_id=int(organization.id),
        name="Nested no-fanout target",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        related_source_role_id=int(source.id),
        ats_owner_role_id=int(owner.id),
        job_spec_text=(
            "A complete target role specification for reliable distributed "
            "systems and measurable production delivery."
        ),
    )
    db.add(target)
    db.flush()
    seeded = ensure_sister_evaluations(
        db,
        target,
        seed_missing=True,
        source_role=source,
    )
    db.commit()
    assert seeded["total"] == 1

    later = _owner_application(
        db, organization=organization, owner=owner, suffix="nested-later"
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(source.id),
            candidate_id=int(later.candidate_id),
            source_application_id=int(later.id),
            ats_application_id=int(later.id),
            status="pending",
            pipeline_stage="applied",
            application_outcome="open",
            membership_source="direct",
            spec_fingerprint="nested-later",
        )
    )
    db.commit()
    assert ensure_application_sister_evaluations(
        db, later, sister_roles=[target]
    ) == []
    assert (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(target.id))
        .count()
        == 1
    )


def test_projection_keeps_local_state_and_returns_shared_ats_restrictions(db):
    organization, owner, related = _roles(db, suffix="projection")
    source = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="restricted",
        pipeline_stage="advanced",
        application_outcome="rejected",
    )
    source.workable_stage = "Technical Interview"
    source.workable_disqualified = True
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(source.candidate_id),
        source_application_id=int(source.id),
        ats_application_id=int(source.id),
        status="done",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        application_outcome_source="recruiter",
        membership_source="initial_snapshot",
        spec_fingerprint="projection-membership",
        role_fit_score=91,
    )
    db.add(membership)
    db.commit()

    adapted = RelatedRoleSearchApplication(
        source,
        role=related,
        evaluation=membership,
        assessment_score=None,
    )
    projected = project_sister_application(
        {
            "id": int(source.id),
            "application_outcome": source.application_outcome,
            "pipeline_stage": source.pipeline_stage,
            "workable_disqualified": source.workable_disqualified,
        },
        sister_role=related,
        owner_role=owner,
        evaluation=membership,
        application=source,
    )

    assert adapted.pipeline_stage == projected["pipeline_stage"] == "review"
    assert adapted.application_outcome == projected["application_outcome"] == "open"
    assert projected["ats_context"]["application_outcome"] == "rejected"
    assert projected["action_restrictions"]["restricted"] is True
    assert projected["action_restrictions"]["can_advance_locally"] is True
    assert projected["action_restrictions"]["can_reject_locally"] is True
    assert projected["action_restrictions"]["can_advance_in_ats"] is False
    assert projected["action_restrictions"]["can_reject_in_ats"] is False
    assert {
        "shared_ats_outcome_rejected",
        "shared_ats_disqualified",
        "shared_ats_post_handover",
    }.issubset(projected["action_restrictions"]["codes"])


def test_ats_transport_identity_is_enforced_on_orm_writes_and_owner_changes(db):
    organization, owner, related = _roles(db, suffix="transport-write-boundary")
    valid = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="transport-valid",
    )
    wrong_candidate = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="transport-wrong-candidate",
    )
    wrong_owner = Role(
        organization_id=int(organization.id),
        name="Wrong ATS owner",
        source="workable",
        workable_job_id=f"WRONG-OWNER-{id(db)}",
    )
    db.add(wrong_owner)
    db.flush()
    wrong_owner_application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(valid.candidate_id),
        role_id=int(wrong_owner.id),
        pipeline_stage="applied",
        pipeline_stage_source="sync",
        application_outcome="open",
    )
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(valid.candidate_id),
        source_application_id=int(valid.id),
        ats_application_id=int(valid.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="transport-write-boundary",
        role_fit_score=87,
    )
    db.add_all([wrong_owner_application, membership])
    db.commit()

    db.add(
        SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related.id),
            candidate_id=int(wrong_candidate.candidate_id),
            source_application_id=int(wrong_candidate.id),
            ats_application_id=int(valid.id),
            status="pending",
            pipeline_stage="applied",
            application_outcome="open",
            membership_source="direct",
            spec_fingerprint="invalid-transport-insert",
        )
    )
    with pytest.raises(ValueError, match="declared ATS owner"):
        db.flush()
    db.rollback()

    for invalid_application_id in (
        int(wrong_candidate.id),
        int(wrong_owner_application.id),
    ):
        membership = db.get(SisterRoleEvaluation, int(membership.id))
        membership.ats_application_id = invalid_application_id
        with pytest.raises(ValueError, match="declared ATS owner"):
            db.flush()
        db.rollback()

    membership = db.get(SisterRoleEvaluation, int(membership.id))
    assert membership.ats_application_id == valid.id
    related.ats_owner_role_id = int(wrong_owner.id)
    db.commit()
    db.refresh(membership)
    assert membership.ats_application_id is None
    assert membership.ats_application is None


def test_related_payloads_use_only_validated_ats_transport_fields(db):
    organization, owner, related = _roles(db, suffix="transport-read-boundary")
    transport = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="transport-read-valid",
    )
    transport.workable_stage = "ATS technical interview"
    transport.bullhorn_status = "ATS submitted"
    transport.external_stage_raw = "ats-raw"
    transport.external_stage_normalized = "ats-normalized"
    direct_source = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(transport.candidate_id),
        role_id=int(related.id),
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="open",
        workable_stage="WRONG source stage",
        bullhorn_status="WRONG source status",
        external_stage_raw="wrong-source-raw",
        external_stage_normalized="wrong-source-normalized",
        cv_text="Role-local evidence",
    )
    db.add(direct_source)
    db.flush()
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(transport.candidate_id),
        source_application_id=int(direct_source.id),
        ats_application_id=int(transport.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="direct",
        spec_fingerprint="transport-read-boundary",
        role_fit_score=93,
    )
    db.add(membership)
    db.commit()

    adapted = RelatedRoleSearchApplication(
        direct_source,
        role=related,
        evaluation=membership,
        assessment_score=None,
    )
    summary = application_summary(adapted)
    projected = project_sister_application(
        {
            "workable_stage": direct_source.workable_stage,
            "bullhorn_status": direct_source.bullhorn_status,
            "external_stage_raw": direct_source.external_stage_raw,
            "external_stage_normalized": direct_source.external_stage_normalized,
        },
        sister_role=related,
        owner_role=owner,
        evaluation=membership,
        application=direct_source,
    )
    for payload in (summary, projected):
        assert payload["workable_stage"] == "ATS technical interview"
        assert payload["bullhorn_status"] == "ATS submitted"
        assert payload["external_stage_raw"] == "ats-raw"
        assert payload["external_stage_normalized"] == "ats-normalized"

    # Simulate a corrupt legacy/direct-SQL row. Production migration triggers
    # reject this write; canonical reads still fail closed if it is encountered
    # in a metadata-created test schema or during a partial recovery.
    db.execute(
        text(
            "UPDATE sister_role_evaluations "
            "SET ats_application_id = :application_id WHERE id = :membership_id"
        ),
        {
            "application_id": int(_owner_application(
                db,
                organization=organization,
                owner=owner,
                suffix="transport-read-wrong-candidate",
            ).id),
            "membership_id": int(membership.id),
        },
    )
    db.commit()
    db.expire_all()
    membership = db.get(SisterRoleEvaluation, int(membership.id))
    direct_source = db.get(CandidateApplication, int(direct_source.id))
    related = db.get(Role, int(related.id))
    adapted = RelatedRoleSearchApplication(
        direct_source,
        role=related,
        evaluation=membership,
        assessment_score=None,
    )
    summary = application_summary(adapted)
    projected = project_sister_application(
        {
            "workable_stage": direct_source.workable_stage,
            "bullhorn_status": direct_source.bullhorn_status,
            "external_stage_raw": direct_source.external_stage_raw,
            "external_stage_normalized": direct_source.external_stage_normalized,
        },
        sister_role=related,
        owner_role=owner,
        evaluation=membership,
        application=direct_source,
    )
    for payload in (summary, projected):
        assert payload["workable_stage"] is None
        assert payload["bullhorn_status"] is None
        assert payload["external_stage_raw"] is None
        assert payload["external_stage_normalized"] is None


def test_direct_related_application_creates_explicit_membership(db):
    organization, _owner, related = _roles(db, suffix="direct")

    result = create_application.run(
        db,
        Actor.system(),
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_email=f"direct-related-{id(db)}@example.com",
        candidate_name="Direct Related Candidate",
    )
    db.commit()

    membership = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == int(related.id))
        .one()
    )
    assert membership.candidate_id == result.candidate_id
    assert membership.source_application_id == result.application_id
    assert membership.ats_application_id is None
    assert membership.membership_source == "direct"
    assert membership.pipeline_stage == "applied"
    assert membership.application_outcome == "open"

    application = db.get(CandidateApplication, int(result.application_id))
    application.cv_text = "Direct evidence now includes Python and production ML."
    application.candidate.cv_text = application.cv_text
    queued = ensure_application_sister_evaluations(
        db,
        application,
        queue_for_rescore=True,
    )
    db.commit()

    assert queued == [int(membership.id)]
    db.refresh(membership)
    assert membership.status == "pending"
    assert membership.cv_fingerprint is not None


def test_direct_application_promotes_role_truth_and_archives_owner_shadow(db):
    organization, owner, related = _roles(db, suffix="direct-promotes")
    owner_application = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="direct-promotes",
    )
    owner_membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(owner_application.candidate_id),
        source_application_id=int(owner_application.id),
        ats_application_id=int(owner_application.id),
        status="done",
        pipeline_stage="applied",
        application_outcome="open",
        membership_source="legacy_explicit",
        spec_fingerprint="owner-shadow-spec",
        role_fit_score=88,
    )
    db.add(owner_membership)
    db.commit()

    now = datetime.now(timezone.utc)
    direct_application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(owner_application.candidate_id),
        role_id=int(related.id),
        pipeline_stage="applied",
        pipeline_stage_updated_at=now,
        pipeline_stage_source="system",
        application_outcome="open",
        application_outcome_updated_at=now,
        cv_text="Direct role evidence for production engineering.",
    )
    db.add(direct_application)
    db.flush()
    create_direct_related_membership(
        db,
        role=related,
        application=direct_application,
    )
    db.commit()

    rows = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == int(related.id),
            SisterRoleEvaluation.candidate_id == int(owner_application.candidate_id),
        )
        .order_by(SisterRoleEvaluation.id.asc())
        .all()
    )
    assert len(rows) == 2
    live = [row for row in rows if row.deleted_at is None]
    shadows = [row for row in rows if row.deleted_at is not None]
    assert len(live) == 1
    assert len(shadows) == 1
    assert live[0].source_application_id == direct_application.id
    assert live[0].ats_application_id == owner_application.id
    assert live[0].membership_source == "direct"
    assert shadows[0].source_application_id == owner_application.id
    assert shadows[0].membership_source == "legacy_compat_shadow"


def test_direct_application_promotion_closes_prior_active_lifecycle(db):
    organization, owner, related = _roles(db, suffix="direct-promotion-lifecycle")
    owner_application = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="direct-promotion-lifecycle",
    )
    prior_membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(owner_application.candidate_id),
        source_application_id=int(owner_application.id),
        ats_application_id=int(owner_application.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="prior-promotion-spec",
        role_fit_score=91,
        summary="Prior owner-backed role lifecycle",
    )
    prior_assessment = Assessment(
        organization_id=int(organization.id),
        candidate_id=int(owner_application.candidate_id),
        role_id=int(related.id),
        application_id=int(owner_application.id),
        token=f"direct-promotion-prior-assessment-{id(db)}",
        status=AssessmentStatus.COMPLETED,
        is_voided=False,
    )
    prior_decision = AgentDecision(
        organization_id=int(organization.id),
        role_id=int(related.id),
        application_id=int(owner_application.id),
        decision_type="advance_to_interview",
        recommendation="advance",
        status="processing",
        reasoning="Prior owner-backed lifecycle recommendation",
        model_version="prior-model",
        prompt_version="prior-prompt",
        idempotency_key=f"direct-promotion-prior-decision-{id(db)}",
    )
    db.add_all([prior_membership, prior_assessment, prior_decision])
    db.commit()

    now = datetime.now(timezone.utc)
    direct_application = CandidateApplication(
        organization_id=int(organization.id),
        candidate_id=int(owner_application.candidate_id),
        role_id=int(related.id),
        pipeline_stage="applied",
        pipeline_stage_updated_at=now,
        pipeline_stage_source="system",
        application_outcome="open",
        application_outcome_updated_at=now,
        cv_text="Fresh direct-role evidence for the new lifecycle.",
    )
    db.add(direct_application)
    db.flush()
    current_membership = create_direct_related_membership(
        db,
        role=related,
        application=direct_application,
    )
    db.commit()

    db.refresh(prior_membership)
    db.refresh(prior_assessment)
    db.refresh(prior_decision)
    assert prior_membership.deleted_at is not None
    assert prior_membership.membership_source == "legacy_compat_shadow"
    assert prior_membership.history[-1]["role_fit_score"] == 91
    assert current_membership.deleted_at is None
    assert current_membership.source_application_id == direct_application.id
    assert prior_assessment.is_voided is True
    assert prior_assessment.void_reason == (
        "Superseded when the candidate re-applied to this role"
    )
    assert prior_decision.status == "discarded"
    assert prior_decision.resolved_at is not None
    assert prior_decision.resolution_note == (
        "superseded: candidate started a new role membership lifecycle"
    )

    current_assessment = Assessment(
        organization_id=int(organization.id),
        candidate_id=int(owner_application.candidate_id),
        role_id=int(related.id),
        application_id=int(direct_application.id),
        token=f"direct-promotion-current-assessment-{id(db)}",
        status=AssessmentStatus.PENDING,
        is_voided=False,
    )
    current_decision = AgentDecision(
        organization_id=int(organization.id),
        role_id=int(related.id),
        application_id=int(direct_application.id),
        decision_type="send_assessment",
        recommendation="send_assessment",
        status="pending",
        reasoning="New direct-role lifecycle recommendation",
        model_version="current-model",
        prompt_version="current-prompt",
        idempotency_key=f"direct-promotion-current-decision-{id(db)}",
    )
    db.add_all([current_assessment, current_decision])
    db.commit()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                AgentDecision(
                    organization_id=int(organization.id),
                    role_id=int(related.id),
                    application_id=int(owner_application.id),
                    decision_type="reject",
                    recommendation="reject",
                    status="pending",
                    reasoning="Duplicate related-role card through ATS transport",
                    model_version="current-model",
                    prompt_version="current-prompt",
                    idempotency_key=(
                        f"direct-promotion-duplicate-decision-{id(db)}"
                    ),
                )
            )
            db.flush()

    owner_role_decision = AgentDecision(
        organization_id=int(organization.id),
        role_id=int(owner.id),
        application_id=int(owner_application.id),
        decision_type="reject",
        recommendation="reject",
        status="pending",
        reasoning="Independent owner-role recommendation",
        model_version="current-model",
        prompt_version="current-prompt",
        idempotency_key=f"direct-promotion-owner-decision-{id(db)}",
    )
    db.add(owner_role_decision)
    db.commit()

    active_assessments = (
        db.query(Assessment)
        .filter(
            Assessment.organization_id == int(organization.id),
            Assessment.role_id == int(related.id),
            Assessment.candidate_id == int(owner_application.candidate_id),
            Assessment.is_voided.is_(False),
        )
        .all()
    )
    active_decisions = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.organization_id == int(organization.id),
            AgentDecision.role_id == int(related.id),
            AgentDecision.application_id.in_(
                [int(owner_application.id), int(direct_application.id)]
            ),
            AgentDecision.status.in_(AGENT_DECISION_ACTIVE_STATUSES),
        )
        .all()
    )
    assert [int(row.id) for row in active_assessments] == [
        int(current_assessment.id)
    ]
    assert [int(row.id) for row in active_decisions] == [int(current_decision.id)]
    assert owner_role_decision.candidate_id == owner_application.candidate_id


def test_direct_reapply_restores_membership_as_a_fresh_role_lifecycle(db):
    organization, owner, related = _roles(db, suffix="direct-reapply")
    owner_application = _owner_application(
        db,
        organization=organization,
        owner=owner,
        suffix="direct-reapply",
    )
    membership = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_id=int(owner_application.candidate_id),
        source_application_id=int(owner_application.id),
        ats_application_id=int(owner_application.id),
        status="done",
        pipeline_stage="advanced",
        pipeline_stage_source="recruiter",
        application_outcome="rejected",
        application_outcome_source="recruiter",
        version=7,
        membership_source="initial_snapshot",
        spec_fingerprint="prior-role-spec",
        cv_fingerprint="prior-cv",
        role_fit_score=94,
        summary="Resolved result from the prior membership lifecycle",
        details={"cycle": "prior"},
        model_version="prior-model",
        prompt_version="prior-prompt",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(membership)
    db.flush()
    prior_assessment = Assessment(
        organization_id=int(organization.id),
        candidate_id=int(owner_application.candidate_id),
        role_id=int(related.id),
        application_id=int(owner_application.id),
        token=f"direct-reapply-assessment-{id(db)}",
        status=AssessmentStatus.COMPLETED,
        is_voided=False,
    )
    prior_decision = AgentDecision(
        organization_id=int(organization.id),
        role_id=int(related.id),
        application_id=int(owner_application.id),
        decision_type="advance_to_interview",
        recommendation="advance",
        status="processing",
        reasoning="Prior membership recommendation",
        model_version="prior-model",
        prompt_version="prior-prompt",
        idempotency_key=f"direct-reapply-decision-{id(db)}",
    )
    db.add_all([prior_assessment, prior_decision])
    db.commit()
    membership_id = int(membership.id)

    result = create_application.run(
        db,
        Actor.system(),
        organization_id=int(organization.id),
        role_id=int(related.id),
        candidate_email=str(owner_application.candidate.email),
        candidate_name=str(owner_application.candidate.full_name),
    )
    db.commit()

    restored = db.get(SisterRoleEvaluation, membership_id)
    assert restored.deleted_at is None
    assert restored.candidate_id == owner_application.candidate_id
    assert restored.source_application_id == result.application_id
    assert restored.ats_application_id == owner_application.id
    assert restored.membership_source == "direct"
    assert restored.pipeline_stage == "applied"
    assert restored.application_outcome == "open"
    assert restored.version == 8
    assert restored.status == "pending"
    assert restored.role_fit_score is None
    assert restored.summary is None
    assert restored.details is None
    assert restored.model_version is None
    assert restored.prompt_version is None
    assert restored.history[-1]["role_fit_score"] == 94

    db.refresh(prior_assessment)
    db.refresh(prior_decision)
    assert prior_assessment.is_voided is True
    assert prior_assessment.void_reason == (
        "Superseded when the candidate re-applied to this role"
    )
    assert prior_decision.status == "discarded"
    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == result.application_id,
            CandidateApplicationEvent.role_id == int(related.id),
            CandidateApplicationEvent.event_type
            == "related_role_membership_restored",
        )
        .one()
    )
    assert event.from_stage == "advanced"
    assert event.to_stage == "applied"
    assert event.from_outcome == "rejected"
    assert event.to_outcome == "open"
    assert event.event_metadata["previous_source_application_id"] == (
        owner_application.id
    )
