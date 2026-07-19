from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.background_job_run import BackgroundJobRun
from app.models.organization import Organization
from app.models.role import Role
from app.platform.config import settings
from app.services.ats_note_provider import AtsNoteProviderFailure
from app.services.ats_note_audit import ats_note_event_key
from app.services.ats_note_receipt import ATS_NOTE_WRITEBACK_KEY
from app.services.workable_op_runner import OP_POST_NOTE, execute_op


@pytest.fixture
def workable_enabled(monkeypatch):
    """Workable writes are opt-in in every test that reaches that boundary."""

    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)


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


def test_note_provider_callback_is_detached_and_receipt_is_sanitized(
    db, workable_enabled
):
    org, _role, _candidate, app = _seed(db)
    body = "Useful recruiter context"

    def provider(plan, *, should_yield):
        assert not db.in_transaction()
        assert callable(should_yield)
        assert should_yield() is False
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
            should_yield=lambda: False,
        )

    assert result["status"] == "ok"
    assert call.call_count == 1
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["status"] == "confirmed"
    assert receipt["body_preview"] == body
    assert "body" not in receipt
    assert receipt["attempts"] == 1


def test_ambiguous_note_is_never_blindly_retried(db, workable_enabled):
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


def test_provider_success_checkpoint_replay_finishes_without_reposting(
    db, workable_enabled, monkeypatch
):
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
    # The kill switch blocks only a new provider write; a confirmed checkpoint
    # must still finish its safe local audit state after a worker restart.
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", True)

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


def test_indexed_confirmed_event_prevents_a_b_a_repost(db, workable_enabled):
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
    confirmed_a = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == int(app.id),
            CandidateApplicationEvent.idempotency_key
            == ats_note_event_key("note-A", "confirmed"),
        )
        .one()
    )
    assert confirmed_a.event_metadata["operation_id"] == "note-A"
    assert confirmed_a.event_metadata["note_intent_sha256"]
    db.refresh(app)
    assert "ats_note_writeback_history" not in app.integration_sync_state
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

    def provider(plan, *, should_yield=None):
        assert not db.in_transaction()
        assert should_yield is None
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
    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload
        )

    assert result["code"] == "invalid_provider"
    assert result["provider_called"] is False
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


def test_note_receipt_preview_redacts_credentials(db, workable_enabled):
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
def test_note_claim_requires_explicit_exact_targets(
    db, missing_field, workable_enabled
):
    org, _role, _candidate, app = _seed(db)
    payload = _payload(app, operation=f"note-missing-{missing_field}", body="Target me")
    payload.pop(missing_field)

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db, organization_id=org.id, op_type=OP_POST_NOTE, payload=payload
        )

    assert result["code"] == "not_linked"
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.rollback()


def test_bullhorn_note_respects_disabled_feature_gate(db, monkeypatch):
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)
    org, _role, _candidate, app = _seed(db, dual_linked=True)

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
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

    assert result["code"] == "not_configured"
    assert result["provider_called"] is False
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


def test_legacy_note_action_maps_only_expected_queue_failures(db, workable_enabled):
    from app.actions.post_workable_note import run
    from app.actions.types import Actor
    from app.services.ats_job_run_errors import AtsJobRunPersistenceError
    from app.services.ats_note_dispatch import AtsNoteQueueError

    org, _role, _candidate, app = _seed(db)
    call = {
        "db": db,
        "actor": Actor.agent(55),
        "organization_id": int(org.id),
        "application_id": int(app.id),
        "body": "Legacy tool context",
    }
    with patch(
        "app.services.ats_note_dispatch.enqueue_application_ats_note",
        side_effect=AtsNoteQueueError(
            "role_unavailable", "The role is no longer available"
        ),
    ):
        refusal = run(
            call["db"],
            call["actor"],
            organization_id=call["organization_id"],
            application_id=call["application_id"],
            body=call["body"],
        )
    with patch(
        "app.services.ats_note_dispatch.enqueue_application_ats_note",
        side_effect=AtsJobRunPersistenceError("post_note"),
    ):
        persistence = run(
            call["db"],
            call["actor"],
            organization_id=call["organization_id"],
            application_id=call["application_id"],
            body=call["body"],
        )

    assert refusal.status == "skipped"
    assert refusal.detail == "The role is no longer available"
    assert persistence.status == "failed"
    assert "no provider call was made" in str(persistence.detail)


def test_legacy_note_action_does_not_hide_unexpected_queue_faults(
    db, workable_enabled
):
    from app.actions.post_workable_note import run
    from app.actions.types import Actor

    org, _role, _candidate, app = _seed(db)
    with (
        patch(
            "app.services.ats_note_dispatch.enqueue_application_ats_note",
            side_effect=RuntimeError("programmer fault"),
        ),
        pytest.raises(RuntimeError, match="programmer fault"),
    ):
        run(
            db,
            Actor.agent(55),
            organization_id=int(org.id),
            application_id=int(app.id),
            body="Legacy tool context",
        )


def test_note_claim_refuses_an_unresolved_stage_receipt(db, workable_enabled):
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

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db,
            organization_id=org.id,
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation="note-blocked-by-stage",
                body="Must wait for stage",
            ),
        )

    assert result["code"] == "conflicting_provider_operation"
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.rollback()


def _assert_soft_deleted_scope_is_not_queued(db, *, target: str, expected_code: str):
    from app.services.ats_note_dispatch import (
        AtsNoteQueueError,
        enqueue_application_ats_note,
    )

    org, role, candidate, app = _seed(db)
    entity = {"application": app, "candidate": candidate, "role": role}[target]
    entity.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with (
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(AtsNoteQueueError) as exc_info,
    ):
        enqueue_application_ats_note(
            db,
            organization_id=int(org.id),
            application_id=int(app.id),
            body="Do not queue this note",
            provider="workable",
            actor_type="recruiter",
            actor_id=41,
        )

    assert exc_info.value.code == expected_code
    enqueue.assert_not_called()


def test_note_enqueue_rejects_soft_deleted_application(db, workable_enabled):
    _assert_soft_deleted_scope_is_not_queued(
        db, target="application", expected_code="application_unavailable"
    )


def test_note_enqueue_rejects_soft_deleted_candidate(db, workable_enabled):
    _assert_soft_deleted_scope_is_not_queued(
        db, target="candidate", expected_code="candidate_unavailable"
    )


def test_note_enqueue_rejects_soft_deleted_role(db, workable_enabled):
    _assert_soft_deleted_scope_is_not_queued(
        db, target="role", expected_code="role_unavailable"
    )


def test_note_enqueue_rejects_oversize_body_without_truncating_identity(
    db, workable_enabled
):
    from app.services.ats_note_claim import normalize_ats_note_body
    from app.services.ats_note_dispatch import (
        AtsNoteQueueError,
        enqueue_application_ats_note,
    )

    org, _role, _candidate, app = _seed(db)
    body = ("x" * 8_000) + "different-tail"
    assert normalize_ats_note_body(body) == body

    with (
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(AtsNoteQueueError) as exc_info,
    ):
        enqueue_application_ats_note(
            db,
            organization_id=int(org.id),
            application_id=int(app.id),
            body=body,
            provider="workable",
            actor_type="recruiter",
            actor_id=41,
        )

    assert exc_info.value.code == "note_too_long"
    enqueue.assert_not_called()
    assert db.query(BackgroundJobRun).count() == 0


def test_queued_oversize_note_stops_before_provider_or_receipt(
    db, workable_enabled
):
    org, _role, _candidate, app = _seed(db)
    payload = _payload(
        app,
        operation="note-oversize-historical-payload",
        body=("x" * 8_000) + "different-tail",
    )

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=payload,
        )

    assert result["code"] == "note_too_long"
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.refresh(app)
    assert ATS_NOTE_WRITEBACK_KEY not in dict(app.integration_sync_state or {})


@pytest.mark.parametrize(
    "missing_authority",
    [
        "feature_flag",
        "connection",
        "username",
        "client_id",
        "client_secret",
        "refresh_token",
    ],
)
def test_bullhorn_enqueue_rejects_unusable_authority_before_queue(
    db, monkeypatch, missing_authority
):
    from app.services.ats_note_dispatch import (
        AtsNoteQueueError,
        enqueue_application_ats_note,
    )

    org, _role, _candidate, app = _seed(db, dual_linked=True)
    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    if missing_authority == "feature_flag":
        monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)
    elif missing_authority == "connection":
        org.bullhorn_connected = False
    elif missing_authority == "username":
        org.bullhorn_username = None
    elif missing_authority == "client_id":
        org.bullhorn_client_id = None
    elif missing_authority == "client_secret":
        org.bullhorn_client_secret = None
    elif missing_authority == "refresh_token":
        org.bullhorn_refresh_token = None
    db.commit()

    with (
        patch("app.services.workable_op_runner.enqueue_workable_op") as enqueue,
        pytest.raises(AtsNoteQueueError) as exc_info,
    ):
        enqueue_application_ats_note(
            db,
            organization_id=int(org.id),
            application_id=int(app.id),
            body="Do not queue an impossible Bullhorn write",
            provider="bullhorn",
            actor_type="recruiter",
            actor_id=41,
        )

    expected_code = (
        "bullhorn_disabled"
        if missing_authority == "feature_flag"
        else "bullhorn_not_configured"
    )
    assert exc_info.value.code == expected_code
    assert "secret" not in exc_info.value.message.lower()
    assert "token" not in exc_info.value.message.lower()
    enqueue.assert_not_called()
    assert db.query(BackgroundJobRun).count() == 0


def _assert_soft_deleted_scope_never_reaches_provider(
    db,
    workable_enabled,
    *,
    target: str,
    expected_code: str,
):
    org, role, candidate, app = _seed(db)
    payload = _payload(
        app,
        operation=f"note-deleted-{target}",
        body="Queued before the roster changed",
    )
    entity = {"application": app, "candidate": candidate, "role": role}[target]
    entity.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=payload,
        )

    assert result["code"] == expected_code
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.rollback()


def test_queued_note_stops_when_application_is_soft_deleted(
    db, workable_enabled
):
    _assert_soft_deleted_scope_never_reaches_provider(
        db,
        workable_enabled,
        target="application",
        expected_code="application_unavailable",
    )


def test_queued_note_stops_when_candidate_is_soft_deleted(db, workable_enabled):
    _assert_soft_deleted_scope_never_reaches_provider(
        db,
        workable_enabled,
        target="candidate",
        expected_code="candidate_unavailable",
    )


def test_queued_note_stops_when_role_is_soft_deleted(db, workable_enabled):
    _assert_soft_deleted_scope_never_reaches_provider(
        db,
        workable_enabled,
        target="role",
        expected_code="role_unavailable",
    )


@pytest.mark.parametrize(
    ("target", "expected_code"),
    [
        ("application", "application_unavailable"),
        ("candidate", "candidate_unavailable"),
        ("role", "role_unavailable"),
    ],
)
def test_note_relocks_scope_after_claim_before_provider(
    db, workable_enabled, target, expected_code
):
    from app.services import ats_note_writeback

    org, role, candidate, app = _seed(db)
    entity = {"application": app, "candidate": candidate, "role": role}[target]
    original_prepare = ats_note_writeback.prepare_ats_note_delivery

    def prepare_then_delete(*args, **kwargs):
        plan, terminal = original_prepare(*args, **kwargs)
        assert plan is not None and terminal is None
        entity.deleted_at = datetime.now(timezone.utc)
        db.commit()
        return plan, terminal

    with (
        patch(
            "app.services.ats_note_writeback.prepare_ats_note_delivery",
            side_effect=prepare_then_delete,
        ),
        patch(
            "app.services.ats_note_writeback.perform_ats_note_provider_call"
        ) as provider,
    ):
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation=f"note-deleted-after-claim-{target}",
                body="Claimed before the roster changed",
            ),
        )

    assert result["status"] == "failed"
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.refresh(app)
    receipt = app.integration_sync_state[ATS_NOTE_WRITEBACK_KEY]
    assert receipt["failure_code"] == expected_code


def test_queued_note_stops_at_worker_claim_when_workable_is_disabled(
    db, monkeypatch
):
    org, _role, _candidate, app = _seed(db)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", True)

    with patch(
        "app.services.ats_note_writeback.perform_ats_note_provider_call"
    ) as provider:
        result = execute_op(
            db,
            organization_id=int(org.id),
            op_type=OP_POST_NOTE,
            payload=_payload(
                app,
                operation="note-disabled-after-queue",
                body="Accepted before the kill switch changed",
            ),
        )

    assert result["code"] == "workable_disabled"
    assert result["provider_called"] is False
    provider.assert_not_called()
    db.refresh(app)
    assert ATS_NOTE_WRITEBACK_KEY not in dict(app.integration_sync_state or {})


def test_workable_kill_switch_stops_a_plan_claimed_while_enabled(
    db, workable_enabled, monkeypatch
):
    from app.services.ats_note_claim import (
        ensure_note_operation_payload,
        prepare_ats_note_delivery,
    )
    from app.services.ats_note_provider import perform_ats_note_provider_call

    org, _role, _candidate, app = _seed(db)
    payload = ensure_note_operation_payload(
        _payload(
            app,
            operation="note-disabled-after-claim",
            body="Must never leave the process",
        ),
        organization_id=int(org.id),
    )
    plan, terminal = prepare_ats_note_delivery(
        db, organization_id=int(org.id), payload=payload
    )
    assert terminal is None and plan is not None
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", True)

    with (
        patch(
            "app.components.integrations.workable.service.WorkableService"
        ) as client,
        pytest.raises(AtsNoteProviderFailure) as exc_info,
    ):
        perform_ats_note_provider_call(plan)

    assert exc_info.value.code == "workable_disabled"
    assert exc_info.value.provider_called is False
    client.assert_not_called()
