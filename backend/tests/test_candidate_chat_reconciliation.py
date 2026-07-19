"""Exact, owner-scoped recovery for corrupt or ambiguous candidate chat."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.components.assessments.candidate_chat_reconciliation import (
    CHAT_RECONCILIATION_ARCHIVE_KEY,
    discover_candidate_chat_reconciliation_records,
    public_candidate_chat_reconciliation,
)
from app.components.assessments.candidate_chat_submission import (
    finalize_or_block_candidate_chat_for_submit,
)
from app.components.assessments.chat_idempotency import (
    CHAT_CLAIMS_KEY,
    RequestOutcomeInDoubtError,
    candidate_chat_request_hash,
    claim_candidate_chat_request,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.task import Task
from app.models.user import User
from app.platform.security import get_password_hash
from app.services import (
    candidate_chat_reconciliation as candidate_chat_reconciliation_service,
)
from app.domains.assessments_runtime import candidate_chat_reconciliation_routes
from app.components.assessments import candidate_chat_reconciliation
from tests.conftest import auth_headers, login_user

MEMBER_PASSWORD = "ChatRecoveryMember123!"


def _checkpoint(*, malformed: bool, secret: str = "provider-secret-response"):
    return {
        "version": 1,
        "success": True,
        "stop_reason": "end_turn",
        "content": secret,
        "tool_calls_made": "corrupt" if malformed else [],
        "input_tokens": 10,
        "output_tokens": 7,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "model": "claude-test",
    }


def _finalization_input(request_id: str):
    return {
        "version": 1,
        "message": "Please inspect the failing test",
        "code_context": None,
        "selected_file_path": None,
        "paste_detected": False,
        "browser_focused": True,
        "time_since_last_prompt_ms": 1200,
        "request_id": request_id,
    }


def _claim(
    request_id: str,
    *,
    state: str = "agent_completed",
    malformed_checkpoint: bool = True,
):
    return {
        "request_id": request_id,
        "request_hash": candidate_chat_request_hash(
            message="Please inspect the failing test",
            code_context=None,
            selected_file_path=None,
            paste_detected=False,
            browser_focused=True,
            time_since_last_prompt_ms=1200,
        ),
        "prompt_fingerprint": "prompt-fingerprint",
        "task_fingerprint": "task-fingerprint",
        "role_fingerprint": None,
        "e2b_session_id": "chat-recovery-sandbox",
        "state": state,
        "provider_disposition": "succeeded",
        "created_at": "2026-07-17T01:00:00+00:00",
        "updated_at": "2026-07-17T01:01:00+00:00",
        "chat_turn_checkpoint": _checkpoint(malformed=malformed_checkpoint),
        "finalization_input": _finalization_input(request_id),
    }


def _history_entry(index: int) -> dict:
    hex_index = f"{index:064x}"[-64:]
    request_index = f"{index:032x}"[-32:]
    return {
        "action": "close_without_replay",
        "actor_id": 1,
        "actor_type": "workspace_owner",
        "resolved_at": "2026-07-17T01:02:00+00:00",
        "operation_id": f"chatrec_{hex_index}",
        "request_reference": f"chatreq_{request_index}",
        "issue_code": "provider_checkpoint_malformed",
        "provider_outcome_discarded_attested": True,
        "disposition": "provider_outcome_not_replayed",
        "prior_state": "agent_completed",
        "prior_updated_at": "2026-07-17T01:01:00+00:00",
    }


def _seed(client, db, *, suffix: str, claim_value=None):
    owner_headers, owner_email = auth_headers(
        client,
        organization_name=f"Chat Recovery {suffix}",
    )
    owner = db.query(User).filter(User.email == owner_email).one()
    member = User(
        organization_id=int(owner.organization_id),
        email=f"chat-recovery-member-{suffix}@example.test",
        full_name="Chat recovery member",
        hashed_password=get_password_hash(MEMBER_PASSWORD),
        is_verified=True,
        is_active=True,
        role="member",
    )
    candidate = Candidate(
        organization_id=int(owner.organization_id),
        email=f"chat-recovery-candidate-{suffix}@example.test",
        full_name="Chat recovery candidate",
    )
    task = Task(
        organization_id=int(owner.organization_id),
        name=f"Chat recovery task {suffix}",
        task_key=f"chat-recovery-task-{suffix}",
    )
    db.add_all([member, candidate, task])
    db.flush()
    row = Assessment(
        organization_id=int(owner.organization_id),
        candidate_id=int(candidate.id),
        task_id=int(task.id),
        token=f"chat-recovery-token-{suffix}",
        status=AssessmentStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=28),
        duration_minutes=30,
        e2b_session_id="chat-recovery-sandbox",
        ai_prompts=[],
        prompt_analytics={
            "metric_details": {"word_count_avg": 8},
            CHAT_CLAIMS_KEY: (
                {"request-corrupt": _claim("request-corrupt")}
                if claim_value is None
                else claim_value
            ),
        },
        timeline=[],
        is_voided=False,
    )
    db.add(row)
    db.commit()
    login = login_user(client, member.email, MEMBER_PASSWORD)
    assert login.status_code == 200, login.text
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return row, owner, owner_headers, member_headers


def test_corrupt_checkpoint_is_visible_without_exposing_internal_evidence(
    client,
    db,
):
    row, _owner, owner_headers, member_headers = _seed(
        client, db, suffix="visibility"
    )

    member_detail = client.get(
        f"/api/v1/assessments/{row.id}", headers=member_headers
    )
    owner_detail = client.get(
        f"/api/v1/assessments/{row.id}", headers=owner_headers
    )
    assert member_detail.status_code == 200, member_detail.text
    assert owner_detail.status_code == 200, owner_detail.text
    member_summary = member_detail.json()["candidate_chat_reconciliation"]
    owner_summary = owner_detail.json()["candidate_chat_reconciliation"]
    assert member_summary == {
        "reconciliation_required": True,
        "operation_count": 1,
        "can_reconcile": False,
        "operations": [],
    }
    assert owner_summary["can_reconcile"] is True
    assert owner_summary["operations"][0]["issue_code"] == (
        "provider_checkpoint_malformed"
    )
    assert owner_summary["operations"][0]["state"] == "agent_completed"
    assert CHAT_CLAIMS_KEY not in owner_detail.json()["prompt_analytics"]
    assert "provider-secret-response" not in owner_detail.text
    assert "request-corrupt" not in owner_detail.text

    owner_list = client.get("/api/v1/assessments/", headers=owner_headers)
    assert owner_list.status_code == 200, owner_list.text
    listed_row = next(
        item for item in owner_list.json()["items"] if item["id"] == row.id
    )
    assert listed_row["prompt_analytics"] is None
    assert listed_row["candidate_chat_reconciliation"]["operation_count"] == 1
    assert listed_row["candidate_chat_reconciliation"]["can_reconcile"] is True

    forbidden = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=member_headers,
    )
    assert forbidden.status_code == 403


def test_owner_close_requires_exact_fences_and_preserves_every_claim_field(
    client,
    db,
):
    row, owner, owner_headers, _member_headers = _seed(
        client, db, suffix="resolve"
    )
    db.refresh(row)
    exact_prior = deepcopy(row.prompt_analytics[CHAT_CLAIMS_KEY]["request-corrupt"])
    listed = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    )
    assert listed.status_code == 200, listed.text
    operation = listed.json()["operations"][0]
    url = (
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
        f"{operation['operation_id']}/resolve"
    )

    unattested = client.post(
        url,
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
        },
    )
    assert unattested.status_code == 422
    db.expire_all()
    unchanged = db.get(Assessment, row.id)
    assert unchanged.prompt_analytics[CHAT_CLAIMS_KEY]["request-corrupt"] == exact_prior

    wrong_request = client.post(
        url,
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": f"chatreq_{'0' * 32}",
            "provider_outcome_discarded_attested": True,
        },
    )
    assert wrong_request.status_code == 409

    resolved = client.post(
        url,
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "reconciled_no_replay"
    assert resolved.json()["candidate_chat_reconciliation"][
        "reconciliation_required"
    ] is False

    db.expire_all()
    stored = db.get(Assessment, row.id)
    claim = stored.prompt_analytics[CHAT_CLAIMS_KEY]["request-corrupt"]
    assert claim["state"] == "reconciled_no_replay"
    for key, value in exact_prior.items():
        if key != "state":
            assert claim[key] == value
    history = claim["operator_reconciliation_history"]
    assert history[-1]["prior_state"] == "agent_completed"
    assert history[-1]["operation_id"] == operation["operation_id"]
    assert history[-1]["actor_id"] == int(owner.id)
    assert history[-1]["provider_outcome_discarded_attested"] is True
    assert stored.timeline[-1]["event_type"] == (
        "candidate_chat_reconciled_no_replay_by_owner"
    )

    # Submission is unblocked, but the discarded exact request cannot be
    # replayed. A distinctly keyed request can be claimed normally.
    assert finalize_or_block_candidate_chat_for_submit(
        db,
        assessment_id=int(row.id),
        token=row.token,
    ) is False
    with pytest.raises(RequestOutcomeInDoubtError):
        claim_candidate_chat_request(
            stored.prompt_analytics,
            claim_key="request-corrupt",
            request_id="request-corrupt",
            request_hash=exact_prior["request_hash"],
            prompt_fingerprint="next-prompt",
        )
    _analytics, new_claim = claim_candidate_chat_request(
        stored.prompt_analytics,
        claim_key="new-request",
        request_id="new-request",
        request_hash="new-request-hash",
        prompt_fingerprint="next-prompt",
    )
    assert new_claim["state"] == "claimed"


def test_stale_operation_hash_cannot_close_changed_evidence(client, db):
    row, _owner, owner_headers, _member_headers = _seed(
        client, db, suffix="stale"
    )
    listed = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]
    db.expire_all()
    stored = db.get(Assessment, row.id)
    analytics = deepcopy(stored.prompt_analytics)
    analytics[CHAT_CLAIMS_KEY]["request-corrupt"]["new_evidence"] = "arrived"
    stored.prompt_analytics = analytics
    db.commit()

    stale = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{listed['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": listed["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )
    assert stale.status_code == 409
    db.expire_all()
    unchanged = db.get(Assessment, row.id).prompt_analytics[CHAT_CLAIMS_KEY][
        "request-corrupt"
    ]
    assert unchanged["state"] == "agent_completed"
    assert unchanged["new_evidence"] == "arrived"
    assert "operator_reconciliation_history" not in unchanged


def test_cross_organization_owner_cannot_read_or_resolve_operation(client, db):
    row, _owner, owner_headers, _member_headers = _seed(
        client, db, suffix="org-scope"
    )
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]
    other_headers, _email = auth_headers(
        client,
        organization_name="Other Chat Recovery Org",
    )

    assert client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=other_headers,
    ).status_code == 404
    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=other_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )
    assert response.status_code == 404


def test_valid_completed_checkpoint_is_not_offered_for_manual_discard(client, db):
    valid = {"valid-request": _claim("valid-request", malformed_checkpoint=False)}
    row, _owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix="valid",
        claim_value=valid,
    )

    listed = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["reconciliation_required"] is False
    assert listed.json()["operations"] == []


def test_ambiguous_manual_state_can_be_closed_without_losing_evidence(client, db):
    manual_claim = _claim(
        "manual-request",
        state="manual_reconciliation_required",
    )
    manual_claim["provider_disposition"] = "manual_reconciliation_required"
    claims = {"manual-request": manual_claim}
    row, _owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix="manual",
        claim_value=claims,
    )
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]

    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    stored = db.get(Assessment, row.id).prompt_analytics[CHAT_CLAIMS_KEY][
        "manual-request"
    ]
    assert stored["state"] == "reconciled_no_replay"
    assert stored["provider_disposition"] == "manual_reconciliation_required"
    assert stored["chat_turn_checkpoint"] == manual_claim["chat_turn_checkpoint"]


def test_malformed_claims_container_is_archived_exactly_before_recovery(client, db):
    corrupt_container = [
        {"unreadable": "keep-this-exact-evidence"},
        "second-record",
    ]
    row, _owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix="container",
        claim_value=corrupt_container,
    )
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]
    assert operation["scope"] == "claims_container"
    assert "keep-this-exact-evidence" not in str(operation)

    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    analytics = db.get(Assessment, row.id).prompt_analytics
    assert analytics[CHAT_CLAIMS_KEY] == {}
    archive = analytics[CHAT_RECONCILIATION_ARCHIVE_KEY][-1]
    assert archive["prior_evidence"] == corrupt_container
    assert archive["resolution"]["provider_outcome_discarded_attested"] is True


def test_resolution_rechecks_operation_after_entering_workspace_mutex(
    client,
    db,
    monkeypatch,
):
    row, _owner, owner_headers, _member_headers = _seed(
        client, db, suffix="mutex-recheck"
    )
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]
    mutex_events = []

    @contextmanager
    def mutate_before_resolution(mutex_db, *, assessment_id):
        mutex_events.append(("entered", int(assessment_id)))
        stored = mutex_db.get(Assessment, int(assessment_id))
        analytics = deepcopy(stored.prompt_analytics)
        analytics[CHAT_CLAIMS_KEY]["request-corrupt"]["late_evidence"] = True
        stored.prompt_analytics = analytics
        mutex_db.commit()
        try:
            yield
        finally:
            mutex_events.append(("released", int(assessment_id)))

    monkeypatch.setattr(
        candidate_chat_reconciliation_routes,
        "assessment_workspace_mutex",
        mutate_before_resolution,
    )
    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    assert mutex_events == [("entered", row.id), ("released", row.id)]
    db.expire_all()
    unchanged = db.get(Assessment, row.id).prompt_analytics[CHAT_CLAIMS_KEY][
        "request-corrupt"
    ]
    assert unchanged["state"] == "agent_completed"
    assert unchanged["late_evidence"] is True
    assert "operator_reconciliation_history" not in unchanged


def test_oversized_container_visibility_does_not_iterate_or_copy_evidence():
    class OversizedClaims(dict):
        def __len__(self):
            return 5_001

        def __iter__(self):  # pragma: no cover - failure proves no iteration
            raise AssertionError("oversized claims must not be iterated")

        def items(self):  # pragma: no cover - failure proves no iteration
            raise AssertionError("oversized claims must not be iterated")

        def __deepcopy__(self, memo):  # pragma: no cover - failure proves no copy
            raise AssertionError("oversized claims must not be copied")

    assessment = SimpleNamespace(
        id=77,
        prompt_analytics={CHAT_CLAIMS_KEY: OversizedClaims()},
    )

    summary = public_candidate_chat_reconciliation(
        assessment,
        can_reconcile=True,
    )

    assert summary["operation_count"] == 1
    assert summary["operations"][0]["issue_code"] == "claims_container_oversized"
    assert summary["operations"][0]["can_close_without_replay"] is False


def test_actionable_discovery_hashes_evidence_once_and_retains_no_raw_copy(
    monkeypatch,
):
    calls = []
    original_hash = candidate_chat_reconciliation._stable_hash

    def observed_hash(value):
        calls.append(value)
        return original_hash(value)

    monkeypatch.setattr(candidate_chat_reconciliation, "_stable_hash", observed_hash)
    assessment = SimpleNamespace(
        id=78,
        prompt_analytics={
            CHAT_CLAIMS_KEY: {"request-corrupt": _claim("request-corrupt")}
        },
    )

    records = discover_candidate_chat_reconciliation_records(assessment)

    assert len(records) == 1
    assert len(calls) == 1
    assert not hasattr(records[0], "raw_claim")
    assert records[0].operation_id.startswith("chatrec_")
    assert records[0].request_reference.startswith("chatreq_")


@pytest.mark.parametrize(
    "field, corrupt_value, expected_issue",
    [
        ("role_fingerprint", {"not": "a fingerprint"}, "claim_identity_malformed"),
        (
            "latency_ms",
            {"not": "an integer"},
            "claim_finalization_state_malformed",
        ),
    ],
)
def test_unrestorable_claim_identity_or_finalization_state_is_visible(
    field,
    corrupt_value,
    expected_issue,
):
    claim = _claim("request-corrupt", malformed_checkpoint=False)
    claim[field] = corrupt_value
    assessment = SimpleNamespace(
        id=79,
        prompt_analytics={CHAT_CLAIMS_KEY: {"request-corrupt": claim}},
    )

    records = discover_candidate_chat_reconciliation_records(assessment)

    assert len(records) == 1
    assert records[0].issue_code == expected_issue
    assert records[0].public["can_close_without_replay"] is True


def test_excessive_depth_or_non_string_json_keys_never_reach_hashing(monkeypatch):
    hashed_values = []
    original_hash = candidate_chat_reconciliation._stable_hash

    def observed_hash(value):
        hashed_values.append(value)
        return original_hash(value)

    monkeypatch.setattr(candidate_chat_reconciliation, "_stable_hash", observed_hash)
    deeply_nested = {}
    cursor = deeply_nested
    for _index in range(70):
        cursor["next"] = {}
        cursor = cursor["next"]
    deep_claim = _claim("deep", malformed_checkpoint=False)
    deep_claim["unexpected_nested_evidence"] = deeply_nested
    key_claim = _claim("key", malformed_checkpoint=False)
    key_claim["unexpected_nested_evidence"] = {1: "non-string-key"}

    for assessment_id, claim in ((80, deep_claim), (81, key_claim)):
        records = discover_candidate_chat_reconciliation_records(
            SimpleNamespace(
                id=assessment_id,
                prompt_analytics={CHAT_CLAIMS_KEY: {"request-corrupt": claim}},
            )
        )
        assert len(records) == 1
        assert records[0].issue_code == "claim_record_oversized"
        assert records[0].public["can_close_without_replay"] is False
    assert hashed_values
    assert all("claim" not in value for value in hashed_values)


@pytest.mark.parametrize(
    "stored_history, expected_detail",
    [
        ([{"unexpected": "unsafe"}], "history is malformed"),
        ([_history_entry(index) for index in range(99)], "byte limit"),
    ],
)
def test_malformed_or_oversized_operator_history_is_never_appended(
    client,
    db,
    stored_history,
    expected_detail,
):
    row, _owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix=f"history-{len(stored_history)}",
    )
    db.refresh(row)
    analytics = deepcopy(row.prompt_analytics)
    analytics[CHAT_CLAIMS_KEY]["request-corrupt"][
        "operator_reconciliation_history"
    ] = stored_history
    row.prompt_analytics = analytics
    db.commit()
    exact_prior = deepcopy(analytics[CHAT_CLAIMS_KEY]["request-corrupt"])
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]

    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    assert expected_detail in response.json()["detail"]
    db.expire_all()
    unchanged = db.get(Assessment, row.id).prompt_analytics[CHAT_CLAIMS_KEY][
        "request-corrupt"
    ]
    assert unchanged == exact_prior


def test_history_count_limit_is_checked_before_entries_are_scanned(
    client,
    db,
    monkeypatch,
):
    row, _owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix="history-count-order",
    )
    db.refresh(row)
    analytics = deepcopy(row.prompt_analytics)
    analytics[CHAT_CLAIMS_KEY]["request-corrupt"][
        "operator_reconciliation_history"
    ] = [_history_entry(index) for index in range(100)]
    row.prompt_analytics = analytics
    db.commit()
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]

    def unexpected_entry_scan(_value):  # pragma: no cover - proves ordering
        raise AssertionError("over-limit history entries must not be scanned")

    monkeypatch.setattr(
        candidate_chat_reconciliation_service,
        "_valid_operator_history_entry",
        unexpected_entry_scan,
    )
    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    assert "history reached its safety limit" in response.json()["detail"]


def test_operation_id_path_is_tightly_validated_before_mutation(client, db):
    row, _owner, owner_headers, _member_headers = _seed(
        client, db, suffix="operation-validation"
    )
    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            "chatrec_short/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": f"chatreq_{'0' * 32}",
            "provider_outcome_discarded_attested": True,
        },
    )

    assert response.status_code == 422
    db.expire_all()
    claim = db.get(Assessment, row.id).prompt_analytics[CHAT_CLAIMS_KEY][
        "request-corrupt"
    ]
    assert claim["state"] == "agent_completed"
    assert "operator_reconciliation_history" not in claim


def test_malformed_archive_scope_is_rejected_without_changing_evidence(client, db):
    corrupt_container = ["retain-this-container"]
    row, owner, owner_headers, _member_headers = _seed(
        client,
        db,
        suffix="archive-schema",
        claim_value=corrupt_container,
    )
    operation = client.get(
        f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations",
        headers=owner_headers,
    ).json()["operations"][0]
    db.expire_all()
    stored = db.get(Assessment, row.id)
    analytics = deepcopy(stored.prompt_analytics)
    analytics[CHAT_RECONCILIATION_ARCHIVE_KEY] = [
        {
            "scope": "request",
            # A request archive must identify its exact claim key. Keeping this
            # malformed prior entry proves strict append-only schema checking.
            "claim_key": None,
            "prior_evidence": {"retain": "existing-archive-evidence"},
            "resolution": {
                "action": "close_without_replay",
                "actor_id": int(owner.id),
                "actor_type": "workspace_owner",
                "resolved_at": "2026-07-17T01:02:00+00:00",
                "operation_id": operation["operation_id"],
                "request_reference": operation["request_reference"],
                "issue_code": "claims_container_malformed",
                "provider_outcome_discarded_attested": True,
                "disposition": "provider_outcome_not_replayed",
            },
        }
    ]
    stored.prompt_analytics = analytics
    db.commit()
    exact_prior = deepcopy(analytics)

    response = client.post(
        (
            f"/api/v1/assessments/{row.id}/candidate-chat-reconciliations/"
            f"{operation['operation_id']}/resolve"
        ),
        headers=owner_headers,
        json={
            "action": "close_without_replay",
            "expected_request_reference": operation["request_reference"],
            "provider_outcome_discarded_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    assert "history is malformed" in response.json()["detail"]
    db.expire_all()
    assert db.get(Assessment, row.id).prompt_analytics == exact_prior
