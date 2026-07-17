from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.platform.config import settings
from app.services.ats_note_provider import AtsNoteProviderFailure
from app.services.ats_note_receipt import (
    ATS_NOTE_WRITEBACK_HISTORY_KEY,
    ATS_NOTE_WRITEBACK_KEY,
)
from app.services.workable_op_runner import OP_POST_NOTE, execute_op


def _seed(db, *, dual_linked: bool = False):
    org = Organization(
        name="ATS note org",
        slug=f"ats-note-{id(db)}-{int(dual_linked)}",
        sync_mode="bullhorn_primary" if dual_linked else "workable_primary",
        workable_connected=True,
        workable_access_token="workable-secret-token",
        workable_subdomain="notes",
        workable_config={
            "workable_writeback": True,
            "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
            "workable_actor_member_id": "member-1",
        },
        bullhorn_connected=dual_linked,
        bullhorn_username="api-user" if dual_linked else None,
        bullhorn_client_id="client-id" if dual_linked else None,
        bullhorn_client_secret="encrypted-secret" if dual_linked else None,
        bullhorn_refresh_token="encrypted-refresh" if dual_linked else None,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Engineer",
        source="bullhorn" if dual_linked else "workable",
        workable_job_id="workable-job",
        bullhorn_job_order_id="42" if dual_linked else None,
    )
    candidate = Candidate(
        organization_id=org.id,
        email="note-candidate@example.test",
        full_name="Note Candidate",
        bullhorn_candidate_id="71" if dual_linked else None,
    )
    db.add_all([role, candidate])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source=role.source,
        status="review",
        pipeline_stage="review",
        application_outcome="open",
        workable_candidate_id="workable-candidate",
        bullhorn_job_submission_id="91" if dual_linked else None,
    )
    db.add(app)
    db.commit()
    return org, role, candidate, app


def _payload(app, *, operation: str, body: str, provider: str = "workable"):
    if provider == "bullhorn":
        application_target = str(app.bullhorn_job_submission_id)
        candidate_target = str(app.candidate.bullhorn_candidate_id)
    else:
        application_target = candidate_target = str(app.workable_candidate_id)
    return {
        "application_id": int(app.id),
        "body": body,
        "provider": provider,
        "provider_target_id": application_target,
        "candidate_provider_id": candidate_target,
        "note_operation_id": operation,
        "actor_type": "recruiter",
    }


def test_note_provider_callback_is_detached_and_receipt_is_sanitized(db):
    org, _role, _candidate, app = _seed(db)
    body = "Useful recruiter context"

    def provider(plan):
        assert not db.in_transaction()
        assert plan.body == body
        assert body not in repr(plan)
        assert org.workable_access_token not in repr(plan)
        return {"provider": "workable", "provider_confirmed": True}

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        side_effect=provider,
    ) as call:
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=_payload(app, operation="note-detached", body=body),
        )

    assert result["status"] == "ok"
    assert call.call_count == 1
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["status"] == "confirmed"
    assert receipt["body_preview"] == body
    assert "body" not in receipt
    assert receipt["attempts"] == 1


def test_ambiguous_note_is_never_blindly_retried(db):
    org, _role, _candidate, app = _seed(db)
    payload = _payload(app, operation="note-ambiguous", body="May have posted")
    failure = AtsNoteProviderFailure(
        code="api_error",
        message="Provider result is uncertain",
        provider_called=None,
    )

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        side_effect=failure,
    ):
        first = execute_op(
            db, organization_id=int(org.id), op_type=OP_POST_NOTE, payload=payload
        )
    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        second = execute_op(
            db, organization_id=int(org.id), op_type=OP_POST_NOTE, payload=payload
        )

    assert first["status"] == "manual_reconciliation_required"
    assert second["status"] == "manual_reconciliation_required"
    provider.assert_not_called()
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["provider_called"] is None
    assert receipt["provider_succeeded"] is None
    assert receipt["attempts"] == 1


def test_provider_success_checkpoint_replay_finishes_without_reposting(db):
    from app.services.ats_note_claim import (
        ensure_note_operation_payload,
        prepare_ats_note_delivery,
    )
    from app.services.ats_note_writeback import checkpoint_ats_note_provider_success

    org, _role, _candidate, app = _seed(db)
    payload = ensure_note_operation_payload(
        _payload(app, operation="note-checkpoint", body="Checkpoint me"),
        organization_id=int(org.id),
    )
    plan, terminal = prepare_ats_note_delivery(
        db, organization_id=int(org.id), payload=payload
    )
    assert terminal is None and plan is not None and not db.in_transaction()
    assert (
        checkpoint_ats_note_provider_success(
            db,
            plan=plan,
            provider_result={"provider": "workable", "provider_confirmed": True},
        )
        is None
    )

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        first = execute_op(
            db, organization_id=int(org.id), op_type=OP_POST_NOTE, payload=payload
        )
        second = execute_op(
            db, organization_id=int(org.id), op_type=OP_POST_NOTE, payload=payload
        )

    assert first["status"] == "ok"
    assert second["status"] == "already_completed"
    provider.assert_not_called()
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.event_type == "workable_note_posted",
        )
        .count()
        == 1
    )


def test_confirmed_history_prevents_a_b_a_repost(db):
    org, _role, _candidate, app = _seed(db)
    payload_a = _payload(app, operation="note-A", body="First note")
    payload_b = _payload(app, operation="note-B", body="Second note")

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        return_value={"provider": "workable", "provider_confirmed": True},
    ) as provider:
        assert (
            execute_op(
                db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload_a
            )["status"]
            == "ok"
        )
        assert (
            execute_op(
                db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload_b
            )["status"]
            == "ok"
        )
        replay = execute_op(
            db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload_a
        )

    assert replay["status"] == "already_completed"
    assert provider.call_count == 2
    db.refresh(app)
    history = app.integration_sync_state[ATS_NOTE_WRITEBACK_HISTORY_KEY]
    assert any(
        item["operation_id"] == "note-A" and item["status"] == "confirmed"
        for item in history
    )
    assert (
        app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]["operation_id"] == "note-B"
    )


def test_explicit_bullhorn_note_is_allowed_on_dual_linked_application(db, monkeypatch):
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org, _role, _candidate, app = _seed(db, dual_linked=True)
    payload = _payload(
        app,
        operation="note-dual-bullhorn",
        body="Bullhorn owns this dual-linked application",
        provider="bullhorn",
    )

    def provider(plan):
        assert not db.in_transaction()
        assert plan.provider == "bullhorn"
        assert plan.application_provider_target_id == "91"
        assert plan.provider_target_id == "71"
        return {
            "provider": "bullhorn",
            "provider_confirmed": True,
            "provider_receipt_id": "301",
        }

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        side_effect=provider,
    ):
        result = execute_op(
            db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload
        )

    assert result["status"] == "ok"
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["provider"] == "bullhorn"
    assert receipt["provider_result"]["provider_receipt_id"] == "301"


def test_note_without_explicit_provider_fails_before_provider_call(db):
    org, _role, _candidate, app = _seed(db)
    payload = {
        "application_id": int(app.id),
        "body": "No provider",
        "note_operation_id": "note-no-provider",
    }
    with (
        patch(
            "app.services.ats_note_writeback.perform_ats_note_provider_call"
        ) as provider,
        pytest.raises(AtsNoteProviderFailure) as exc_info,
    ):
        execute_op(db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload)

    assert exc_info.value.code == "invalid_provider"
    assert exc_info.value.provider_called is False
    provider.assert_not_called()
    db.rollback()


@pytest.mark.parametrize("response", [None, {}, []])
def test_malformed_bullhorn_note_receipt_is_ambiguous(db, response, monkeypatch):
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org, _role, _candidate, app = _seed(db, dual_linked=True)

    class Client:
        def create_note(self, **_kwargs):
            return response

    with patch(
        "app.services.ats_note_provider._bullhorn_client", return_value=Client()
    ):
        result = execute_op(
            db,
            organization_id=org.id,
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation=f"note-malformed-{type(response).__name__}",
                body="Expect a Bullhorn receipt",
                provider="bullhorn",
            ),
        )

    assert result["status"] == "manual_reconciliation_required"
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["provider_called"] is None
    assert receipt["provider_succeeded"] is None


def test_note_receipt_preview_redacts_credentials(db):
    org, _role, _candidate, app = _seed(db)
    body = (
        "Authorization: Bearer bearer-secret "
        'refresh_token="refresh-secret" client_secret=client-secret'
    )
    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call",
        return_value={"provider": "workable", "provider_confirmed": True},
    ):
        result = execute_op(
            db,
            organization_id=org.id,
            op_type=OP_POST_NOTE,
            payload=_payload(app, operation="note-redacted-preview", body=body),
        )

    assert result["status"] == "ok"
    db.refresh(app)
    receipt_json = json.dumps(app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY])
    assert "bearer-secret" not in receipt_json
    assert "refresh-secret" not in receipt_json
    assert "client-secret" not in receipt_json
    assert "[REDACTED]" in receipt_json


@pytest.mark.parametrize(
    "missing_field", ["provider_target_id", "candidate_provider_id"]
)
def test_note_claim_requires_explicit_exact_targets(db, missing_field):
    org, _role, _candidate, app = _seed(db)
    payload = _payload(app, operation=f"note-missing-{missing_field}", body="Target me")
    payload.pop(missing_field)

    with (
        patch(
            "app.services.ats_note_writeback.perform_ats_note_provider_call"
        ) as provider,
        pytest.raises(AtsNoteProviderFailure) as exc_info,
    ):
        execute_op(db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload)

    assert exc_info.value.code == "not_linked"
    assert exc_info.value.provider_called is False
    provider.assert_not_called()
    db.rollback()


def test_bullhorn_note_respects_disabled_feature_gate(db, monkeypatch):
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)
    org, _role, _candidate, app = _seed(db, dual_linked=True)

    with (
        patch(
            "app.services.ats_note_writeback.perform_ats_note_provider_call"
        ) as provider,
        pytest.raises(AtsNoteProviderFailure) as exc_info,
    ):
        execute_op(
            db,
            organization_id=org.id,
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation="note-bullhorn-disabled",
                body="Do not send",
                provider="bullhorn",
            ),
        )

    assert exc_info.value.code == "not_configured"
    assert exc_info.value.provider_called is False
    provider.assert_not_called()
    db.rollback()


def test_bullhorn_success_checkpoint_survives_credential_rotation(db, monkeypatch):
    from app.services.ats_note_claim import (
        ensure_note_operation_payload,
        prepare_ats_note_delivery,
    )
    from app.services.ats_note_writeback import checkpoint_ats_note_provider_success

    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org, _role, _candidate, app = _seed(db, dual_linked=True)
    payload = ensure_note_operation_payload(
        _payload(
            app,
            operation="note-bullhorn-credential-rotation",
            body="Checkpoint after OAuth refresh",
            provider="bullhorn",
        ),
        organization_id=int(org.id),
    )
    plan, terminal = prepare_ats_note_delivery(
        db, organization_id=int(org.id), payload=payload
    )
    assert plan is not None and terminal is None

    org.bullhorn_refresh_token = "rotated-encrypted-refresh"
    org.bullhorn_rest_url = "https://rest-rotated.example.test/rest-services"
    org.bullhorn_credential_generation = 1
    db.commit()
    assert (
        checkpoint_ats_note_provider_success(
            db,
            plan=plan,
            provider_result={
                "provider": "bullhorn",
                "provider_confirmed": True,
                "provider_receipt_id": "501",
            },
        )
        is None
    )

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db, organization_id=int(org.id), op_type=OP_POST_NOTE, payload=payload
        )

    assert result["status"] == "ok"
    provider.assert_not_called()
    db.refresh(app)
    assert app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]["status"] == "confirmed"


def test_legacy_note_action_only_durably_queues(db, monkeypatch):
    from app.actions.post_workable_note import run
    from app.actions.types import Actor
    from app.platform.config import settings

    org, _role, _candidate, app = _seed(db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=731
    ) as enqueue:
        result = run(
            db,
            Actor.agent(55),
            organization_id=int(org.id),
            application_id=int(app.id),
            body="Legacy tool context",
        )

    assert result.status == "queued"
    assert "731" in str(result.detail)
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["provider"] == "workable"
    assert payload["provider_target_id"] == str(app.workable_candidate_id)
    assert payload["candidate_provider_id"] == str(app.workable_candidate_id)


def test_note_claim_refuses_an_unresolved_stage_receipt(db):
    org, _role, _candidate, app = _seed(db)
    state = dict(app.integration_sync_state or {})
    state["stage_move_operation"] = {
        "operation_id": "stage-in-flight",
        "status": "provider_call_started",
        "provider_called": None,
        "provider_succeeded": None,
        "provider_outcome_uncertain": True,
    }
    app.integration_sync_state = state
    db.commit()

    with (
        patch(
            "app.services.ats_note_writeback.perform_ats_note_provider_call"
        ) as provider,
        pytest.raises(AtsNoteProviderFailure) as exc_info,
    ):
        execute_op(
            db,
            organization_id=org.id,
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation="note-blocked-by-stage",
                body="Must wait for stage",
            ),
        )

    assert exc_info.value.code == "conflicting_provider_operation"
    assert exc_info.value.provider_called is False
    provider.assert_not_called()
    db.rollback()
