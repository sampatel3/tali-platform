from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.organization import Organization
from app.models.role import Role
from app.services import (
    scoring_batch_successor_reconcile,
    scoring_batch_successors,
)
from app.services.scoring_batch_fanout_recovery import (
    SCORING_QUEUE_CONTRACT,
    claim_due_scoring_fanouts,
)


def _scope(db, label: str):
    organization = Organization(name=label, slug=f"{label}-{id(db)}")
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=organization.id,
        name=label,
        source="manual",
        job_spec_text="Validate durable recovery metadata.",
    )
    db.add(role)
    db.flush()
    return organization, role


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        (
            {"fanout_dispatch_next_attempt_at": "not-a-date"},
            "invalid_fanout_dispatch_next_attempt_at",
        ),
        (
            {
                "fanout_lease_expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            },
            "invalid_future_fanout_lease_expires_at",
        ),
    ],
)
def test_fanout_recovery_quarantines_unrecoverable_time_metadata(
    db, updates, reason
) -> None:
    organization, role = _scope(db, f"fanout-{reason}")
    counters = {
        "queue_contract": SCORING_QUEUE_CONTRACT,
        "target_application_ids": [123],
        "include_scored": False,
        "applied_after": None,
        "fanout_complete": False,
        **updates,
    }
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="queued",
        counters=counters,
    )
    db.add(run)
    db.commit()

    scanned, payloads = claim_due_scoring_fanouts(db, limit=5)
    db.commit()
    db.refresh(run)

    assert scanned == 1
    assert payloads == []
    assert run.status == "failed"
    assert run.counters["fanout_quarantine_reason"] == reason


def test_fanout_recovery_ignores_corrupt_filter_after_exact_snapshot(db) -> None:
    organization, role = _scope(db, "fanout-exact-filter")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="queued",
        counters={
            "queue_contract": SCORING_QUEUE_CONTRACT,
            "target_application_ids": [123],
            "include_scored": False,
            "applied_after": "not-a-date",
            "fanout_complete": False,
        },
    )
    db.add(run)
    db.commit()

    scanned, payloads = claim_due_scoring_fanouts(db, limit=5)

    assert scanned == 1
    assert payloads[0]["run_id"] == run.id
    assert payloads[0]["applied_after"] is None


@pytest.mark.parametrize(
    ("payload_update", "reason"),
    [
        ({"reconcile_after": "not-a-date"}, "invalid_reconcile_after"),
        (
            {
                "claimed_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            },
            "invalid_future_claimed_at",
        ),
    ],
)
def test_successor_recovery_quarantines_future_or_malformed_metadata(
    db, monkeypatch, payload_update, reason
) -> None:
    organization, role = _scope(db, f"successor-{reason}")
    payload = {
        "queue_id": reason,
        "include_scored": False,
        "applied_after": None,
        "state": "pending",
        "dispatch_attempt": 0,
        **payload_update,
    }
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [123],
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "fanout_complete": True,
            "queued_successor": payload,
        },
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=5
    )

    db.expire_all()
    run = db.get(BackgroundJobRun, run.id)
    assert result["quarantined"] == 1
    assert run.counters["quarantined_scoring_successor"]["reason"] == reason
    assert "queued_successor" not in run.counters


def test_future_successor_claim_timestamp_cannot_suppress_recovery(
    db, monkeypatch
) -> None:
    organization, role = _scope(db, "successor-claim-future")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters={
            "queued_successor": {
                "queue_id": "future-claim",
                "include_scored": False,
                "applied_after": None,
                "state": "claimed",
                "claim_token": "stale-token",
                "claimed_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
            }
        },
        finished_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        scoring_batch_successors,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )

    claimed = scoring_batch_successors.claim_scoring_successor(
        run.id,
        role_id=role.id,
        organization_id=organization.id,
    )

    assert claimed is not None
    assert claimed["claim_token"] != "stale-token"


@pytest.mark.parametrize(
    ("tail_updates", "reason"),
    [
        ({"queue_contract": "missing-contract"}, "unsupported_queue_contract"),
        (
            {
                "fanout_lease_expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            },
            "invalid_future_fanout_lease_expires_at",
        ),
    ],
)
def test_fanout_audit_rotates_beyond_one_hundred_live_roots(
    db,
    tail_updates,
    reason,
) -> None:
    organization, role = _scope(db, f"fanout-rotation-{reason}")
    now = datetime.now(timezone.utc)
    live_until = (now + timedelta(minutes=5)).isoformat()
    prefix = [
        BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=organization.id,
            status="running",
            counters={
                "queue_contract": SCORING_QUEUE_CONTRACT,
                "target_application_ids": [1_000 + index],
                "include_scored": False,
                "applied_after": None,
                "fanout_complete": False,
                "fanout_lease_expires_at": live_until,
            },
        )
        for index in range(101)
    ]
    tail_counters = {
        "queue_contract": SCORING_QUEUE_CONTRACT,
        "target_application_ids": [9_999],
        "include_scored": False,
        "applied_after": None,
        "fanout_complete": False,
        "fanout_lease_expires_at": live_until,
        **tail_updates,
    }
    tail = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters=tail_counters,
    )
    db.add_all((*prefix, tail))
    db.commit()

    first = claim_due_scoring_fanouts(db, limit=1, now=now)
    db.commit()
    second = claim_due_scoring_fanouts(db, limit=1, now=now)
    db.commit()
    db.refresh(prefix[0])
    db.refresh(tail)

    assert first == (0, [])
    assert second == (1, [])
    assert "fanout_recovery_audited_at" in prefix[0].counters
    assert tail.status == "failed"
    assert tail.counters["fanout_quarantine_reason"] == reason


def test_fanout_publish_recovery_excludes_completed_fanouts(db) -> None:
    organization, role = _scope(db, "fanout-complete-excluded")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={
            "fanout_complete": True,
            "target_application_ids": [123],
            "include_scored": False,
        },
    )
    db.add(run)
    db.commit()

    assert claim_due_scoring_fanouts(db, limit=5) == (0, [])
    db.commit()
    db.refresh(run)

    assert run.status == "running"
    assert "fanout_recovery_audited_at" not in run.counters


def test_successor_audit_rotates_beyond_one_hundred_deferred_intents(
    db,
    monkeypatch,
) -> None:
    organization, role = _scope(db, "successor-rotation")
    deferred_until = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()

    def _counters(queue_id: str, reconcile_after: str) -> dict:
        return {
            "total": 1,
            "selected_total": 1,
            "target_application_ids": [123],
            "dispatched_application_ids": [],
            "score_job_ids": [],
            "owned_score_job_ids": [],
            "not_enqueued": 1,
            "fanout_complete": True,
            "queued_successor": {
                "queue_id": queue_id,
                "include_scored": False,
                "applied_after": None,
                "state": "pending",
                "dispatch_attempt": 0,
                "reconcile_after": reconcile_after,
            },
        }

    prefix = [
        BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=organization.id,
            status="completed",
            counters=_counters(f"deferred-{index}", deferred_until),
            finished_at=datetime.now(timezone.utc),
        )
        for index in range(101)
    ]
    tail = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=_counters("invalid-tail", "not-a-date"),
        finished_at=datetime.now(timezone.utc),
    )
    db.add_all((*prefix, tail))
    db.commit()
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "SessionLocal",
        sessionmaker(bind=db.get_bind(), expire_on_commit=False),
    )

    first = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )
    second = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )

    db.expire_all()
    tail = db.get(BackgroundJobRun, tail.id)
    assert first["quarantined"] == 0
    assert second["quarantined"] == 1
    assert tail.counters["quarantined_scoring_successor"]["reason"] == (
        "invalid_reconcile_after"
    )
