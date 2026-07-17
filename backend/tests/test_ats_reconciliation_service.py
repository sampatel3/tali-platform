from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.job_hiring_team import JobHiringTeam, TEAM_ROLE_RECRUITER
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.application_lifecycle_restore import receipt_blocks_lifecycle_restore
from app.services.ats_reconciliation_service import (
    ReceiptIdentity,
    ReceiptSnapshot,
    check_ats_reconciliation,
    resolve_ats_reconciliation,
)
from app.services.ats_reconciliation_evidence import (
    has_exact_reconciliation_resolution,
)
from app.services.reconciliation_history import (
    MAX_RECONCILIATION_HISTORY_BYTES,
    MAX_RECONCILIATION_HISTORY_ENTRIES,
    RECONCILIATION_HISTORY_SATURATION_KEY,
)
from tests.conftest import TestingSessionLocal


RECEIPT_KEYS = (
    "auto_reject_operation",
    "cv_gap_rejection_operation",
    "outcome_writeback",
    "outcome_writeback_reconciliation",
)


def _seed(db, *, receipt_key="auto_reject_operation", outcome="open"):
    suffix = uuid4().hex
    org = Organization(name=f"Recon {suffix}", slug=f"recon-{suffix}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Engineer")
    candidate = Candidate(
        organization_id=org.id,
        email=f"recon-{suffix}@example.test",
        full_name="Reconciliation Candidate",
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
    operation_id = f"{receipt_key}:{suffix}"
    target = f"workable-{suffix}"
    original = {
        "application_id": None,
        "operation_id": operation_id,
        "provider": "workable",
        "provider_target_id": target,
        "status": "manual_reconciliation_required",
        "target_outcome": "rejected",
        "provider_call_started_at": "2026-07-16T10:00:00+00:00",
        "provider_called": None,
        "provider_succeeded": None,
        "provider_outcome_uncertain": True,
        "manual_reconciliation_required": True,
    }
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied" if outcome == "open" else outcome,
        pipeline_stage="review",
        application_outcome=outcome,
        version=3,
        source="workable",
        workable_candidate_id=target,
        integration_sync_state={receipt_key: original},
    )
    db.add(app)
    db.flush()
    original["application_id"] = int(app.id)
    app.integration_sync_state = {receipt_key: dict(original)}
    db.commit()
    return org, role, owner, app, ReceiptIdentity(
        receipt_key=receipt_key,
        operation_id=operation_id,
        provider="workable",
        provider_target_id=target,
    )


def _provider_outcome(outcome):
    def _lookup(db, snapshot):
        assert not db.in_transaction(), "provider lookup must run after lock commit"
        assert snapshot.identity.provider_target_id.startswith("workable-")
        return {
            "remote_outcome": outcome,
            "remote_status": "disqualified" if outcome == "rejected" else "applied",
            "evidence": {"candidate_id": snapshot.identity.provider_target_id},
        }

    return _lookup


def test_default_provider_lookup_resolves_the_canonical_boundary_at_execution(
    db, monkeypatch
):
    _org, _role, owner, app, identity = _seed(db)
    monkeypatch.setattr(
        "app.services.ats_reconciliation_provider.read_provider_observation",
        _provider_outcome("open"),
    )

    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
    )

    assert observation["remote_outcome"] == "open"


@pytest.mark.parametrize("receipt_key", RECEIPT_KEYS)
def test_every_receipt_family_checks_and_resolves_without_erasing_provider_phase(
    db, receipt_key
):
    _org, _role, owner, app, identity = _seed(db, receipt_key=receipt_key)
    original = dict(app.integration_sync_state[receipt_key])

    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )
    evidence = resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_provider_matches_local",
        current_user=owner,
    )

    db.refresh(app)
    receipt = app.integration_sync_state[receipt_key]
    for field in (
        "status",
        "provider_call_started_at",
        "provider_called",
        "provider_succeeded",
        "provider_outcome_uncertain",
        "manual_reconciliation_required",
    ):
        assert receipt[field] == original[field]
    assert receipt["resolved_operation_id"] == identity.operation_id
    assert receipt["reconciliation_evidence"] == evidence
    assert len(receipt["reconciliation_observation_history"]) == 1
    assert len(receipt["reconciliation_resolution_history"]) == 1
    assert not receipt_blocks_lifecycle_restore(receipt, receipt_key=receipt_key)


def test_aligns_local_outcome_only_to_fresh_observed_open_or_rejected(db):
    _org, _role, owner, app, identity = _seed(db, outcome="open")
    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("rejected"),
    )

    evidence = resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="align_local_to_provider",
        current_user=owner,
    )

    db.refresh(app)
    assert app.application_outcome == "rejected"
    assert app.version == 4
    assert evidence["local_outcome_before"] == "open"
    assert evidence["local_outcome_after"] == "rejected"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("operation_id", "wrong-op"),
        ("provider", "bullhorn"),
        ("provider_target_id", "wrong-target"),
    ],
)
def test_exact_operation_and_target_are_mandatory(db, field, replacement):
    _org, _role, owner, app, identity = _seed(db)
    values = identity.__dict__ | {field: replacement}

    with pytest.raises(HTTPException) as exc_info:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=ReceiptIdentity(**values),
            current_user=owner,
            provider_lookup=_provider_outcome("open"),
        )

    assert exc_info.value.status_code == 409


def test_tenant_and_job_permission_fail_closed(db):
    _org, _role, _owner, app, identity = _seed(db)
    outsider_org = Organization(name="Outsider", slug=f"outsider-{uuid4().hex}")
    db.add(outsider_org)
    db.flush()
    outsider = User(
        organization_id=outsider_org.id,
        email=f"outside-{uuid4().hex}@example.test",
        hashed_password="x",
        role="owner",
        is_active=True,
        is_verified=True,
    )
    member = User(
        organization_id=app.organization_id,
        email=f"member-{uuid4().hex}@example.test",
        hashed_password="x",
        role="member",
        is_active=True,
        is_verified=True,
    )
    db.add_all([outsider, member])
    db.commit()

    with pytest.raises(HTTPException) as cross_tenant:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=outsider,
            provider_lookup=_provider_outcome("open"),
        )
    assert cross_tenant.value.status_code == 404
    db.rollback()
    with pytest.raises(HTTPException) as forbidden:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=member,
            provider_lookup=_provider_outcome("open"),
        )
    assert forbidden.value.status_code == 403


def test_related_only_recruiter_uses_exact_shared_roster_authority(db):
    org, owner_role, _owner, app, identity = _seed(db)
    related = Role(
        organization_id=org.id,
        name="Related Engineer",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner_role.id,
    )
    recruiter = User(
        organization_id=org.id,
        email=f"related-{uuid4().hex}@example.test",
        hashed_password="x",
        role="member",
        is_active=True,
        is_verified=True,
    )
    db.add_all([related, recruiter])
    db.flush()
    db.add_all(
        [
            JobHiringTeam(
                organization_id=org.id,
                role_id=related.id,
                user_id=recruiter.id,
                team_role=TEAM_ROLE_RECRUITER,
            ),
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                source_application_id=app.id,
                status="done",
                spec_fingerprint="s" * 64,
                cv_fingerprint="c" * 64,
            ),
        ]
    )
    db.commit()

    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=recruiter,
        acting_role_id=related.id,
        provider_lookup=_provider_outcome("open"),
    )
    assert observation["checked_by_actor_id"] == recruiter.id
    evidence = resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_provider_matches_local",
        current_user=recruiter,
        acting_role_id=related.id,
    )
    assert evidence["disposition"] == "confirm_provider_matches_local"

    with pytest.raises(HTTPException) as owner_role_forbidden:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=recruiter,
            provider_lookup=_provider_outcome("open"),
        )
    assert owner_role_forbidden.value.status_code == 403


def test_provider_phase_change_during_unlocked_lookup_discards_observation(db):
    _org, _role, owner, app, identity = _seed(db)
    app_id = int(app.id)

    def concurrent_lookup(_db, snapshot):
        with TestingSessionLocal() as concurrent:
            current = concurrent.get(CandidateApplication, app_id)
            state = dict(current.integration_sync_state)
            receipt = dict(state[identity.receipt_key])
            receipt["provider_succeeded"] = True
            receipt["provider_succeeded_at"] = datetime.now(timezone.utc).isoformat()
            state[identity.receipt_key] = receipt
            current.integration_sync_state = state
            concurrent.commit()
        return _provider_outcome("open")(_db, snapshot)

    with pytest.raises(HTTPException) as exc_info:
        check_ats_reconciliation(
            db,
            application_id=app_id,
            identity=identity,
            current_user=owner,
            provider_lookup=concurrent_lookup,
        )
    assert exc_info.value.status_code == 409
    db.rollback()
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.event_type == "ats_reconciliation_observed")
        .count()
        == 0
    )


def test_application_change_during_unlocked_lookup_discards_observation(db):
    _org, _role, owner, app, identity = _seed(db)
    app_id = int(app.id)

    def concurrent_lookup(_db, snapshot):
        with TestingSessionLocal() as concurrent:
            current = concurrent.get(CandidateApplication, app_id)
            current.application_outcome = "rejected"
            current.version = int(current.version) + 1
            concurrent.commit()
        return _provider_outcome("open")(_db, snapshot)

    with pytest.raises(HTTPException) as exc_info:
        check_ats_reconciliation(
            db,
            application_id=app_id,
            identity=identity,
            current_user=owner,
            provider_lookup=concurrent_lookup,
        )
    assert exc_info.value.status_code == 409
    db.rollback()
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.event_type == "ats_reconciliation_observed")
        .count()
        == 0
    )


def test_permission_revocation_during_provider_read_discards_observation(db):
    org, role, _owner, app, identity = _seed(db)
    recruiter = User(
        organization_id=org.id,
        email=f"revoked-{uuid4().hex}@example.test",
        hashed_password="x",
        role="member",
        is_active=True,
        is_verified=True,
    )
    db.add(recruiter)
    db.flush()
    membership = JobHiringTeam(
        organization_id=org.id,
        role_id=role.id,
        user_id=recruiter.id,
        team_role=TEAM_ROLE_RECRUITER,
    )
    db.add(membership)
    db.commit()
    membership_id = int(membership.id)
    db.rollback()

    def revoke_during_lookup(_db, snapshot):
        with TestingSessionLocal() as concurrent:
            current = concurrent.get(JobHiringTeam, membership_id)
            current.team_role = "interviewer"
            concurrent.commit()
        return _provider_outcome("open")(_db, snapshot)

    with pytest.raises(HTTPException) as exc_info:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=recruiter,
            provider_lookup=revoke_during_lookup,
        )
    assert exc_info.value.status_code == 403
    db.rollback()
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.event_type == "ats_reconciliation_observed")
        .count()
        == 0
    )


def test_stale_or_superseded_observation_cannot_resolve(db):
    _org, _role, owner, app, identity = _seed(db)
    first = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )
    second = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )

    with pytest.raises(HTTPException) as superseded:
        resolve_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=first["observation_id"],
            disposition="confirm_provider_matches_local",
            current_user=owner,
        )
    assert superseded.value.status_code == 409
    db.rollback()
    with pytest.raises(HTTPException) as stale:
        resolve_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=second["observation_id"],
            disposition="confirm_provider_matches_local",
            current_user=owner,
            now=datetime.now(timezone.utc) + timedelta(minutes=6),
        )
    assert stale.value.status_code == 409


def test_resolving_one_receipt_does_not_bypass_another(db):
    _org, _role, owner, app, identity = _seed(db, outcome="open")
    state = dict(app.integration_sync_state)
    state["cv_gap_rejection_operation"] = {
        "operation_id": "cv-other",
        "provider": "workable",
        "provider_target_id": "other-target",
        "status": "provider_call_started",
        "provider_outcome_uncertain": True,
    }
    app.integration_sync_state = state
    db.commit()
    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )
    resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_provider_matches_local",
        current_user=owner,
    )

    db.refresh(app)
    assert not receipt_blocks_lifecycle_restore(
        app.integration_sync_state[identity.receipt_key],
        receipt_key=identity.receipt_key,
    )
    assert receipt_blocks_lifecycle_restore(
        app.integration_sync_state["cv_gap_rejection_operation"],
        receipt_key="cv_gap_rejection_operation",
    )


def test_align_is_blocked_while_another_receipt_remains_unresolved(db):
    _org, _role, owner, app, identity = _seed(db, outcome="open")
    state = dict(app.integration_sync_state)
    state["cv_gap_rejection_operation"] = {
        "operation_id": "cv-other",
        "provider": "workable",
        "provider_target_id": "other-target",
        "status": "provider_call_started",
        "provider_outcome_uncertain": True,
    }
    app.integration_sync_state = state
    db.commit()
    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("rejected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=observation["observation_id"],
            disposition="align_local_to_provider",
            current_user=owner,
        )
    assert exc_info.value.status_code == 409
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"
    assert app.integration_sync_state[identity.receipt_key].get(
        "reconciliation_status"
    ) != "resolved"


@pytest.mark.parametrize("remote_outcome", ["unknown", "unsupported"])
def test_unknown_or_unsupported_observation_can_never_resolve(db, remote_outcome):
    _org, _role, owner, app, identity = _seed(db)
    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome(remote_outcome),
    )
    assert observation["remote_outcome"] == remote_outcome

    with pytest.raises(HTTPException) as exc_info:
        resolve_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=observation["observation_id"],
            disposition="align_local_to_provider",
            current_user=owner,
        )
    assert exc_info.value.status_code == 409
    assert "open or rejected" in str(exc_info.value.detail)


def test_cross_family_resolution_evidence_is_not_exact_proof():
    copied = {
        "operation_id": "outcome:1",
        "provider": "workable",
        "provider_target_id": "candidate-1",
        "reconciliation_status": "resolved",
        "resolved_operation_id": "outcome:1",
        "resolved_receipt_key": "auto_reject_operation",
        "reconciliation_resolved_by_actor_id": 7,
        "reconciliation_resolved_by_actor_type": "recruiter",
        "reconciliation_disposition": "confirm_provider_matches_local",
        "reconciliation_observation_id": "obs-1",
        "reconciliation_evidence": {
            "receipt_key": "auto_reject_operation",
            "operation_id": "outcome:1",
            "provider": "workable",
            "provider_target_id": "candidate-1",
            "observation_id": "obs-1",
            "remote_outcome": "open",
        },
    }

    assert not has_exact_reconciliation_resolution(
        copied, receipt_key="outcome_writeback"
    )
    copied["manual_reconciliation_required"] = True
    assert receipt_blocks_lifecycle_restore(
        copied, receipt_key="outcome_writeback"
    )


@pytest.mark.parametrize(
    "loose_resolution",
    [
        {"reconciliation_resolved_at": "2026-07-17T00:00:00+00:00"},
        {"provider_reconciled_at": "2026-07-17T00:00:00+00:00"},
        {"reconciliation_status": "resolved"},
    ],
)
def test_bare_legacy_resolution_marker_never_unblocks_restore(loose_resolution):
    receipt = {
        "operation_id": "manual:legacy",
        "provider": "workable",
        "provider_target_id": "candidate-legacy",
        "status": "manual_reconciliation_required",
        "manual_reconciliation_required": True,
        **loose_resolution,
    }

    assert receipt_blocks_lifecycle_restore(
        receipt, receipt_key="outcome_writeback"
    )


def test_observation_and_resolution_events_are_append_only_history(db):
    _org, _role, owner, app, identity = _seed(db)
    first = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )
    second = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("open"),
    )
    resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=second["observation_id"],
        disposition="confirm_provider_matches_local",
        current_user=owner,
    )

    db.refresh(app)
    history = app.integration_sync_state[identity.receipt_key][
        "reconciliation_observation_history"
    ]
    assert [row["observation_id"] for row in history] == [
        first["observation_id"],
        second["observation_id"],
    ]
    events = (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == app.id)
        .filter(
            CandidateApplicationEvent.event_type.in_(
                ["ats_reconciliation_observed", "ats_reconciliation_resolved"]
            )
        )
        .order_by(CandidateApplicationEvent.id)
        .all()
    )
    assert [event.event_type for event in events] == [
        "ats_reconciliation_observed",
        "ats_reconciliation_observed",
        "ats_reconciliation_resolved",
    ]


@pytest.mark.parametrize(
    ("history_key", "history"),
    [
        (
            "reconciliation_observation_history",
            [{"observation_id": f"kept-{index}"} for index in range(100)],
        ),
        (
            "reconciliation_resolution_history",
            [{"observation_id": f"kept-{index}"} for index in range(100)],
        ),
        ("reconciliation_observation_history", [{"kept": True}, "malformed"]),
        ("reconciliation_resolution_history", [{"kept": True}, "malformed"]),
    ],
)
def test_full_or_malformed_history_fails_before_provider_read_without_rewrite(
    db, history_key, history
):
    _org, _role, owner, app, identity = _seed(db)
    state = dict(app.integration_sync_state)
    receipt = dict(state[identity.receipt_key])
    receipt[history_key] = deepcopy(history)
    state[identity.receipt_key] = receipt
    app.integration_sync_state = state
    db.commit()
    original = deepcopy(receipt)
    provider_calls = []

    with pytest.raises(HTTPException) as caught:
        check_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            current_user=owner,
            provider_lookup=lambda *_args: provider_calls.append(True),
        )

    assert caught.value.status_code == 409
    assert provider_calls == []
    db.rollback()
    db.refresh(app)
    assert app.integration_sync_state[identity.receipt_key] == original


def test_oversized_new_evidence_uses_latest_fields_and_events_without_trimming(db):
    _org, _role, owner, app, identity = _seed(db)
    prior = [{"observation_id": "prior", "evidence": {"raw": "preserve"}}]
    state = dict(app.integration_sync_state)
    receipt = dict(state[identity.receipt_key])
    receipt["reconciliation_observation_history"] = deepcopy(prior)
    state[identity.receipt_key] = receipt
    app.integration_sync_state = state
    db.commit()
    raw_evidence = "x" * MAX_RECONCILIATION_HISTORY_BYTES

    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=lambda *_args: {
            "remote_outcome": "open",
            "remote_status": "applied",
            "evidence": {"raw": raw_evidence},
        },
    )
    resolution = resolve_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        observation_id=observation["observation_id"],
        disposition="confirm_provider_matches_local",
        current_user=owner,
    )

    db.refresh(app)
    receipt = app.integration_sync_state[identity.receipt_key]
    assert receipt["reconciliation_observation_history"] == prior
    assert receipt.get("reconciliation_resolution_history", []) == []
    assert receipt["reconciliation_observation"] == observation
    assert receipt["reconciliation_evidence"] == resolution
    assert observation["evidence"]["raw"] == raw_evidence
    assert resolution["provider_evidence"]["raw"] == raw_evidence
    saturation = receipt[RECONCILIATION_HISTORY_SATURATION_KEY]
    assert set(saturation) == {
        "reconciliation_observation_history",
        "reconciliation_resolution_history",
    }
    assert all(
        marker["reason"] == "candidate_exceeds_byte_limit"
        for marker in saturation.values()
    )
    assert all(
        marker["max_entries"] == MAX_RECONCILIATION_HISTORY_ENTRIES
        for marker in saturation.values()
    )
    events = (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == app.id)
        .filter(
            CandidateApplicationEvent.event_type.in_(
                ["ats_reconciliation_observed", "ats_reconciliation_resolved"]
            )
        )
        .order_by(CandidateApplicationEvent.id)
        .all()
    )
    assert [event.event_metadata for event in events] == [observation, resolution]


def test_full_resolution_history_blocks_local_alignment_without_rewrite(db):
    _org, _role, owner, app, identity = _seed(db, outcome="open")
    observation = check_ats_reconciliation(
        db,
        application_id=app.id,
        identity=identity,
        current_user=owner,
        provider_lookup=_provider_outcome("rejected"),
    )
    db.refresh(app)
    state = dict(app.integration_sync_state)
    receipt = dict(state[identity.receipt_key])
    retained = [
        {"observation_id": f"prior-resolution-{index}"}
        for index in range(MAX_RECONCILIATION_HISTORY_ENTRIES)
    ]
    receipt["reconciliation_resolution_history"] = deepcopy(retained)
    state[identity.receipt_key] = receipt
    app.integration_sync_state = state
    db.commit()

    with pytest.raises(HTTPException) as caught:
        resolve_ats_reconciliation(
            db,
            application_id=app.id,
            identity=identity,
            observation_id=observation["observation_id"],
            disposition="align_local_to_provider",
            current_user=owner,
        )

    assert caught.value.status_code == 409
    db.rollback()
    db.refresh(app)
    assert app.application_outcome == "open"
    assert (
        app.integration_sync_state[identity.receipt_key][
            "reconciliation_resolution_history"
        ]
        == retained
    )


@pytest.mark.parametrize(
    ("remote_status", "bullhorn_config", "expected_outcome"),
    [
        ("Submitted", {}, "open"),
        (
            "Placed",
            {"confirmedJobResponseStatus": "Placed"},
            "unsupported",
        ),
    ],
)
def test_dual_connected_bullhorn_receipt_resolves_exact_provider_link(
    db, remote_status, bullhorn_config, expected_outcome
):
    from app.services.ats_reconciliation_provider import read_provider_observation

    org = Organization(
        name="Dual",
        slug=f"dual-{uuid4().hex}",
        workable_connected=True,
        workable_access_token="workable-token",
        workable_subdomain="dual",
        bullhorn_connected=True,
        bullhorn_client_id="client",
        bullhorn_client_secret="secret",
        bullhorn_refresh_token="refresh",
        bullhorn_username="user",
        bullhorn_config=bullhorn_config,
    )
    db.add(org)
    db.commit()
    identity = ReceiptIdentity(
        receipt_key="outcome_writeback",
        operation_id="bullhorn-exact",
        provider="bullhorn",
        provider_target_id="41",
    )
    snapshot = ReceiptSnapshot(
        application_id=1,
        organization_id=org.id,
        application_version=1,
        application_outcome="open",
        identity=identity,
        receipt_fingerprint="fingerprint",
    )

    class Provider:
        ats = "bullhorn"

        @staticmethod
        def get_job_submission_status(submission_id):
            assert not db.in_transaction()
            assert submission_id == "41"
            return {"id": 41, "status": remote_status, "isDeleted": False}

    def exact_resolver(_org, _db, linkage):
        assert linkage.workable_candidate_id is None
        assert linkage.bullhorn_job_submission_id == "41"
        return Provider()

    with (
        patch(
            "app.services.ats_reconciliation_provider.resolve_application_ats_provider",
            side_effect=exact_resolver,
        ),
        patch(
            "app.services.ats_reconciliation_provider.resolve_stage",
            return_value=type("Mapping", (), {"taali_stage": "applied", "is_reject": False})(),
        ),
    ):
        observed = read_provider_observation(db, snapshot)

    assert observed["remote_outcome"] == expected_outcome
    assert observed["evidence"]["job_submission_id"] == "41"


def test_bullhorn_provider_exposes_supported_exact_submission_read():
    from app.components.integrations.bullhorn.provider import BullhornProvider

    class Client:
        @staticmethod
        def get_job_submission(submission_id):
            assert submission_id == "73"
            return {"id": 73, "status": "Submitted"}

    provider = object.__new__(BullhornProvider)
    with patch.object(BullhornProvider, "_client", return_value=Client()):
        assert provider.get_job_submission_status("73") == {
            "id": 73,
            "status": "Submitted",
        }


def test_reconciliation_routes_are_registered():
    from app.main import app as api

    paths = set(api.openapi()["paths"])
    assert "/api/v1/applications/{application_id}/ats-reconciliation/check" in paths
    assert "/api/v1/applications/{application_id}/ats-reconciliation/resolve" in paths
