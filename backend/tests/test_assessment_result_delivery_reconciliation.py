"""Owner-attested Workable result recovery and recruiter-safe visibility."""

from __future__ import annotations

import json
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.components.assessments.result_delivery_contracts import (
    DELIVERY_CANCELLED,
    DELIVERY_PENDING,
    DELIVERY_PROVIDER_STARTED,
    DELIVERY_RECONCILIATION_REQUIRED,
    write_receipt,
)
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.organization import Organization
from app.models.task import Task
from app.models.user import User
from app.platform.security import get_password_hash
from app.platform.config import settings
from app.domains.assessments_runtime import assessment_archival
from app.services import assessment_result_delivery_reconciliation as reconciliation
from app.services.assessment_result_workable_delivery import (
    authorize_assessment_result_delivery,
)
from tests.conftest import auth_headers, login_user

MEMBER_PASSWORD = "ResultMemberPass123!"


def _seed_reconciliation(client, db, monkeypatch, *, suffix: str):
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)
    owner_headers, owner_email = auth_headers(client)
    owner = db.query(User).filter(User.email == owner_email).one()
    org = owner.organization
    org.workable_connected = True
    org.workable_access_token = f"never-serialize-{suffix}"
    org.workable_subdomain = f"result-{suffix}"
    org.workable_config = {
        "workable_writeback": True,
        "workable_actor_member_id": "member-result-owner",
    }
    member = User(
        organization_id=int(org.id),
        email=f"result-member-{suffix}@example.test",
        full_name="Result member",
        hashed_password=get_password_hash(MEMBER_PASSWORD),
        is_verified=True,
        is_active=True,
        role="member",
    )
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"result-candidate-{suffix}@example.test",
        full_name="Result candidate",
    )
    task = Task(
        organization_id=int(org.id),
        name=f"Result task {suffix}",
        task_key=f"result-task-{suffix}",
    )
    db.add_all([member, candidate, task])
    db.flush()
    row = Assessment(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        task_id=int(task.id),
        token=f"result-assessment-{suffix}",
        status=AssessmentStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc),
        duration_minutes=30,
        total_duration_seconds=125,
        score=8.0,
        tests_passed=4,
        tests_total=5,
        workable_candidate_id=f"workable-candidate-{suffix}",
        posted_to_workable=False,
        is_voided=False,
    )
    db.add(row)
    db.commit()
    dispatch = authorize_assessment_result_delivery(
        db,
        assessment_id=int(row.id),
        organization_id=int(org.id),
    )
    assert dispatch is not None
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt.update(
        provider_called=True,
        provider_outcome_uncertain=True,
        last_error_code="workable_network_error",
    )
    write_receipt(
        row,
        receipt,
        status=DELIVERY_RECONCILIATION_REQUIRED,
    )
    db.commit()
    login = login_user(client, member.email, MEMBER_PASSWORD)
    assert login.status_code == 200, login.text
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return row, owner_headers, member_headers, dispatch.operation_id


def _history_entry(receipt, *, actor_id: int = 1):
    archived = deepcopy(receipt)
    archived.pop("reconciliation_history", None)
    return {
        "receipt": archived,
        "resolution": {
            "action": "retry_after_provider_absence",
            "actor_id": int(actor_id),
            "actor_type": "workspace_owner",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "provider_result_present_attested": False,
            "provider_result_absent_attested": True,
            "prior_status": str(archived["status"]),
            "prior_operation_id": str(archived["operation_id"]),
        },
    }


def _encoded_size(value) -> int:
    return len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def test_result_status_is_recruiter_visible_but_mutation_is_owner_only(
    client, db, monkeypatch,
):
    row, owner_headers, member_headers, _operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="visibility"
    )

    assert client.get(f"/api/v1/assessments/{row.id}").status_code == 401
    member_read = client.get(
        f"/api/v1/assessments/{row.id}", headers=member_headers
    )
    owner_read = client.get(
        f"/api/v1/assessments/{row.id}", headers=owner_headers
    )

    assert member_read.status_code == 200, member_read.text
    assert owner_read.status_code == 200, owner_read.text
    member_evidence = member_read.json()["workable_result_delivery"]
    owner_evidence = owner_read.json()["workable_result_delivery"]
    assert member_evidence["status"] == DELIVERY_RECONCILIATION_REQUIRED
    assert member_evidence["reconciliation_required"] is True
    assert member_evidence["can_reconcile"] is False
    assert owner_evidence["can_reconcile"] is True
    serialized = json.dumps(member_evidence, sort_keys=True)
    assert "intent" not in member_evidence
    assert "candidate-visibility" not in serialized
    assert "member-result-owner" not in serialized
    assert "never-serialize-visibility" not in member_read.text

    forbidden = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=member_headers,
        json={
            "action": "confirm_delivered",
            "expected_operation_id": _operation_id,
            "provider_result_present_attested": True,
        },
    )
    assert forbidden.status_code == 403


def test_retry_requires_absence_attestation_and_preserves_full_prior_receipt(
    client, db, monkeypatch,
):
    row, owner_headers, _member_headers, old_operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="retry"
    )
    db.refresh(row)
    exact_prior = dict(row.workable_result_delivery_receipt)
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    blocked = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": old_operation_id,
        },
    )

    assert blocked.status_code == 422, blocked.text
    assert publish_calls == []
    db.expire_all()
    unchanged = db.get(Assessment, row.id)
    assert unchanged.workable_result_delivery_receipt["operation_id"] == (
        old_operation_id
    )

    retried = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": old_operation_id,
            "provider_result_absent_attested": True,
        },
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["dispatch_status"] == "published"
    assert len(publish_calls) == 1
    assert publish_calls[0].operation_id != old_operation_id
    db.expire_all()
    stored = db.get(Assessment, row.id)
    replacement = stored.workable_result_delivery_receipt
    assert replacement["operation_id"] != old_operation_id
    archived = replacement["reconciliation_history"][-1]
    assert archived["receipt"] == exact_prior
    assert archived["resolution"]["provider_result_absent_attested"] is True
    assert replacement["provider_called"] is False
    assert stored.posted_to_workable is False


def test_owner_archive_cancels_only_unstarted_delivery_and_retains_evidence(
    client, db, monkeypatch,
):
    row, owner_headers, member_headers, operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="archive-pending"
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt.update(
        provider_called=False,
        provider_succeeded=False,
        provider_outcome_uncertain=False,
    )
    write_receipt(row, receipt, status=DELIVERY_PENDING)
    db.commit()
    db.refresh(row)
    prior_receipt = json.loads(json.dumps(row.workable_result_delivery_receipt))
    prior_timeline = json.loads(json.dumps(row.timeline or []))
    mutex_events = []

    @contextmanager
    def observed_mutex(mutex_db, *, assessment_id):
        mutex_events.append(("entered", int(assessment_id)))
        yield
        mutex_db.expire_all()
        assert mutex_db.get(Assessment, assessment_id).is_voided is True
        mutex_events.append(("released", int(assessment_id)))

    monkeypatch.setattr(
        assessment_archival,
        "assessment_workspace_mutex",
        observed_mutex,
    )

    archived = client.delete(
        f"/api/v1/assessments/{row.id}", headers=member_headers
    )
    assert archived.status_code == 204, archived.text
    assert mutex_events == [("entered", row.id), ("released", row.id)]
    db.expire_all()
    stored = db.get(Assessment, row.id)
    assert stored is not None
    assert stored.is_voided is True
    assert stored.voided_at is not None
    assert stored.void_reason == "archived_by_recruiter"
    assert stored.workable_result_delivery_status == DELIVERY_CANCELLED
    retained = dict(stored.workable_result_delivery_receipt)
    assert {
        key: value
        for key, value in retained.items()
        if key not in {"status", "updated_at"}
    } == {
        key: value
        for key, value in prior_receipt.items()
        if key not in {"status", "updated_at"}
    }
    assert (stored.timeline or [])[: len(prior_timeline)] == prior_timeline
    assert (stored.timeline or [])[-1]["event_type"] == "assessment_archived"
    assert (stored.timeline or [])[-1]["actor_type"] == "workspace_member"

    assert client.get(
        f"/api/v1/assessments/{row.id}", headers=owner_headers
    ).status_code == 404
    listed = client.get("/api/v1/assessments/", headers=owner_headers)
    assert listed.status_code == 200, listed.text
    assert row.id not in {item["id"] for item in listed.json()["items"]}

    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )
    archived_receipt = json.loads(json.dumps(stored.workable_result_delivery_receipt))
    blocked = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": operation_id,
            "provider_result_absent_attested": True,
        },
    )
    assert blocked.status_code == 404, blocked.text
    assert publish_calls == []
    db.expire_all()
    assert db.get(Assessment, row.id).workable_result_delivery_receipt == (
        archived_receipt
    )


def test_owner_archive_preserves_provider_started_receipt_without_relabelling(
    client, db, monkeypatch,
):
    row, owner_headers, _member_headers, _operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="archive-provider-started"
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt.update(
        provider_called=True,
        provider_succeeded=False,
        provider_outcome_uncertain=False,
    )
    write_receipt(
        row,
        receipt,
        status=DELIVERY_PROVIDER_STARTED,
        claimed_at=datetime.now(timezone.utc),
    )
    db.commit()
    db.refresh(row)
    exact_receipt = json.loads(json.dumps(row.workable_result_delivery_receipt))
    prior_timeline = json.loads(json.dumps(row.timeline or []))

    other_org = Organization(
        name="Archive isolation",
        slug="archive-provider-started-isolation",
    )
    db.add(other_org)
    db.flush()
    other_member = User(
        organization_id=int(other_org.id),
        email="archive-isolation@example.test",
        full_name="Other organization member",
        hashed_password=get_password_hash(MEMBER_PASSWORD),
        is_verified=True,
        is_active=True,
        role="member",
    )
    db.add(other_member)
    db.commit()
    other_login = login_user(client, other_member.email, MEMBER_PASSWORD)
    assert other_login.status_code == 200, other_login.text
    other_headers = {
        "Authorization": f"Bearer {other_login.json()['access_token']}"
    }

    isolated = client.delete(
        f"/api/v1/assessments/{row.id}", headers=other_headers
    )
    assert isolated.status_code == 404, isolated.text
    db.expire_all()
    assert db.get(Assessment, row.id).is_voided is False

    archived = client.delete(
        f"/api/v1/assessments/{row.id}", headers=owner_headers
    )

    assert archived.status_code == 204, archived.text
    db.expire_all()
    stored = db.get(Assessment, row.id)
    assert stored.is_voided is True
    assert stored.workable_result_delivery_status == DELIVERY_PROVIDER_STARTED
    assert stored.workable_result_delivery_receipt == exact_receipt
    assert (stored.timeline or [])[: len(prior_timeline)] == prior_timeline
    assert (stored.timeline or [])[-1]["event_type"] == "assessment_archived"


def test_confirm_delivered_never_sends_and_records_owner_attestation(
    client, db, monkeypatch,
):
    row, owner_headers, _member_headers, operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="confirm"
    )
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda _dispatch: (_ for _ in ()).throw(
            AssertionError("manual confirmation must never send")
        ),
    )

    response = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "confirm_delivered",
            "expected_operation_id": operation_id,
            "provider_result_present_attested": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"
    assert response.json()["dispatch_status"] == "not_sent"
    db.expire_all()
    stored = db.get(Assessment, row.id)
    assert stored.posted_to_workable is True
    assert stored.workable_result_delivery_status == "confirmed"
    resolution = stored.workable_result_delivery_receipt["manual_resolution"]
    assert resolution["provider_result_present_attested"] is True
    assert any(
        event.get("event_type")
        == "workable_result_delivery_manually_reconciled"
        for event in stored.timeline
    )


def test_malformed_history_blocks_retry_without_overwriting_evidence(
    client, db, monkeypatch,
):
    row, owner_headers, _member_headers, old_operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="history"
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["reconciliation_history"] = {"malformed": True}
    row.workable_result_delivery_receipt = receipt
    db.commit()

    response = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": old_operation_id,
            "provider_result_absent_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    db.expire_all()
    stored = db.get(Assessment, row.id)
    assert stored.workable_result_delivery_receipt == receipt
    assert stored.workable_result_delivery_receipt["operation_id"] == old_operation_id


def test_stale_owner_attestation_cannot_mutate_a_replacement_operation(
    client, db, monkeypatch,
):
    row, owner_headers, _member_headers, stale_operation_id = _seed_reconciliation(
        client, db, monkeypatch, suffix="stale-operation"
    )
    db.refresh(row)
    replacement = dict(row.workable_result_delivery_receipt)
    replacement["operation_id"] = "replacement-operation-id"
    row.workable_result_delivery_receipt = replacement
    db.commit()
    exact_current = json.loads(json.dumps(replacement))
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    response = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": stale_operation_id,
            "provider_result_absent_attested": True,
        },
    )

    assert response.status_code == 409, response.text
    assert "operation changed" in response.json()["detail"].lower()
    assert publish_calls == []
    db.expire_all()
    stored = db.get(Assessment, row.id)
    assert stored.workable_result_delivery_receipt == exact_current
    assert stored.workable_result_delivery_status == DELIVERY_RECONCILIATION_REQUIRED
    assert stored.posted_to_workable is False


def test_history_99_to_100_succeeds_but_100_to_101_fails_unchanged(
    client,
    db,
    monkeypatch,
):
    row, owner_headers, _member_headers, operation_id = _seed_reconciliation(
        client,
        db,
        monkeypatch,
        suffix="history-count-boundary",
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    entry = _history_entry(receipt)
    receipt["reconciliation_history"] = [deepcopy(entry) for _ in range(99)]
    row.workable_result_delivery_receipt = receipt
    db.commit()
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda _dispatch: "published",
    )

    success = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": operation_id,
            "provider_result_absent_attested": True,
        },
    )
    assert success.status_code == 200, success.text
    db.expire_all()
    replacement = db.get(Assessment, row.id)
    assert len(
        replacement.workable_result_delivery_receipt["reconciliation_history"]
    ) == 100

    replacement_receipt = dict(replacement.workable_result_delivery_receipt)
    replacement_operation_id = replacement_receipt["operation_id"]
    replacement_receipt.update(
        provider_called=True,
        provider_outcome_uncertain=True,
    )
    write_receipt(
        replacement,
        replacement_receipt,
        status=DELIVERY_RECONCILIATION_REQUIRED,
    )
    db.commit()
    exact_receipt = deepcopy(replacement.workable_result_delivery_receipt)
    exact_timeline = deepcopy(replacement.timeline)
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    blocked = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": replacement_operation_id,
            "provider_result_absent_attested": True,
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert publish_calls == []
    db.expire_all()
    unchanged = db.get(Assessment, row.id)
    assert unchanged.workable_result_delivery_receipt == exact_receipt
    assert unchanged.timeline == exact_timeline
    assert unchanged.workable_result_delivery_status == (
        DELIVERY_RECONCILIATION_REQUIRED
    )


def _history_at_exact_byte_limit(receipt):
    history = [_history_entry(receipt) for _ in range(5)]
    for entry in history:
        entry["receipt"]["padding"] = ""
    remaining = reconciliation._MAX_HISTORY_BYTES - _encoded_size(history)
    assert remaining > 0
    for entry in history:
        addition = min(remaining, 110_000)
        entry["receipt"]["padding"] = "x" * addition
        remaining -= addition
    assert remaining == 0
    assert _encoded_size(history) == reconciliation._MAX_HISTORY_BYTES
    assert all(
        _encoded_size(entry["receipt"])
        <= reconciliation._MAX_ARCHIVED_RECEIPT_BYTES
        for entry in history
    )
    return history


def test_exact_history_byte_limit_is_valid_and_one_byte_over_is_unchanged(
    client,
    db,
    monkeypatch,
):
    row, owner_headers, _member_headers, operation_id = _seed_reconciliation(
        client,
        db,
        monkeypatch,
        suffix="history-byte-boundary",
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["reconciliation_history"] = _history_at_exact_byte_limit(receipt)
    row.workable_result_delivery_receipt = receipt
    db.commit()

    confirmed = client.post(
        f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
        headers=owner_headers,
        json={
            "action": "confirm_delivered",
            "expected_operation_id": operation_id,
            "provider_result_present_attested": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    row2, owner_headers2, _member_headers2, operation_id2 = _seed_reconciliation(
        client,
        db,
        monkeypatch,
        suffix="history-byte-over",
    )
    db.refresh(row2)
    receipt2 = dict(row2.workable_result_delivery_receipt)
    history2 = _history_at_exact_byte_limit(receipt2)
    history2[0]["receipt"]["padding"] += "x"
    receipt2["reconciliation_history"] = history2
    row2.workable_result_delivery_receipt = receipt2
    db.commit()
    exact_receipt = deepcopy(receipt2)
    exact_timeline = deepcopy(row2.timeline)
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    blocked = client.post(
        f"/api/v1/assessments/{row2.id}/workable-result-delivery/reconcile",
        headers=owner_headers2,
        json={
            "action": "retry_after_provider_absence",
            "expected_operation_id": operation_id2,
            "provider_result_absent_attested": True,
        },
    )
    assert blocked.status_code == 409, blocked.text
    assert publish_calls == []
    db.expire_all()
    unchanged = db.get(Assessment, row2.id)
    assert unchanged.workable_result_delivery_receipt == exact_receipt
    assert unchanged.timeline == exact_timeline


@pytest.mark.parametrize("hazard", ("deep", "wide", "secret", "schema"))
def test_malformed_bounded_history_blocks_both_actions_without_mutation_or_publish(
    client,
    db,
    monkeypatch,
    hazard,
):
    row, owner_headers, _member_headers, operation_id = _seed_reconciliation(
        client,
        db,
        monkeypatch,
        suffix=f"history-hazard-{hazard}",
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    entry = _history_entry(receipt)
    if hazard == "deep":
        nested = {}
        cursor = nested
        for _ in range(reconciliation._MAX_JSON_DEPTH + 2):
            cursor["next"] = {}
            cursor = cursor["next"]
        entry["receipt"]["extra"] = nested
    elif hazard == "wide":
        entry["receipt"]["extra"] = [0] * (reconciliation._MAX_JSON_NODES + 1)
    elif hazard == "secret":
        entry["receipt"]["provider_api_key"] = "must-never-survive"
    else:
        entry["unexpected"] = True
    receipt["reconciliation_history"] = [entry]
    row.workable_result_delivery_receipt = receipt
    db.commit()
    exact_receipt = deepcopy(receipt)
    exact_timeline = deepcopy(row.timeline)
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    for action, flag in (
        ("confirm_delivered", {"provider_result_present_attested": True}),
        (
            "retry_after_provider_absence",
            {"provider_result_absent_attested": True},
        ),
    ):
        response = client.post(
            f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
            headers=owner_headers,
            json={
                "action": action,
                "expected_operation_id": operation_id,
                **flag,
            },
        )
        assert response.status_code == 409, response.text
        assert "must-never-survive" not in response.text
    assert publish_calls == []
    db.expire_all()
    unchanged = db.get(Assessment, row.id)
    assert unchanged.workable_result_delivery_receipt == exact_receipt
    assert unchanged.timeline == exact_timeline
    assert unchanged.workable_result_delivery_status == (
        DELIVERY_RECONCILIATION_REQUIRED
    )


def test_oversized_current_receipt_is_rejected_before_archival_copy_and_publish(
    client,
    db,
    monkeypatch,
):
    row, owner_headers, _member_headers, operation_id = _seed_reconciliation(
        client,
        db,
        monkeypatch,
        suffix="oversized-current-receipt",
    )
    db.refresh(row)
    receipt = dict(row.workable_result_delivery_receipt)
    receipt["padding"] = "x" * reconciliation._MAX_ARCHIVED_RECEIPT_BYTES
    row.workable_result_delivery_receipt = receipt
    db.commit()
    exact_timeline = deepcopy(row.timeline)
    publish_calls = []
    monkeypatch.setattr(
        reconciliation,
        "publish_assessment_result_delivery",
        lambda dispatch: publish_calls.append(dispatch) or "published",
    )

    for action, attestation in (
        ("confirm_delivered", {"provider_result_present_attested": True}),
        (
            "retry_after_provider_absence",
            {"provider_result_absent_attested": True},
        ),
    ):
        response = client.post(
            f"/api/v1/assessments/{row.id}/workable-result-delivery/reconcile",
            headers=owner_headers,
            json={
                "action": action,
                "expected_operation_id": operation_id,
                **attestation,
            },
        )
        assert response.status_code == 409, response.text
    assert publish_calls == []
    db.expire_all()
    unchanged = db.get(Assessment, row.id)
    assert unchanged.workable_result_delivery_receipt == receipt
    assert unchanged.timeline == exact_timeline
