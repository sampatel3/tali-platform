"""Ground-truth tests for logical-role selected and batch operations."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime import applications_routes
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User


def _application(db, *, org, role, candidate, source="manual", cv_text="CV"):
    application = CandidateApplication(
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source=source,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        cv_text=cv_text,
    )
    db.add(application)
    db.flush()
    return application


def _evaluation(db, *, role, candidate, source, deleted=False):
    evaluation = SisterRoleEvaluation(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source_application_id=int(source.id),
        ats_application_id=int(source.id),
        status="error",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint=f"spec-{role.id}",
        cv_fingerprint=f"cv-{candidate.id}",
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


@pytest.fixture
def logical_role_world(db):
    org = Organization(
        name="Logical role batch org",
        slug="logical-role-batch-org",
        workable_connected=True,
        workable_access_token="test",
        workable_subdomain="logical-role-batch",
    )
    db.add(org)
    db.flush()
    user = User(
        email="owner@logical-role-batch.test",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        organization_id=int(org.id),
        role="owner",
    )
    owner = Role(
        organization_id=int(org.id),
        name="ATS owner role",
        source="manual",
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Owner-only backend role.",
    )
    outsider = Role(
        organization_id=int(org.id),
        name="Outsider role",
        source="manual",
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Unrelated finance role.",
    )
    db.add_all([user, owner, outsider])
    db.flush()
    related = Role(
        organization_id=int(org.id),
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        related_source_role_id=int(owner.id),
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Independent AI engineering role.",
    )
    db.add(related)
    db.flush()

    member = Candidate(
        organization_id=int(org.id),
        email="member@logical-role-batch.test",
        full_name="Related Member",
        cv_text="Production AI engineering",
    )
    removed = Candidate(
        organization_id=int(org.id),
        email="removed@logical-role-batch.test",
        full_name="Removed Member",
        cv_text="Former member",
    )
    foreign = Candidate(
        organization_id=int(org.id),
        email="foreign@logical-role-batch.test",
        full_name="Foreign Candidate",
        cv_text="Finance systems",
    )
    db.add_all([member, removed, foreign])
    db.flush()
    member_app = _application(db, org=org, role=owner, candidate=member)
    removed_app = _application(db, org=org, role=owner, candidate=removed)
    foreign_app = _application(db, org=org, role=outsider, candidate=foreign)
    evaluation = _evaluation(
        db,
        role=related,
        candidate=member,
        source=member_app,
    )
    _evaluation(
        db,
        role=related,
        candidate=removed,
        source=removed_app,
        deleted=True,
    )
    db.commit()
    return SimpleNamespace(
        org=org,
        user=user,
        owner=owner,
        outsider=outsider,
        related=related,
        member_app=member_app,
        removed_app=removed_app,
        foreign_app=foreign_app,
        evaluation=evaluation,
    )


def _rescreen_result(evaluation_id: int):
    result = SimpleNamespace(
        queued_count=1,
        waiting_count=0,
        unscorable_count=0,
        reset_count=1,
        evaluation_ids=(evaluation_id,),
    )
    result.as_dict = lambda: {"evaluation_ids": [evaluation_id], "reset_count": 1}
    return result


def test_related_selected_score_uses_role_local_evaluation(
    db,
    logical_role_world,
):
    world = logical_role_world
    with (
        patch(
            "app.services.related_role_rescreen_service."
            "rescreen_related_role_candidates",
            return_value=_rescreen_result(world.evaluation.id),
        ) as rescreen,
        patch(
            "app.domains.assessments_runtime.applications_routes.enqueue_score"
        ) as owner_score,
    ):
        result = applications_routes.score_selected_applications(
            role_id=int(world.related.id),
            payload={"application_ids": [int(world.member_app.id)]},
            db=db,
            current_user=world.user,
        )

    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["enqueued"] == 1
    owner_score.assert_not_called()
    assert rescreen.call_args.kwargs["application_ids"] == [world.member_app.id]
    assert rescreen.call_args.kwargs["require_all_memberships"] is True


@pytest.mark.parametrize("invalid_attr", ["foreign_app", "removed_app"])
def test_related_selected_score_rejects_entire_mixed_or_removed_batch(
    db,
    logical_role_world,
    invalid_attr,
):
    world = logical_role_world
    invalid = getattr(world, invalid_attr)
    with patch(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates"
    ) as rescreen:
        with pytest.raises(HTTPException) as exc_info:
            applications_routes.score_selected_applications(
                role_id=int(world.related.id),
                payload={
                    "application_ids": [
                        int(world.member_app.id),
                        int(invalid.id),
                    ]
                },
                db=db,
                current_user=world.user,
            )

    assert exc_info.value.status_code == 400
    rescreen.assert_not_called()


def test_ordinary_selected_score_rejects_mixed_role_before_enqueue(
    db,
    logical_role_world,
):
    world = logical_role_world
    with patch(
        "app.domains.assessments_runtime.applications_routes.enqueue_score"
    ) as enqueue:
        with pytest.raises(HTTPException) as exc_info:
            applications_routes.score_selected_applications(
                role_id=int(world.owner.id),
                payload={
                    "application_ids": [
                        int(world.member_app.id),
                        int(world.foreign_app.id),
                    ]
                },
                db=db,
                current_user=world.user,
            )

    assert exc_info.value.status_code == 400
    enqueue.assert_not_called()


def test_related_interview_refresh_is_explicit_restriction_not_owner_write(
    db,
    logical_role_world,
):
    world = logical_role_world
    world.member_app.screening_pack = {"owner": "unchanged"}
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        applications_routes.refresh_interview_support_bulk(
            role_id=int(world.related.id),
            payload={"application_ids": [int(world.member_app.id)]},
            db=db,
            current_user=world.user,
        )

    assert exc_info.value.status_code == 409
    db.refresh(world.member_app)
    assert world.member_app.screening_pack == {"owner": "unchanged"}


def test_related_batch_preview_counts_only_live_membership(
    db,
    logical_role_world,
):
    world = logical_role_world
    result = applications_routes.batch_score_role(
        role_id=int(world.related.id),
        include_scored=False,
        applied_after=None,
        dry_run=True,
        db=db,
        current_user=world.user,
    )

    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["total"] == 1
    assert result["will_score"] == 1


def test_related_selected_fetch_uses_logical_id_and_validated_transport(
    db,
    logical_role_world,
):
    world = logical_role_world
    world.member_app.source = "workable"
    world.member_app.cv_text = None
    world.member_app.candidate.cv_text = None
    db.commit()

    with patch(
        "app.domains.assessments_runtime.applications_routes.threading.Thread"
    ) as thread:
        result = applications_routes.fetch_cvs_selected_applications(
            role_id=int(world.related.id),
            payload={"application_ids": [int(world.member_app.id)]},
            db=db,
            current_user=world.user,
        )

    assert result == {
        "status": "started",
        "requested": 1,
        "fetching": 1,
        "already_present": 0,
    }
    assert thread.call_args.kwargs["kwargs"] == {
        "score_after": False,
        "role_id": int(world.related.id),
    }
    assert thread.call_args.kwargs["args"] == (
        [int(world.member_app.id)],
        int(world.org.id),
    )


def test_process_selection_rejects_outsider_without_starting_worker(
    db,
    logical_role_world,
):
    world = logical_role_world
    with patch("app.domains.assessments_runtime.applications_routes.threading.Thread") as thread:
        with pytest.raises(HTTPException) as exc_info:
            applications_routes.process_role(
                role_id=int(world.related.id),
                payload={
                    "score": "new",
                    "application_ids": [
                        int(world.member_app.id),
                        int(world.foreign_app.id),
                    ],
                },
                dry_run=False,
                db=db,
                current_user=world.user,
            )

    assert exc_info.value.status_code == 400
    thread.assert_not_called()


def test_related_process_preview_uses_role_local_membership(
    db,
    logical_role_world,
):
    world = logical_role_world
    result = applications_routes.process_role(
        role_id=int(world.related.id),
        payload={
            "score": "new",
            "application_ids": [int(world.member_app.id)],
        },
        dry_run=True,
        db=db,
        current_user=world.user,
    )

    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["total_candidates"] == 1
    assert result["score"] == {"will_run": 1, "mode": "new"}
    assert result["selected_count"] == 1


def test_related_process_worker_keeps_role_local_rescreen_after_extraction(
    db,
    logical_role_world,
):
    world = logical_role_world
    progress = applications_routes._empty_process_progress()

    with patch(
        "app.services.related_role_rescreen_service."
        "rescreen_related_role_candidates",
        return_value=_rescreen_result(world.evaluation.id),
    ) as rescreen:
        applications_routes._run_related_role_process(
            db,
            role=world.related,
            org=world.org,
            progress=progress,
            fetch_cvs=False,
            refresh_cvs=False,
            score_mode="new",
            sync_graph=False,
            refresh_graph=False,
            stage_filter=None,
            application_ids=[int(world.member_app.id)],
            user_id=int(world.user.id),
        )

    assert progress["status"] == "completed"
    assert progress["score"]["scored"] == 1
    assert rescreen.call_args.kwargs["application_ids"] == [world.member_app.id]
    assert rescreen.call_args.kwargs["require_all_memberships"] is True


def test_delayed_batch_task_rechecks_related_membership_and_uses_rescreen(
    db,
    logical_role_world,
    monkeypatch,
):
    from app.platform import database
    from app.tasks.scoring_tasks import batch_score_role as batch_score_task

    world = logical_role_world
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    with (
        patch(
            "app.services.related_role_rescreen_service."
            "rescreen_related_role_candidates",
            return_value=_rescreen_result(world.evaluation.id),
        ) as rescreen,
        patch("app.services.cv_score_orchestrator.enqueue_score") as owner_score,
    ):
        result = batch_score_task(
            int(world.related.id),
            include_scored=False,
        )

    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["count"] == 1
    owner_score.assert_not_called()
    assert rescreen.call_args.kwargs["application_ids"] == [world.member_app.id]
    assert rescreen.call_args.kwargs["require_all_memberships"] is True
