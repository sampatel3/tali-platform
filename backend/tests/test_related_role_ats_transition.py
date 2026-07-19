"""Durability regressions for related-role moves through a shared ATS."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services import workable_op_runner
from app.services.ats_job_run_errors import AtsJobRunPersistenceError
from app.services.ats_stage_move_dispatch_snapshot import (
    build_stage_move_dispatch_payload,
)
from app.services.related_role_ats_transition import (
    advance_prepared_related_role_transition,
    prepare_related_role_ats_transition,
)
from app.services.workable_actions_service import WorkableWritebackError


def _shared_application(db, *, provider: str):
    org = Organization(
        name=f"Related ATS {provider}",
        slug=f"related-ats-{provider}-{uuid4().hex}",
        workable_connected=provider == "workable",
        workable_access_token="token" if provider == "workable" else None,
        workable_subdomain="example" if provider == "workable" else None,
        workable_config=(
            {
                "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
                "workable_writeback": True,
                "workable_actor_member_id": "member-1",
            }
            if provider == "workable"
            else {}
        ),
        bullhorn_connected=provider == "bullhorn",
        bullhorn_username="api-user" if provider == "bullhorn" else None,
        bullhorn_client_id="client-id" if provider == "bullhorn" else None,
        bullhorn_client_secret="encrypted-secret" if provider == "bullhorn" else None,
        bullhorn_refresh_token="encrypted-refresh" if provider == "bullhorn" else None,
    )
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=org.id,
        name="ATS owner",
        source=provider,
        role_kind=ROLE_KIND_STANDARD,
        workable_job_id="workable-job" if provider == "workable" else None,
        bullhorn_job_order_id="bullhorn-job" if provider == "bullhorn" else None,
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org.id,
        name="Related platform role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"related-{provider}-{uuid4().hex}@example.com",
        full_name="Related Candidate",
        bullhorn_candidate_id=(
            "67890" if provider == "bullhorn" else None
        ),
    )
    db.add_all([related, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source=provider,
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        workable_candidate_id=(
            "workable-candidate" if provider == "workable" else None
        ),
        bullhorn_job_submission_id=(
            "12345" if provider == "bullhorn" else None
        ),
    )
    db.add(application)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related.id,
        source_application_id=application.id,
        status="done",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        spec_fingerprint="related-ats-transition",
    )
    db.add(evaluation)
    db.commit()
    return org, owner, related, application, evaluation


def test_workable_move_commits_related_stage_and_replay_is_idempotent(db, monkeypatch):
    from app.platform.config import settings

    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    payload = {
        **build_stage_move_dispatch_payload(
            app=application,
            owner_role=owner,
            provider="workable",
            target_stage="Technical Interview",
            acting_role=related,
            related_evaluation=evaluation,
        ),
        "user_id": None,
    }

    with (
        patch(
            "app.components.integrations.workable.service.WorkableService.move_candidate",
            return_value={"success": True},
        ) as move,
        patch(
            "app.services.ats_note_dispatch.enqueue_application_ats_note",
            return_value=77,
        ) as post_note,
    ):
        first = workable_op_runner._op_move_stage(db, org.id, payload)
        db.refresh(evaluation)
        first_updated_at = evaluation.pipeline_stage_updated_at
        second = workable_op_runner._op_move_stage(db, org.id, payload)
        db.refresh(evaluation)

    assert first["status"] == second["status"] == "ok"
    assert first["application_id"] == second["application_id"] == application.id
    assert second["replayed"] is True
    assert move.call_count == 1
    assert post_note.call_count == 1
    assert evaluation.pipeline_stage == "advanced"
    assert evaluation.pipeline_stage_source == "recruiter"
    assert evaluation.pipeline_stage_updated_at == first_updated_at


def test_legacy_related_transition_import_remains_fail_closed_and_idempotent(db):
    _org, _owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    prepared = prepare_related_role_ats_transition(
        db,
        acting_role_id=int(related.id),
        application=application,
    )

    assert prepared is not None
    assert advance_prepared_related_role_transition(prepared) is related
    first_updated_at = evaluation.pipeline_stage_updated_at
    assert advance_prepared_related_role_transition(prepared) is None
    assert evaluation.pipeline_stage_updated_at == first_updated_at

    related.deleted_at = datetime.now(timezone.utc)
    db.flush()
    with pytest.raises(WorkableWritebackError) as caught:
        prepare_related_role_ats_transition(
            db,
            acting_role_id=int(related.id),
            application=application,
        )
    assert caught.value.code == "related_scope_unavailable"


def test_workable_attribution_note_has_an_explicit_at_least_once_crash_boundary(
    db, monkeypatch
):
    """A note-queue failure replays only the durable note, never the stage move."""

    from app.platform.config import settings

    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    payload = {
        **build_stage_move_dispatch_payload(
            app=application,
            owner_role=owner,
            provider="workable",
            target_stage="Technical Interview",
            acting_role=related,
            related_evaluation=evaluation,
        ),
        "user_id": None,
    }
    accepted_notes = []

    def post_note_then_lose_commit(*args, **kwargs):
        accepted_notes.append((args, kwargs))
        if len(accepted_notes) == 1:
            raise AtsJobRunPersistenceError("post_note")
        return 88

    with (
        patch(
            "app.components.integrations.workable.service.WorkableService.move_candidate",
            return_value={"success": True},
        ) as move,
        patch(
            "app.services.ats_note_dispatch.enqueue_application_ats_note",
            side_effect=post_note_then_lose_commit,
        ),
    ):
        with pytest.raises(WorkableWritebackError) as caught:
            workable_op_runner._op_move_stage(db, org.id, payload)
        assert caught.value.code == "note_tracking_unavailable"
        db.rollback()

        result = workable_op_runner._op_move_stage(db, org.id, payload)

    assert result["status"] == "ok"
    assert result["application_id"] == application.id
    assert result["replayed"] is True
    assert move.call_count == 1
    assert len(accepted_notes) == 2
    db.refresh(evaluation)
    assert evaluation.pipeline_stage == "advanced"


def test_bullhorn_move_uses_canonical_receipt_and_replay_is_idempotent(db):
    org, owner, related, application, evaluation = _shared_application(
        db, provider="bullhorn"
    )
    payload = {
        **build_stage_move_dispatch_payload(
            app=application,
            owner_role=owner,
            provider="bullhorn",
            target_stage="advanced",
            acting_role=related,
            related_evaluation=evaluation,
        ),
        "user_id": None,
    }

    with (
        patch("app.platform.config.settings.BULLHORN_ENABLED", True),
        patch(
            "app.components.integrations.bullhorn.write_back.resolve_remote_status",
            return_value="Interview Scheduled",
        ),
        patch(
            "app.services.ats_stage_move_provider.perform_stage_move_provider_call",
            return_value={
                "success": True,
                "code": "ok",
                "provider": "bullhorn",
                "provider_remote_stage": "Interview Scheduled",
            },
        ) as provider_call,
        patch(
            "app.services.ats_note_dispatch.enqueue_application_ats_note",
            return_value=91,
        ) as post_note,
    ):
        first = workable_op_runner._op_move_stage(db, org.id, payload)
        db.refresh(evaluation)
        first_updated_at = evaluation.pipeline_stage_updated_at
        second = workable_op_runner._op_move_stage(db, org.id, payload)
        db.refresh(evaluation)

    assert first["status"] == second["status"] == "ok"
    assert first["application_id"] == second["application_id"] == application.id
    assert second["replayed"] is True
    provider_call.assert_called_once()
    post_note.assert_called_once()
    assert evaluation.pipeline_stage == "advanced"
    assert evaluation.pipeline_stage_source == "recruiter"
    assert evaluation.pipeline_stage_updated_at == first_updated_at


def test_worker_does_not_advance_or_attribute_a_soft_deleted_related_role(db):
    org, _owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    related.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(evaluation)
    original_updated_at = evaluation.pipeline_stage_updated_at
    payload = {
        "application_id": application.id,
        "target_stage": "Technical Interview",
        "acting_role_id": related.id,
        "user_id": None,
    }

    with (
        patch(
            "app.services.workable_actions_service.move_candidate_in_workable"
        ) as move,
        patch("app.services.workable_op_runner._op_post_note") as post_note,
        pytest.raises(WorkableWritebackError) as caught,
    ):
        workable_op_runner._op_move_stage(db, org.id, payload)

    assert caught.value.code == "related_scope_unavailable"
    move.assert_not_called()
    post_note.assert_not_called()
    db.refresh(evaluation)
    assert evaluation.pipeline_stage == "review"
    assert evaluation.pipeline_stage_updated_at == original_updated_at


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("application_deleted", "application_closed"),
        ("candidate_deleted", "application_scope_changed"),
        ("candidate_wrong_org", "application_scope_changed"),
        ("owner_deleted", "application_scope_changed"),
        ("owner_reassigned", "related_scope_unavailable"),
    ],
)
def test_worker_rechecks_live_roster_before_provider_write(
    db, mutation, expected_code
):
    org, owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    if mutation == "application_deleted":
        application.deleted_at = datetime.now(timezone.utc)
    elif mutation == "candidate_deleted":
        application.candidate.deleted_at = datetime.now(timezone.utc)
    elif mutation == "candidate_wrong_org":
        other_org = Organization(
            name="Other ATS tenant",
            slug=f"other-ats-{uuid4().hex}",
        )
        db.add(other_org)
        db.flush()
        application.candidate.organization_id = int(other_org.id)
    elif mutation == "owner_deleted":
        owner.deleted_at = datetime.now(timezone.utc)
    else:
        other_owner = Role(
            organization_id=org.id,
            name="Different ATS owner",
            source="workable",
            role_kind=ROLE_KIND_STANDARD,
            workable_job_id="different-workable-job",
        )
        db.add(other_owner)
        db.flush()
        application.role_id = int(other_owner.id)
    db.commit()

    with (
        patch(
            "app.services.workable_actions_service.move_candidate_in_workable"
        ) as move,
        pytest.raises(WorkableWritebackError) as caught,
    ):
        workable_op_runner._op_move_stage(
            db,
            org.id,
            {
                "application_id": application.id,
                "target_stage": "Technical Interview",
                "acting_role_id": related.id,
            },
        )

    assert caught.value.code == expected_code
    move.assert_not_called()
    db.refresh(evaluation)
    assert evaluation.pipeline_stage == "review"


def test_worker_rechecks_closed_state_before_workable_provider_write(db):
    org, _owner, related, application, evaluation = _shared_application(
        db, provider="workable"
    )
    application.application_outcome = "withdrawn"
    db.commit()

    with patch(
        "app.services.workable_actions_service.move_candidate_in_workable"
    ) as move:
        with pytest.raises(WorkableWritebackError) as caught:
            workable_op_runner._op_move_stage(
                db,
                org.id,
                {
                    "application_id": application.id,
                    "target_stage": "Technical Interview",
                    "acting_role_id": related.id,
                },
            )

    assert caught.value.code == "application_closed"
    move.assert_not_called()
    db.refresh(evaluation)
    assert evaluation.pipeline_stage == "review"
