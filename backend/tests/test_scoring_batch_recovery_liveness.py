from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from app.domains.assessments_runtime.scoring_batch_state import (
    scoring_batch_has_active_jobs,
    scoring_fanout_abandoned,
)
from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import SCORE_JOB_DONE, SCORE_JOB_PENDING, CvScoreJob
from app.models.organization import Organization
from app.models.role import Role
from app.services import scoring_batch_successor_reconcile
from app.services.scoring_batch_fanout_recovery import (
    SCORING_QUEUE_CONTRACT,
    claim_due_scoring_fanouts,
)


def _scope(db, label: str) -> tuple[Organization, Role]:
    organization = Organization(name=f"Recovery {label}", slug=f"recovery-{label}")
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=organization.id,
        name=f"Recovery {label}",
        source="manual",
        job_spec_text="Recover exact scoring work safely.",
    )
    db.add(role)
    db.flush()
    return organization, role


def _application(db, organization: Organization, role: Role, label: str):
    candidate = Candidate(
        organization_id=organization.id,
        email=f"{label}@recovery.test",
        full_name=label,
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        status="applied",
        cv_text=f"CV for {label}",
    )
    db.add(application)
    db.flush()
    return application


def _fanout_counters(target_ids: list[object], **updates):
    counters = {
        "queue_contract": SCORING_QUEUE_CONTRACT,
        "target_application_ids": target_ids,
        "include_scored": False,
        "applied_after": None,
        "fanout_complete": False,
    }
    counters.update(updates)
    return counters


def _successor_payload(queue_id: str, **updates):
    payload = {
        "queue_id": queue_id,
        "include_scored": False,
        "applied_after": None,
        "state": "pending",
        "dispatch_attempt": 0,
    }
    payload.update(updates)
    return payload


def _parent_counters(application_id: int, queue_id: str, **updates):
    counters = {
        "total": 1,
        "selected_total": 1,
        "target_application_ids": [application_id],
        "dispatched_application_ids": [application_id],
        "score_job_ids": [],
        "owned_score_job_ids": [],
        "fanout_complete": True,
        "queued_successor": _successor_payload(queue_id),
    }
    counters.update(updates)
    return counters


def test_fanout_recovery_quarantines_bad_prefix_and_claims_ready_tail(db):
    organization, role = _scope(db, "fanout-prefix")
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    malformed_runs = [
        BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ROLE,
            scope_id=role.id,
            organization_id=organization.id,
            status="queued",
            counters=_fanout_counters(["not-an-application-id"]),
        )
        for _ in range(30)
    ]
    live = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters=_fanout_counters(
            [101],
            fanout_lease_expires_at=(now + timedelta(minutes=5)).isoformat(),
        ),
    )
    ready = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="queued",
        counters=_fanout_counters([102]),
    )
    db.add_all((*malformed_runs, live, ready))
    db.commit()

    scanned, payloads = claim_due_scoring_fanouts(db, limit=1, now=now)
    db.commit()

    assert scanned == 31
    assert [payload["run_id"] for payload in payloads] == [ready.id]
    malformed = malformed_runs[0]
    db.refresh(malformed)
    db.refresh(live)
    assert malformed.status == "failed"
    assert malformed.finished_at is not None
    assert malformed.error == "scoring_batch_invalid_fanout_recovery_contract"
    assert malformed.counters["fanout_quarantine_reason"] == (
        "invalid_target_application_ids"
    )
    assert live.status == "running"
    assert live.finished_at is None


def test_abandonment_uses_heartbeat_and_lease_liveness():
    now = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)
    progress = {
        "status": "running",
        "fanout_complete": False,
        "started_at": (now - timedelta(hours=4)).isoformat(),
        "fanout_heartbeat_at": (now - timedelta(minutes=1)).isoformat(),
        "fanout_lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
    }

    assert not scoring_fanout_abandoned(progress, now=now)
    progress["fanout_heartbeat_at"] = (now - timedelta(hours=3)).isoformat()
    progress["fanout_lease_expires_at"] = (now + timedelta(minutes=1)).isoformat()
    assert not scoring_fanout_abandoned(progress, now=now)
    progress["fanout_lease_expires_at"] = (now - timedelta(minutes=1)).isoformat()
    assert scoring_fanout_abandoned(progress, now=now)


def test_successor_recovery_quarantines_invalid_and_bypasses_active_prefix(
    db, monkeypatch
):
    organization, role = _scope(db, "successor-prefix")
    invalid_application = _application(db, organization, role, "invalid")
    active_application = _application(db, organization, role, "active")
    ready_application = _application(db, organization, role, "ready")
    invalid = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters=_parent_counters(
            invalid_application.id,
            "invalid",
            total="one",
            selected_total=True,
            queued_successor=_successor_payload(
                "invalid",
                include_scored="false",
            ),
        ),
    )
    active = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters=_parent_counters(active_application.id, "active"),
    )
    ready = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=_parent_counters(ready_application.id, "ready"),
        finished_at=datetime.now(timezone.utc),
    )
    db.add_all((invalid, active, ready))
    db.flush()
    active_job = CvScoreJob(
        application_id=active_application.id,
        role_id=role.id,
        batch_run_id=active.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=False,
    )
    db.add(active_job)
    db.flush()
    ready_job = CvScoreJob(
        application_id=ready_application.id,
        role_id=role.id,
        batch_run_id=ready.id,
        status=SCORE_JOB_DONE,
        requires_active_agent=False,
        finished_at=datetime.now(timezone.utc),
    )
    db.add(ready_job)
    db.flush()
    active.counters = {
        **active.counters,
        "score_job_ids": [active_job.id],
        "owned_score_job_ids": [active_job.id],
    }
    ready.counters = {
        **ready.counters,
        "score_job_ids": [ready_job.id],
        "owned_score_job_ids": [ready_job.id],
    }
    db.commit()

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(scoring_batch_successor_reconcile, "SessionLocal", factory)
    claimed_ids: list[int] = []

    def _claim(run_id, **_scope):
        claimed_ids.append(run_id)
        return {**_successor_payload("ready"), "claim_token": "claim"}

    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "claim_scoring_successor",
        _claim,
    )
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "dispatch_claimed_scoring_successor",
        lambda *_args, **_kwargs: {"outcome": "started"},
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )

    assert result["started"] == 1
    assert result["quarantined"] == 1
    assert claimed_ids == [ready.id]
    db.expire_all()
    invalid = db.get(BackgroundJobRun, invalid.id)
    active = db.get(BackgroundJobRun, active.id)
    assert invalid.status == "failed"
    assert invalid.finished_at is not None
    assert invalid.error == "scoring_batch_invalid_terminal_receipts"
    assert "queued_successor" not in invalid.counters
    assert (
        invalid.counters["quarantined_scoring_successor"]["payload"]["include_scored"]
        == "false"
    )
    assert active.status == "running"
    assert "queued_successor" in active.counters
    assert "reconcile_after" in active.counters["queued_successor"]


def test_successor_recovery_quarantines_overcounted_parent_receipts(db, monkeypatch):
    organization, role = _scope(db, "successor-overcount")
    application = _application(db, organization, role, "overcount")
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=_parent_counters(
            application.id,
            "overcount",
            not_enqueued=2,
        ),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(scoring_batch_successor_reconcile, "SessionLocal", factory)
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "claim_scoring_successor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("corrupt parent successor was claimed")
        ),
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["quarantined"] == 1
    assert parent.status == "failed"
    assert parent.error == "scoring_batch_invalid_terminal_receipts"
    assert "queued_successor" not in parent.counters
    assert parent.counters["quarantined_scoring_successor"]["reason"] == (
        "scoring_batch_invalid_terminal_receipts"
    )


def test_successor_recovery_quarantines_stored_completed_receipt_deficit(
    db, monkeypatch
):
    organization, role = _scope(db, "successor-deficit")
    application = _application(db, organization, role, "deficit")
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=_parent_counters(application.id, "deficit"),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.commit()

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(scoring_batch_successor_reconcile, "SessionLocal", factory)
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "claim_scoring_successor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incomplete parent successor was claimed")
        ),
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["quarantined"] == 1
    assert parent.status == "failed"
    assert parent.error == "scoring_batch_incomplete_terminal_receipts"
    assert "queued_successor" not in parent.counters
    assert parent.counters["quarantined_scoring_successor"]["reason"] == (
        "scoring_batch_incomplete_terminal_receipts"
    )


def test_successor_recovery_rejects_terminal_receipt_for_undispatched_target(
    db, monkeypatch
):
    organization, role = _scope(db, "successor-identity")
    dispatched = _application(db, organization, role, "dispatched")
    other = _application(db, organization, role, "other")
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=_parent_counters(
            dispatched.id,
            "identity",
            total=2,
            selected_total=2,
            target_application_ids=[dispatched.id, other.id],
            dispatched_application_ids=[dispatched.id],
            not_enqueued=1,
        ),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(parent)
    db.flush()
    db.add(
        CvScoreJob(
            application_id=other.id,
            role_id=role.id,
            batch_run_id=parent.id,
            status=SCORE_JOB_DONE,
            requires_active_agent=False,
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(scoring_batch_successor_reconcile, "SessionLocal", factory)
    monkeypatch.setattr(
        scoring_batch_successor_reconcile,
        "claim_scoring_successor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("identity-corrupt successor was claimed")
        ),
    )

    result = scoring_batch_successor_reconcile.reconcile_queued_scoring_successors(
        limit=1
    )

    db.expire_all()
    parent = db.get(BackgroundJobRun, parent.id)
    assert result["quarantined"] == 1
    assert parent.status == "failed"
    assert parent.error == "scoring_batch_invalid_terminal_receipts"
    assert parent.counters["quarantined_scoring_successor"]["reason"] == (
        "scoring_batch_invalid_terminal_receipts"
    )


def test_active_receipts_ignore_jobs_outside_exact_target_set(db):
    organization, role = _scope(db, "target-aware")
    target = _application(db, organization, role, "target")
    other = _application(db, organization, role, "other")
    run = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="running",
        counters={},
    )
    db.add(run)
    db.flush()
    other_job = CvScoreJob(
        application_id=other.id,
        role_id=role.id,
        batch_run_id=run.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=False,
    )
    db.add(other_job)
    db.commit()
    progress = {
        "target_application_ids": [target.id],
        "score_job_ids": [other_job.id],
    }

    assert not scoring_batch_has_active_jobs(
        db,
        run_id=run.id,
        progress=progress,
    )
    target_job = CvScoreJob(
        application_id=target.id,
        role_id=role.id,
        batch_run_id=run.id,
        status=SCORE_JOB_PENDING,
        requires_active_agent=False,
    )
    db.add(target_job)
    db.commit()
    assert scoring_batch_has_active_jobs(db, run_id=run.id, progress=progress)
