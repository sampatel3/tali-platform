"""Preservation-first ATS stage-move lifecycle and reconciliation tests."""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import ROLE_KIND_STANDARD, Role
from app.models.user import User
from app.services.ats_stage_move_dispatch_snapshot import (
    build_stage_move_dispatch_payload,
)
from app.services.ats_stage_move_lifecycle import execute_stage_move_lifecycle
from app.services.ats_stage_move_provider import StageMoveProviderFailure
from app.services.ats_stage_move_reconciliation import (
    StageReceiptIdentity,
    check_stage_move_reconciliation,
    resolve_stage_move_reconciliation,
)
from app.services.workable_actions_service import WorkableWritebackError
from tests.conftest import TestingSessionLocal


def _seed(db):
    suffix = uuid4().hex
    org = Organization(
        name=f"Stage lifecycle {suffix}",
        slug=f"stage-lifecycle-{suffix}",
        workable_connected=True,
        workable_access_token="workable-token",
        workable_subdomain="example",
        workable_config={
            "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
            "workable_writeback": True,
            "workable_actor_member_id": "member-1",
        },
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Stage owner",
        source="workable",
        role_kind=ROLE_KIND_STANDARD,
        workable_job_id=f"job-{suffix}",
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"stage-{suffix}@example.test",
        full_name="Stage Candidate",
    )
    owner = User(
        organization_id=org.id,
        email=f"owner-{suffix}@example.test",
        hashed_password="x",
        role="owner",
        is_active=True,
        is_verified=True,
    )
    db.add_all([role, candidate, owner])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="workable",
        status="applied",
        pipeline_stage="review",
        application_outcome="open",
        workable_candidate_id=f"candidate-{suffix}",
    )
    db.add(app)
    db.commit()
    payload = {
        **build_stage_move_dispatch_payload(
            app=app,
            owner_role=role,
            provider="workable",
            target_stage="technical-interview",
        ),
        "user_id": owner.id,
        "reason": "Exact stage hand-back",
    }
    return org, role, owner, app, payload


def _success(plan):
    return {
        "success": True,
        "code": "ok",
        "provider": plan.provider,
        "provider_remote_stage": plan.target_stage,
    }


def _observation(identity, stage):
    def observe(plan):
        assert plan.provider_target_id == identity.provider_target_id
        return {
            "success": True,
            "provider": identity.provider,
            "provider_target_id": identity.provider_target_id,
            "provider_remote_stage": stage,
            "provider_remote_stage_values": [stage],
            "observed_at": "2026-07-17T00:00:00+00:00",
            "evidence": {"candidate_id": identity.provider_target_id},
        }

    return observe


def _archive_ambiguous_stage_receipt(db, app, payload):
    persisted = db.get(CandidateApplication, app.id)
    state = deepcopy(persisted.integration_sync_state)
    archived = state["stage_move_operation"]
    newer = {
        **deepcopy(archived),
        "operation_id": f"{payload['operation_id']}:newer",
        "status": "confirmed",
        "provider_outcome_uncertain": False,
        "manual_reconciliation_required": False,
    }
    state["stage_move_operation_history"] = [
        {
            "operation_id": f"{payload['operation_id']}:retained-sibling",
            "status": "confirmed",
            "evidence": {"retain": True},
        },
        archived,
    ]
    state["stage_move_operation"] = newer
    persisted.integration_sync_state = state
    db.commit()
    return StageReceiptIdentity(
        operation_id=payload["operation_id"],
        provider="workable",
        provider_target_id=payload["provider_target_id"],
    )


def test_provider_phase_has_no_transaction_and_exact_replay_calls_once(db):
    org, _role, _owner, app, payload = _seed(db)
    calls = []

    def provider(plan):
        assert not db.in_transaction()
        calls.append(plan.provider_target_id)
        return _success(plan)

    result = execute_stage_move_lifecycle(
        db, organization_id=org.id, payload=payload, provider_call=provider
    )
    replay = execute_stage_move_lifecycle(
        db,
        organization_id=org.id,
        payload=payload,
        provider_call=lambda _plan: pytest.fail("confirmed replay called provider"),
    )

    db.refresh(app)
    receipt = app.integration_sync_state["stage_move_operation"]
    assert result["status"] == replay["status"] == "ok"
    assert replay["replayed"] is True
    assert calls == [app.workable_candidate_id]
    assert receipt["status"] == "confirmed"
    assert app.workable_stage == "technical-interview"


def test_ambiguous_provider_result_is_never_blindly_replayed(db):
    org, _role, _owner, app, payload = _seed(db)

    def ambiguous(_plan):
        raise StageMoveProviderFailure(
            code="api_error",
            message="timeout after request",
            provider_called=None,
            retriable=True,
        )

    with pytest.raises(WorkableWritebackError) as caught:
        execute_stage_move_lifecycle(
            db, organization_id=org.id, payload=payload, provider_call=ambiguous
        )
    assert caught.value.retriable is False
    assert caught.value.provider_called is None

    replay = execute_stage_move_lifecycle(
        db,
        organization_id=org.id,
        payload=payload,
        provider_call=lambda _plan: pytest.fail("ambiguous replay called provider"),
    )
    db.refresh(app)
    receipt = app.integration_sync_state["stage_move_operation"]
    assert replay["status"] == "reconciliation_required"
    assert receipt["status"] == "manual_reconciliation_required"
    assert receipt["provider_outcome_uncertain"] is True


def test_remote_success_with_concurrent_local_drift_preserves_local_state(db):
    org, _role, _owner, app, payload = _seed(db)

    def provider(plan):
        assert not db.in_transaction()
        other = TestingSessionLocal()
        try:
            changed = other.get(CandidateApplication, app.id)
            changed.pipeline_stage = "invited"
            changed.version = int(changed.version or 1) + 1
            other.commit()
        finally:
            other.close()
        return _success(plan)

    result = execute_stage_move_lifecycle(
        db, organization_id=org.id, payload=payload, provider_call=provider
    )

    db.expire_all()
    persisted = db.get(CandidateApplication, app.id)
    receipt = persisted.integration_sync_state["stage_move_operation"]
    assert result["status"] == "reconciliation_required"
    assert result["reconciliation_reason"] == "application_version_changed"
    assert persisted.pipeline_stage == "invited"
    assert persisted.workable_stage is None
    assert receipt["status"] == "manual_reconciliation_required"
    assert receipt["provider_succeeded"] is True


@pytest.mark.parametrize("receipt_key", ["outcome_writeback", "decision_provider_operation"])
def test_stage_claim_refuses_a_different_unresolved_provider_operation(db, receipt_key):
    org, _role, _owner, app, payload = _seed(db)
    blocker = {
        "operation_id": f"{receipt_key}:active",
        "status": "provider_call_started",
        "provider": "workable",
        "provider_target_id": app.workable_candidate_id,
        "provider_called": None,
        "provider_outcome_uncertain": True,
    }
    app.integration_sync_state = {receipt_key: blocker}
    db.commit()
    calls = []

    with pytest.raises(WorkableWritebackError) as caught:
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda plan: calls.append(plan),
        )

    assert caught.value.code == "provider_operation_in_progress"
    assert calls == []
    db.refresh(app)
    assert app.integration_sync_state == {receipt_key: blocker}


def test_exact_observation_confirms_ambiguous_stage_without_second_provider_write(db):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = StageReceiptIdentity(
        operation_id=payload["operation_id"],
        provider="workable",
        provider_target_id=payload["provider_target_id"],
    )
    observation = check_stage_move_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        observe=_observation(identity, "technical-interview"),
    )
    resolved = resolve_stage_move_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_stage_move",
        current_user=owner,
    )

    db.refresh(app)
    assert observation["remote_matches_expected"] is True
    assert resolved.get("reconciliation_reason") is None, resolved
    assert resolved["status"] == "ok", resolved
    assert app.integration_sync_state["stage_move_operation"]["status"] == "confirmed"
    assert app.workable_stage == "technical-interview"


def test_default_stage_observer_resolves_canonical_boundary_at_call_time(
    db, monkeypatch
):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = StageReceiptIdentity(
        operation_id=payload["operation_id"],
        provider="workable",
        provider_target_id=payload["provider_target_id"],
    )
    seen: list[str] = []

    def patched_observer(plan):
        assert not db.in_transaction()
        seen.append(plan.provider_target_id)
        return _observation(identity, "technical-interview")(plan)

    monkeypatch.setattr(
        "app.services.ats_stage_move_provider.perform_stage_move_provider_observation",
        patched_observer,
    )

    observation = check_stage_move_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
    )

    assert seen == [identity.provider_target_id]
    assert observation["remote_matches_expected"] is True


def test_mismatched_exact_observation_authorizes_only_one_durable_retry(db):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = StageReceiptIdentity(
        operation_id=payload["operation_id"],
        provider="workable",
        provider_target_id=payload["provider_target_id"],
    )
    observation = check_stage_move_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        observe=_observation(identity, "screening"),
    )
    with patch(
        "app.services.workable_op_runner.enqueue_workable_op", return_value=91
    ) as enqueue:
        resolved = resolve_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=observation["observation_id"],
            disposition="retry_stage_move",
            current_user=owner,
        )

    db.refresh(app)
    receipt = app.integration_sync_state["stage_move_operation"]
    assert observation["remote_matches_expected"] is False
    assert resolved["status"] == "queued"
    assert resolved["job_run_id"] == 91
    assert receipt["status"] == "retry_authorized"
    assert receipt["reconciliation_retry_observation_id"] == observation["observation_id"]
    enqueue.assert_called_once()
    assert enqueue.call_args.kwargs["payload"]["operation_id"] == payload["operation_id"]


def test_stage_provider_plans_redact_external_ids_and_connection_fields():
    from app.services.ats_stage_move_provider import (
        StageMoveProviderPlan,
        stage_move_observation_plan,
    )

    plan = StageMoveProviderPlan(
        provider="workable",
        provider_target_id="secret-candidate-id",
        target_stage="technical-interview",
        provider_remote_stage="secret-remote-stage",
        organization_id=42,
        workable_subdomain="secret-subdomain",
        workable_actor_member_id="secret-member-id",
        workable_access_token="secret-token",
        bullhorn_username="secret-username",
        bullhorn_client_id="secret-client-id",
        bullhorn_client_secret="secret-client-secret",
        bullhorn_refresh_token="secret-refresh-token",
        bullhorn_rest_url="secret-rest-url",
        bullhorn_credential_generation=99,
    )

    for rendered in (repr(plan), repr(stage_move_observation_plan(plan))):
        assert "provider='workable'" in rendered
        assert "organization_id=42" in rendered
        assert "secret-" not in rendered


@pytest.mark.parametrize(
    ("history_key", "history"),
    [
        (
            "reconciliation_observation_history",
            [{"observation_id": f"kept-{index}"} for index in range(100)],
        ),
        ("reconciliation_resolution_history", [{"kept": True}, "malformed"]),
    ],
)
def test_stage_history_fails_before_provider_observation_without_rewrite(
    db, history_key, history
):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    state = dict(current_app.integration_sync_state)
    receipt = dict(state["stage_move_operation"])
    receipt[history_key] = deepcopy(history)
    state["stage_move_operation"] = receipt
    current_app.integration_sync_state = state
    db.commit()
    original = deepcopy(receipt)
    identity = StageReceiptIdentity(
        operation_id=payload["operation_id"],
        provider="workable",
        provider_target_id=payload["provider_target_id"],
    )
    provider_calls = []

    with pytest.raises(HTTPException) as caught:
        check_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=owner,
            observe=lambda _plan: provider_calls.append(True),
        )

    assert caught.value.status_code == 409
    assert provider_calls == []
    db.rollback()
    db.refresh(current_app)
    assert current_app.integration_sync_state["stage_move_operation"] == original


def test_repeated_archived_checks_update_one_exact_projection_without_outer_growth(db):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = _archive_ambiguous_stage_receipt(db, app, payload)
    db.expire_all()
    persisted = db.get(CandidateApplication, app.id)
    initial_history = persisted.integration_sync_state["stage_move_operation_history"]
    initial_bytes = len(json.dumps(initial_history).encode("utf-8"))

    observations = [
        check_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=owner,
            observe=_observation(identity, "screening"),
        )
        for _ in range(4)
    ]

    db.expire_all()
    persisted = db.get(CandidateApplication, app.id)
    history = persisted.integration_sync_state["stage_move_operation_history"]
    projection = history[-1]
    final_bytes = len(json.dumps(history).encode("utf-8"))
    assert len(history) == len(initial_history) == 2
    assert history[0] == initial_history[0]
    assert final_bytes - initial_bytes == len(json.dumps(projection)) - len(
        json.dumps(initial_history[-1])
    )
    assert final_bytes > initial_bytes
    assert [
        item["observation_id"]
        for item in projection["reconciliation_observation_history"]
    ] == [item["observation_id"] for item in observations]
    events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type
            == "ats_stage_move_reconciliation_observed",
        )
        .all()
    )
    assert {event.event_metadata["observation_id"] for event in events} == {
        item["observation_id"] for item in observations
    }
    with pytest.raises(HTTPException) as caught:
        resolve_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=observations[-1]["observation_id"],
            disposition="confirm_stage_move",
            current_user=owner,
        )
    assert caught.value.status_code == 409
    assert "archived stage move" in str(caught.value.detail)


@pytest.mark.parametrize("failure", ["malformed_outer", "full_projection"])
def test_archived_check_fails_closed_before_provider_when_history_is_unsafe(db, failure):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = _archive_ambiguous_stage_receipt(db, app, payload)
    db.expire_all()
    persisted = db.get(CandidateApplication, app.id)
    state = deepcopy(persisted.integration_sync_state)
    if failure == "malformed_outer":
        state["stage_move_operation_history"].append("retain-malformed-evidence")
    else:
        state["stage_move_operation_history"][-1][
            "reconciliation_observation_history"
        ] = [{"observation_id": f"kept-{index}"} for index in range(100)]
    persisted.integration_sync_state = state
    db.commit()
    original = deepcopy(state)
    provider_calls = []

    with pytest.raises(HTTPException) as caught:
        check_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=owner,
            observe=lambda _plan: provider_calls.append(True),
        )

    assert caught.value.status_code == 409
    assert provider_calls == []
    db.rollback()
    db.refresh(persisted)
    assert persisted.integration_sync_state == original


def test_archived_check_cannot_overwrite_concurrent_evidence(db):
    org, _role, owner, app, payload = _seed(db)
    with pytest.raises(WorkableWritebackError):
        execute_stage_move_lifecycle(
            db,
            organization_id=org.id,
            payload=payload,
            provider_call=lambda _plan: (_ for _ in ()).throw(
                StageMoveProviderFailure(
                    code="api_error",
                    message="ambiguous",
                    provider_called=None,
                    retriable=True,
                )
            ),
        )
    identity = _archive_ambiguous_stage_receipt(db, app, payload)

    def observe_with_concurrent_evidence(plan):
        other = TestingSessionLocal()
        try:
            concurrent = other.get(CandidateApplication, app.id)
            state = deepcopy(concurrent.integration_sync_state)
            state["stage_move_operation_history"][-1]["concurrent_evidence"] = {
                "retain": True
            }
            concurrent.integration_sync_state = state
            other.commit()
        finally:
            other.close()
        return _observation(identity, "screening")(plan)

    with pytest.raises(HTTPException) as caught:
        check_stage_move_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=owner,
            observe=observe_with_concurrent_evidence,
        )

    assert caught.value.status_code == 409
    db.rollback()
    db.expire_all()
    persisted = db.get(CandidateApplication, app.id)
    history = persisted.integration_sync_state["stage_move_operation_history"]
    assert history[-1]["concurrent_evidence"] == {"retain": True}
    assert "reconciliation_observation_history" not in history[-1]
