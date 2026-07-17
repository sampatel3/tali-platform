from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.actions.types import Actor
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.platform.config import settings
from app.services.decision_provider_call import DecisionProviderFailure
from app.services.decision_provider_lifecycle import (
    execute_decision_provider_lifecycle,
)
from app.services.decision_provider_reconciliation import (
    DecisionReceiptIdentity,
    check_decision_provider_reconciliation,
    resolve_decision_provider_reconciliation,
)
from app.services.workable_actions_service import WorkableWritebackError
from tests.conftest import TestingSessionLocal


def _seed_reject(db):
    suffix = uuid4().hex
    org = Organization(
        name=f"Decision provider {suffix}",
        slug=f"decision-provider-{suffix}",
        workable_connected=True,
        workable_access_token=f"secret-token-{suffix}",
        workable_subdomain="acme",
        workable_config={
            "granted_scopes": ["r_jobs", "r_candidates", "w_candidates"],
            "workable_writeback": True,
            "workable_actor_member_id": "member-1",
            "workable_disqualify_reason_id": "reason-1",
        },
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="workable",
        workable_job_id="job-1",
        workable_job_data={"state": "published"},
        agentic_mode_enabled=True,
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"candidate-{suffix}@example.test",
        full_name="Candidate",
    )
    user = User(
        organization_id=org.id,
        email=f"recruiter-{suffix}@example.test",
        hashed_password="x",
        role="owner",
        is_active=True,
        is_verified=True,
    )
    db.add_all([role, candidate, user])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="workable",
        workable_candidate_id=f"candidate-{suffix}",
        version=1,
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type="reject",
        recommendation="reject",
        status="processing",
        reasoning="Missing a must-have skill",
        evidence={"must_have": "missing"},
        model_version="model-v1",
        prompt_version="prompt-v1",
        idempotency_key=f"decision-provider:{suffix}",
    )
    db.add(decision)
    db.commit()
    return org, role, user, app, decision


def _execute(db, org, user, decision, provider_call):
    return execute_decision_provider_lifecycle(
        db,
        organization_id=int(org.id),
        decision_id=int(decision.id),
        disposition="approved",
        actor=Actor.recruiter(user),
        note="Confirmed by recruiter",
        expected_decision_type="reject",
        provider_call=provider_call,
    )


def test_provider_call_has_no_open_transaction_and_confirms_atomically(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, app, decision = _seed_reject(db)
    seen = []

    def provider_call(plan):
        assert not db.in_transaction()
        seen.append(plan.provider_target_id)
        assert org.workable_access_token not in repr(plan)
        return {
            "success": True,
            "provider": "workable",
            "provider_remote_stage": "disqualified",
        }

    monkeypatch.setattr(
        "app.services.decision_provider_finalize._build_post_operation",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.decision_provider_post_operation.emit_decision_graph_episode",
        lambda **kwargs: None,
    )
    result = _execute(db, org, user, decision, provider_call)

    assert result["status"] == "ok"
    assert seen == [app.workable_candidate_id]
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    current_decision = db.get(AgentDecision, decision.id)
    receipt = current_app.integration_sync_state["decision_provider_operation"]
    assert current_app.application_outcome == "rejected"
    assert current_app.workable_disqualified is True
    assert current_decision.status == "approved"
    assert receipt["status"] == "confirmed"
    assert org.workable_access_token not in json.dumps(receipt)


def test_ambiguous_result_is_not_retried_and_preserves_local_state(db, monkeypatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, app, decision = _seed_reject(db)
    calls = 0

    def ambiguous(_plan):
        nonlocal calls
        calls += 1
        assert not db.in_transaction()
        raise DecisionProviderFailure(
            code="api_error",
            message="timeout after request",
            provider_called=None,
            retriable=True,
        )

    with pytest.raises(WorkableWritebackError) as error:
        _execute(db, org, user, decision, ambiguous)
    assert error.value.retriable is False
    result = _execute(db, org, user, decision, ambiguous)

    assert result["status"] == "reconciliation_required"
    assert calls == 1
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    current_decision = db.get(AgentDecision, decision.id)
    receipt = current_app.integration_sync_state["decision_provider_operation"]
    assert current_app.application_outcome == "open"
    assert current_decision.status == "processing"
    assert receipt["status"] == "manual_reconciliation_required"
    assert receipt["provider_outcome_uncertain"] is True


def test_confirmed_provider_result_with_local_drift_requires_reconciliation(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, app, decision = _seed_reject(db)

    def provider_call(_plan):
        assert not db.in_transaction()
        with TestingSessionLocal() as concurrent:
            changed = concurrent.get(CandidateApplication, int(app.id))
            changed.version = int(changed.version or 1) + 1
            concurrent.commit()
        return {
            "success": True,
            "provider": "workable",
            "provider_remote_stage": "disqualified",
        }

    result = _execute(db, org, user, decision, provider_call)

    assert result["status"] == "reconciliation_required"
    assert result["reconciliation_reason"] == "application_version_changed"
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    current_decision = db.get(AgentDecision, decision.id)
    receipt = current_app.integration_sync_state["decision_provider_operation"]
    assert current_app.application_outcome == "open"
    assert current_decision.status == "processing"
    assert receipt["provider_succeeded"] is True
    assert receipt["manual_reconciliation_required"] is True


def test_known_not_called_failure_can_rearm_same_exact_operation(db, monkeypatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, _app, decision = _seed_reject(db)
    calls = 0

    def provider_call(_plan):
        nonlocal calls
        calls += 1
        assert not db.in_transaction()
        if calls == 1:
            raise DecisionProviderFailure(
                code="not_configured",
                message="pre-request validation failed",
                provider_called=False,
                retriable=True,
            )
        return {
            "success": True,
            "provider": "workable",
            "provider_remote_stage": "disqualified",
        }

    monkeypatch.setattr(
        "app.services.decision_provider_finalize._build_post_operation",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(WorkableWritebackError) as first:
        _execute(db, org, user, decision, provider_call)
    assert first.value.retriable is True
    result = _execute(db, org, user, decision, provider_call)

    assert result["status"] == "ok"
    assert calls == 2


def test_exact_observation_finishes_ambiguous_provider_decision(db, monkeypatch):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, app, decision = _seed_reject(db)

    def ambiguous(_plan):
        raise DecisionProviderFailure(
            code="api_error",
            message="timeout after request",
            provider_called=None,
            retriable=True,
        )

    with pytest.raises(WorkableWritebackError):
        _execute(db, org, user, decision, ambiguous)
    db.expire_all()
    receipt = db.get(CandidateApplication, app.id).integration_sync_state[
        "decision_provider_operation"
    ]
    identity = DecisionReceiptIdentity(
        operation_id=receipt["operation_id"],
        provider=receipt["provider"],
        provider_target_id=receipt["provider_target_id"],
    )

    def observe(plan):
        assert not db.in_transaction()
        assert plan.provider_target_id == identity.provider_target_id
        return {
            "success": True,
            "provider": "workable",
            "provider_target_id": identity.provider_target_id,
            "provider_remote_stage": "disqualified",
            "provider_remote_stage_values": ["disqualified"],
            "provider_effect_matches": True,
            "evidence": {
                "candidate_id": identity.provider_target_id,
                "disqualified": True,
            },
        }

    observation = check_decision_provider_reconciliation(
        db,
        application_id=int(app.id),
        identity=identity,
        current_user=user,
        observe=observe,
    )
    monkeypatch.setattr(
        "app.services.decision_provider_finalize._build_post_operation",
        lambda *args, **kwargs: None,
    )
    result = resolve_decision_provider_reconciliation(
        db,
        application_id=int(app.id),
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_decision_provider_effect",
        current_user=user,
    )

    assert result["status"] == "ok"
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    current_decision = db.get(AgentDecision, decision.id)
    current_receipt = current_app.integration_sync_state[
        "decision_provider_operation"
    ]
    assert current_app.application_outcome == "rejected"
    assert current_decision.status == "approved"
    assert current_receipt["status"] == "confirmed"
    assert current_receipt["reconciliation_status"] == "resolved"
    assert current_receipt["reconciliation_evidence"][
        "provider_effect_matches"
    ] is True


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
def test_decision_history_fails_before_provider_observation_without_rewrite(
    db, monkeypatch, history_key, history
):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    org, _role, user, app, decision = _seed_reject(db)

    def ambiguous(_plan):
        raise DecisionProviderFailure(
            code="api_error",
            message="timeout after request",
            provider_called=None,
            retriable=True,
        )

    with pytest.raises(WorkableWritebackError):
        _execute(db, org, user, decision, ambiguous)
    db.expire_all()
    current_app = db.get(CandidateApplication, app.id)
    state = dict(current_app.integration_sync_state)
    receipt = dict(state["decision_provider_operation"])
    identity = DecisionReceiptIdentity(
        operation_id=receipt["operation_id"],
        provider=receipt["provider"],
        provider_target_id=receipt["provider_target_id"],
    )
    receipt[history_key] = deepcopy(history)
    state["decision_provider_operation"] = receipt
    current_app.integration_sync_state = state
    db.commit()
    original = deepcopy(receipt)
    provider_calls = []

    with pytest.raises(HTTPException) as caught:
        check_decision_provider_reconciliation(
            db,
            application_id=int(app.id),
            identity=identity,
            current_user=user,
            observe=lambda _plan: provider_calls.append(True),
        )

    assert caught.value.status_code == 409
    assert provider_calls == []
    db.rollback()
    db.refresh(current_app)
    assert current_app.integration_sync_state["decision_provider_operation"] == original
