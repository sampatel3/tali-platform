"""Failure-path contracts for the durable recruiter Process cascade."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime import process_routes as process_routes_module
from app.domains.assessments_runtime.process_routes import (
    process_role,
    process_role_cancel,
    process_role_status,
)
from app.models.background_job_run import (
    JOB_KIND_PROCESS_ROLE,
    BackgroundJobRun,
)
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.services.process_role_dispatch import (
    claim_process_publish,
    claim_process_worker,
    ensure_process_role_intent,
    mark_process_dispatched,
)
from app.tasks.prescreen_tasks import recover_process_role_runs


def _scope(db, suffix: str) -> tuple[Organization, Role, User]:
    org = Organization(name=f"Process {suffix}", slug=f"process-{suffix}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name=f"Role {suffix}")
    user = User(
        organization_id=org.id,
        email=f"process-{suffix}@example.test",
        hashed_password="x",
        full_name="Process Owner",
        role="owner",
        is_active=True,
    )
    db.add_all([role, user])
    db.commit()
    return org, role, user


def _progress(role_name: str) -> dict:
    return {
        "status": "queued",
        "role_name": role_name,
        "current_step": None,
        "fetch": {
            "attempted": 0,
            "fetched": 0,
            "unavailable": 0,
            "errors": 0,
            "total": 0,
        },
        "pre_screen": {"total": 0, "processed": 0, "errors": 0},
        "score": {
            "total": 0,
            "scored": 0,
            "filtered": 0,
            "errors": 0,
            "mode": "none",
        },
        "graph_sync": {"total": 0, "synced": 0, "errors": 0},
    }


def test_broker_failure_keeps_intent_recoverable(db):
    org, role, user = _scope(db, "broker-loss")

    with patch(
        "app.tasks.prescreen_tasks.process_role_job.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        result = process_role(
            role.id,
            payload={"fetch_cvs": True},
            dry_run=False,
            db=db,
            current_user=user,
        )

    assert result["dispatch_pending"] is True
    assert result["status"] == "queued"
    run = (
        db.query(BackgroundJobRun)
        .filter_by(kind=JOB_KIND_PROCESS_ROLE, scope_id=role.id)
        .one()
    )
    assert run.status == "dispatching"
    assert run.finished_at is None
    counters = dict(run.counters or {})
    assert counters["recovery_payload"]["organization_id"] == org.id
    assert counters["recovery_payload"]["fetch_cvs"] is True
    counters["dispatch_next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    run.counters = counters
    db.commit()

    with patch("app.tasks.prescreen_tasks.process_role_job.delay") as delay:
        recovered = recover_process_role_runs.run(limit=10)

    assert recovered["kicked"] == 1
    assert recovered["publish_failed"] == 0
    delay.assert_called_once_with(run_id=int(run.id))
    db.expire_all()
    assert db.get(BackgroundJobRun, run.id).status == "queued"


def test_duplicate_delivery_cannot_overlap_running_process(db):
    org, role, _user = _scope(db, "duplicate")
    intent = ensure_process_role_intent(
        db,
        role_id=role.id,
        organization_id=org.id,
        payload={
            "role_id": role.id,
            "organization_id": org.id,
            "fetch_cvs": True,
        },
        progress=_progress(role.name),
    )
    claim_process_publish(intent.run)
    db.commit()
    mark_process_dispatched(db, run_id=int(intent.run.id))
    db.commit()

    first = claim_process_worker(db, run_id=int(intent.run.id))
    db.commit()
    second = claim_process_worker(db, run_id=int(intent.run.id))
    db.commit()

    assert first.state == "claimed"
    assert first.payload is not None
    assert second.state == "already_running"
    assert second.payload is None


def test_stale_worker_fails_without_paid_replay_and_unblocks_role(db):
    org, role, _user = _scope(db, "worker-loss")
    intent = ensure_process_role_intent(
        db,
        role_id=role.id,
        organization_id=org.id,
        payload={
            "role_id": role.id,
            "organization_id": org.id,
            "pre_screen": True,
        },
        progress=_progress(role.name),
    )
    claim_process_publish(intent.run)
    db.commit()
    mark_process_dispatched(db, run_id=int(intent.run.id))
    db.commit()
    claimed = claim_process_worker(db, run_id=int(intent.run.id))
    db.commit()
    assert claimed.state == "claimed"

    run = db.get(BackgroundJobRun, intent.run.id)
    counters = dict(run.counters or {})
    counters["worker_lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    run.counters = counters
    db.commit()

    with patch("app.tasks.prescreen_tasks.process_role_job.delay") as delay:
        recovered = recover_process_role_runs.run(limit=10)

    assert recovered["expired_workers"] == 1
    assert recovered["kicked"] == 0
    delay.assert_not_called()
    db.expire_all()
    failed = db.get(BackgroundJobRun, intent.run.id)
    assert failed.status == "failed"
    assert failed.error == "process_worker_lost"
    assert failed.finished_at is not None
    assert failed.counters["progress"]["status"] == "failed"

    replacement = ensure_process_role_intent(
        db,
        role_id=role.id,
        organization_id=org.id,
        payload={
            "role_id": role.id,
            "organization_id": org.id,
            "pre_screen": True,
        },
        progress=_progress(role.name),
    )
    assert replacement.created is True
    assert replacement.run.id != failed.id


@pytest.mark.parametrize("membership", [None, "interviewer"])
def test_process_start_and_cancel_require_control_permission(db, membership):
    org, role, _owner = _scope(db, f"denied-{membership or 'unassigned'}")
    actor = User(
        organization_id=org.id,
        email=f"process-actor-denied-{membership or 'unassigned'}@example.test",
        hashed_password="x",
        full_name="Denied Process User",
        role="member",
        is_active=True,
    )
    db.add(actor)
    db.flush()
    if membership is not None:
        db.add(
            JobHiringTeam(
                organization_id=org.id,
                role_id=role.id,
                user_id=actor.id,
                team_role=membership,
            )
        )
    db.commit()

    with patch(
        "app.domains.assessments_runtime.process_routes.ensure_process_role_intent"
    ) as ensure_intent:
        with pytest.raises(HTTPException) as start_error:
            process_role(
                role.id,
                payload={"fetch_cvs": True},
                dry_run=False,
                db=db,
                current_user=actor,
            )
    assert start_error.value.status_code == 403
    ensure_intent.assert_not_called()

    with patch(
        "app.domains.assessments_runtime.process_routes.request_process_cancel"
    ) as request_cancel:
        with pytest.raises(HTTPException) as cancel_error:
            process_role_cancel(role.id, db=db, current_user=actor)
    assert cancel_error.value.status_code == 403
    request_cancel.assert_not_called()
    assert (
        db.query(BackgroundJobRun)
        .filter_by(kind=JOB_KIND_PROCESS_ROLE, scope_id=role.id)
        .count()
        == 0
    )


def test_process_status_uses_view_permission_without_granting_control(db):
    org, role, _owner = _scope(db, "viewer")
    viewer = User(
        organization_id=org.id,
        email="process-readonly-viewer@example.test",
        hashed_password="x",
        full_name="Process Viewer",
        role="member",
        is_active=True,
    )
    other_org = Organization(name="Other Process", slug="other-process")
    db.add_all([viewer, other_org])
    db.flush()
    outsider = User(
        organization_id=other_org.id,
        email="process-outsider@example.test",
        hashed_password="x",
        full_name="Process Outsider",
        role="owner",
        is_active=True,
    )
    db.add(outsider)
    db.commit()

    # The compatibility in-memory progress map is process-global; isolate this
    # newly created role id from earlier tests that used a fresh SQLite DB.
    process_routes_module._process_progress.pop(int(role.id), None)
    with patch.object(
        process_routes_module, "_read_process_progress", return_value=None
    ):
        visible = process_role_status(role.id, db=db, current_user=viewer)
    assert visible["role_name"] == role.name
    assert visible["status"] == "idle"

    with patch(
        "app.domains.assessments_runtime.process_routes.latest_process_role_run"
    ) as latest_run:
        with pytest.raises(HTTPException) as status_error:
            process_role_status(role.id, db=db, current_user=outsider)
    assert status_error.value.status_code == 403
    latest_run.assert_not_called()
