from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.domains.assessments_runtime.scoring_batch_backfill_runtime import (
    read_scoring_backfill_status,
)
from app.domains.assessments_runtime import applications_routes
from app.models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ORG,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services import scoring_backfill_recovery
from app.tasks import scoring_tasks


def _seed_roles_and_targets(db, *, roles: int = 2):
    organization = Organization(
        name="Backfill recovery",
        slug=f"backfill-recovery-{id(db)}-{roles}",
    )
    db.add(organization)
    db.flush()
    seeded: list[tuple[Role, CandidateApplication]] = []
    for index in range(roles):
        role = Role(
            organization_id=organization.id,
            name=f"Role {index + 1}",
            source="manual",
            job_spec_text="Score this exact cohort.",
        )
        db.add(role)
        db.flush()
        candidate = Candidate(
            organization_id=organization.id,
            email=f"backfill-recovery-{index}-{id(db)}@example.test",
            full_name=f"Candidate {index + 1}",
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=organization.id,
            candidate_id=candidate.id,
            role_id=role.id,
            source="manual",
            cv_text="Exact target CV",
        )
        db.add(application)
        db.flush()
        seeded.append((role, application))
    db.commit()
    return organization, seeded


def _parent_counters(seeded) -> dict:
    plan = [
        {
            "role_id": int(role.id),
            "role_name": str(role.name),
            "target_application_ids": [int(application.id)],
        }
        for role, application in seeded
    ]
    return {
        "backfill_parent": True,
        "include_scored": False,
        "applied_after": None,
        "role_plan_version": scoring_backfill_recovery.SCORING_BACKFILL_PLAN_VERSION,
        "role_plan": plan,
        "role_plan_digest": scoring_backfill_recovery.scoring_backfill_plan_digest(
            plan
        ),
        "fanout_cursor": 0,
        "children": [],
        "skipped": [],
        "total_target": len(plan),
        "fanout_complete": False,
    }


def _add_parent(db, organization, counters: dict) -> BackgroundJobRun:
    parent = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ORG,
        scope_id=organization.id,
        organization_id=organization.id,
        status="dispatching",
        counters=counters,
    )
    db.add(parent)
    db.commit()
    return parent


def test_route_persists_complete_immutable_plan_before_first_child_dispatch(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db)
    created: list[dict] = []
    run_ids = iter((7000, 7001, 7002))

    def _create(**kwargs):
        if kwargs["scope_kind"] == SCOPE_KIND_ROLE:
            parent_counters = created[0]["counters"]
            assert parent_counters["fanout_cursor"] == 0
            assert parent_counters["fanout_complete"] is False
            assert [entry["role_id"] for entry in parent_counters["role_plan"]] == [
                role.id for role, _ in seeded
            ]
            assert [
                entry["target_application_ids"]
                for entry in parent_counters["role_plan"]
            ] == [[application.id] for _, application in seeded]
        created.append(kwargs)
        return next(run_ids)

    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})
    monkeypatch.setattr(applications_routes, "_create_job_run", _create)
    monkeypatch.setattr(
        applications_routes, "_update_job_run", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        applications_routes, "_write_batch_meta", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(applications_routes, "_redis_client", lambda: None)
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **_kwargs: None,
    )

    result = applications_routes.batch_score_all_roles(
        applied_after=None,
        include_scored=False,
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    parent_counters = created[0]["counters"]
    assert result["status"] == "dispatched"
    assert len(created) == 3
    assert parent_counters["role_plan_digest"] == (
        scoring_backfill_recovery.scoring_backfill_plan_digest(
            parent_counters["role_plan"]
        )
    )


def test_recovery_creates_and_publishes_exactly_one_child_per_planned_role(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db)
    counters = _parent_counters(seeded)
    immutable_plan = list(counters["role_plan"])
    parent = _add_parent(db, organization, counters)
    dispatched: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    first = scoring_backfill_recovery.recover_scoring_backfill_parent(
        parent.id,
        max_children=10,
    )
    second = scoring_backfill_recovery.recover_scoring_backfill_parent(
        parent.id,
        max_children=10,
    )
    db.expire_all()
    db.refresh(parent)
    children = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
            BackgroundJobRun.organization_id == organization.id,
        )
        .order_by(BackgroundJobRun.scope_id.asc())
        .all()
    )

    assert first == {
        "created": 2,
        "adopted": 0,
        "published": 2,
        "publish_failed": 0,
    }
    assert second == {
        "created": 0,
        "adopted": 0,
        "published": 0,
        "publish_failed": 0,
    }
    assert len(children) == 2
    assert [child.dispatch_key for child in children] == [
        f"scoring-backfill:{parent.id}:{role.id}" for role, _ in seeded
    ]
    assert [child.counters["target_application_ids"] for child in children] == [
        [application.id] for _, application in seeded
    ]
    assert [call[0] for call in dispatched] == [(role.id,) for role, _ in seeded]
    assert parent.counters["role_plan"] == immutable_plan
    assert parent.counters["fanout_cursor"] == 2
    assert parent.counters["fanout_complete"] is True
    assert scoring_backfill_recovery.scoring_backfill_fanout_accounted(parent.counters)
    assert parent.status == "running"
    assert parent.finished_at is None


def test_recovery_adopts_child_committed_before_parent_cursor_update(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    parent = _add_parent(db, organization, _parent_counters(seeded))
    role, application = seeded[0]
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="dispatching",
        counters=scoring_backfill_recovery.scoring_backfill_child_counters(
            target_ids=[application.id],
            include_scored=False,
            applied_after=None,
            parent_run_id=parent.id,
        ),
        dispatch_key=f"scoring-backfill:{parent.id}:{role.id}",
    )
    db.add(child)
    db.commit()
    dispatched: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **kwargs: dispatched.append(kwargs),
    )

    result = scoring_backfill_recovery.recover_scoring_backfill_parent(parent.id)
    db.expire_all()
    db.refresh(parent)

    assert result == {
        "created": 0,
        "adopted": 1,
        "published": 1,
        "publish_failed": 0,
    }
    assert (
        db.query(BackgroundJobRun)
        .filter(BackgroundJobRun.dispatch_key == child.dispatch_key)
        .count()
        == 1
    )
    assert parent.counters["children"][0]["run_id"] == child.id
    assert parent.counters["fanout_cursor"] == 1
    assert parent.counters["fanout_complete"] is True
    assert dispatched == [
        {
            "include_scored": False,
            "applied_after": None,
            "run_id": child.id,
        }
    ]


def test_parent_status_requires_completed_fanout_and_every_planned_role(db) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    counters = _parent_counters(seeded)
    counters["fanout_complete"] = True
    parent = _add_parent(db, organization, counters)

    response = read_scoring_backfill_status(
        {},
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )
    db.refresh(parent)

    assert response["status"] == "running"
    assert response["roles"] == []
    assert parent.finished_at is None


def test_empty_plan_finishes_without_creating_or_publishing_children(
    db,
    monkeypatch,
) -> None:
    organization, _seeded = _seed_roles_and_targets(db, roles=0)
    counters = _parent_counters([])
    counters["fanout_lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    parent = _add_parent(db, organization, counters)
    dispatched: list[object] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **_kwargs: dispatched.append(object()),
    )

    result = scoring_backfill_recovery.recover_scoring_backfill_parent(parent.id)
    db.expire_all()
    db.refresh(parent)

    assert result == {
        "created": 0,
        "adopted": 0,
        "published": 0,
        "publish_failed": 0,
    }
    assert parent.status == "completed"
    assert parent.finished_at is not None
    assert parent.counters["fanout_cursor"] == 0
    assert parent.counters["fanout_complete"] is True
    assert scoring_backfill_recovery.scoring_backfill_fanout_accounted(parent.counters)
    assert dispatched == []


def test_reconciler_waits_for_live_producer_lease_then_recovers(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    counters = _parent_counters(seeded)
    counters["fanout_lease_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=1)
    ).isoformat()
    parent = _add_parent(db, organization, counters)
    dispatched: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **kwargs: dispatched.append(kwargs),
    )

    leased = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=5)
    db.refresh(parent)
    released_counters = dict(parent.counters)
    released_counters["fanout_lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    parent.counters = released_counters
    db.commit()
    recovered = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=5)

    assert leased["created"] == 0
    assert recovered["created"] == 1
    assert recovered["published"] == 1
    assert len(dispatched) == 1


@pytest.mark.parametrize(
    ("child_status", "expected_errors", "expected_not_processed"),
    [("failed", 1, 0), ("cancelled", 0, 1)],
)
def test_parent_truthfully_aggregates_terminal_child_deficits(
    db,
    monkeypatch,
    child_status,
    expected_errors,
    expected_not_processed,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    role, application = seeded[0]
    counters = _parent_counters(seeded)
    parent = _add_parent(db, organization, counters)
    child_counters = scoring_backfill_recovery.scoring_backfill_child_counters(
        target_ids=[application.id],
        include_scored=False,
        applied_after=None,
        parent_run_id=parent.id,
    )
    child_counters["fanout_complete"] = True
    if child_status == "cancelled":
        child_counters["not_enqueued"] = 1
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status=child_status,
        counters=child_counters,
        finished_at=datetime.now(timezone.utc),
        dispatch_key=f"scoring-backfill:{parent.id}:{role.id}",
    )
    db.add(child)
    db.flush()
    counters.update(
        children=[
            {
                "role_id": role.id,
                "run_id": child.id,
                "target": 1,
                "dispatch_status": "dispatched",
            }
        ],
        fanout_cursor=1,
        fanout_complete=True,
    )
    parent.counters = counters
    db.commit()
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.batch_score_all_status(
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == child_status
    assert response["total_errors"] == expected_errors
    assert response["total_not_processed"] == expected_not_processed
    assert response["processed"] == expected_errors
    assert response["roles"][0]["status"] == child_status
    db.expire_all()
    db.refresh(parent)
    assert parent.status == child_status


def test_status_poll_fails_closed_on_overlarge_child_not_enqueued(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    role, application = seeded[0]
    counters = _parent_counters(seeded)
    parent = _add_parent(db, organization, counters)
    child_counters = scoring_backfill_recovery.scoring_backfill_child_counters(
        target_ids=[application.id],
        include_scored=False,
        applied_after=None,
        parent_run_id=parent.id,
    )
    child_counters.update(fanout_complete=True, not_enqueued=2)
    child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=child_counters,
        finished_at=datetime.now(timezone.utc),
        dispatch_key=f"scoring-backfill:{parent.id}:{role.id}",
    )
    db.add(child)
    db.flush()
    counters.update(
        children=[
            {
                "role_id": role.id,
                "run_id": child.id,
                "target": 1,
                "dispatch_status": "dispatched",
            }
        ],
        fanout_cursor=1,
        fanout_complete=True,
    )
    parent.counters = counters
    db.commit()
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.batch_score_all_status(
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "failed"
    assert response["total_errors"] == 1
    assert response["roles"][0]["status"] == "failed"
    db.expire_all()
    db.refresh(parent)
    assert parent.status == "failed"
    assert parent.finished_at is not None


def test_status_poll_rejects_child_not_owned_by_immutable_parent_receipt(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    role, application = seeded[0]
    counters = _parent_counters(seeded)
    parent = _add_parent(db, organization, counters)
    unrelated_counters = scoring_backfill_recovery.scoring_backfill_child_counters(
        target_ids=[application.id],
        include_scored=False,
        applied_after=None,
        parent_run_id=parent.id + 1000,
    )
    unrelated_counters.update(fanout_complete=True, not_enqueued=1)
    unrelated_child = BackgroundJobRun(
        kind=JOB_KIND_SCORING_BATCH,
        scope_kind=SCOPE_KIND_ROLE,
        scope_id=role.id,
        organization_id=organization.id,
        status="completed",
        counters=unrelated_counters,
        finished_at=datetime.now(timezone.utc),
        dispatch_key=f"scoring-backfill:{parent.id + 1000}:{role.id}",
    )
    db.add(unrelated_child)
    db.flush()
    counters.update(
        children=[
            {
                "role_id": role.id,
                "run_id": unrelated_child.id,
                "target": 1,
                "dispatch_status": "dispatched",
            }
        ],
        fanout_cursor=1,
        fanout_complete=True,
    )
    parent.counters = counters
    db.commit()
    monkeypatch.setattr(applications_routes, "_batch_score_progress", {})

    response = applications_routes.batch_score_all_status(
        db=db,
        current_user=SimpleNamespace(organization_id=organization.id),
    )

    assert response["status"] == "failed"
    assert response["total_scored"] == 0
    assert response["total_errors"] == 1
    assert response["roles"][0]["receipt_invalid"] is True
    db.expire_all()
    db.refresh(parent)
    assert parent.status == "failed"
    assert parent.error == "scoring_backfill_child_receipt_invalid"


def test_reconciler_quarantines_malformed_parent_lease_before_publish(
    db, monkeypatch
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    counters = _parent_counters(seeded)
    counters["fanout_lease_expires_at"] = "not-a-date"
    parent = _add_parent(db, organization, counters)
    dispatched: list[object] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **_kwargs: dispatched.append(object()),
    )

    result = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=1)

    db.expire_all()
    db.refresh(parent)
    assert result["parents_recovered"] == 1
    assert result["created"] == result["published"] == 0
    assert parent.status == "failed"
    assert parent.finished_at is not None
    assert parent.error == "scoring_backfill_fanout_lease_invalid"
    assert (
        parent.counters["fanout_quarantine_reason"] == "invalid_fanout_lease_expires_at"
    )
    assert dispatched == []


def test_reconciler_sanitizes_malformed_applied_after_for_exact_recovery(
    db, monkeypatch
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    role, application = seeded[0]
    counters = _parent_counters(seeded)
    counters["applied_after"] = "not-a-date"
    parent = _add_parent(db, organization, counters)
    dispatched: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **kwargs: dispatched.append(kwargs),
    )

    result = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=1)

    db.expire_all()
    db.refresh(parent)
    child = (
        db.query(BackgroundJobRun)
        .filter(
            BackgroundJobRun.dispatch_key == f"scoring-backfill:{parent.id}:{role.id}"
        )
        .one()
    )
    assert result["created"] == result["published"] == 1
    assert parent.status == "running"
    assert parent.finished_at is None
    assert parent.error is None
    assert parent.counters["applied_after"] == "not-a-date"
    assert child.counters["applied_after"] is None
    assert child.counters["target_application_ids"] == [application.id]
    assert dispatched == [
        {
            "include_scored": False,
            "applied_after": None,
            "run_id": child.id,
        }
    ]


def test_backfill_audit_rotates_past_old_prefix_and_validates_all_markers(
    db,
    monkeypatch,
) -> None:
    organization, seeded = _seed_roles_and_targets(db, roles=1)
    live_until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    def _run(counters: dict) -> BackgroundJobRun:
        return BackgroundJobRun(
            kind=JOB_KIND_SCORING_BATCH,
            scope_kind=SCOPE_KIND_ORG,
            scope_id=organization.id,
            organization_id=organization.id,
            status="dispatching",
            counters=counters,
        )

    prefix: list[BackgroundJobRun] = []
    for _index in range(101):
        counters = _parent_counters(seeded)
        counters["fanout_lease_expires_at"] = live_until
        prefix.append(_run(counters))

    missing_parent_marker = _parent_counters(seeded)
    missing_parent_marker.pop("backfill_parent")
    corrupt_plan_version = _parent_counters(seeded)
    corrupt_plan_version["role_plan_version"] = "1"
    missing_complete_marker = _parent_counters(seeded)
    missing_complete_marker.pop("fanout_complete")
    far_future_lease = _parent_counters(seeded)
    far_future_lease["fanout_lease_expires_at"] = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()
    invalid = [
        _run(missing_parent_marker),
        _run(corrupt_plan_version),
        _run(missing_complete_marker),
        _run(far_future_lease),
    ]
    # A missing lease is valid and due: it must use the fast recovery path even
    # while the bounded audit is still working through the old live prefix.
    valid_missing_lease = _run(_parent_counters(seeded))
    db.add_all((*prefix, *invalid, valid_missing_lease))
    db.commit()
    dispatched: list[dict] = []
    monkeypatch.setattr(
        scoring_tasks.batch_score_role,
        "delay",
        lambda *_args, **kwargs: dispatched.append(kwargs),
    )

    first = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=1)
    second = scoring_backfill_recovery.reconcile_scoring_backfill_fanout(limit=1)

    db.expire_all()
    refreshed_invalid = [db.get(BackgroundJobRun, run.id) for run in invalid]
    valid_missing_lease = db.get(BackgroundJobRun, valid_missing_lease.id)
    assert first["created"] == first["published"] == 1
    assert first["parents_quarantined"] == 1
    assert second["parents_quarantined"] == 3
    assert all(run.status == "failed" for run in refreshed_invalid)
    assert {run.counters["fanout_quarantine_reason"] for run in refreshed_invalid} == {
        "invalid_backfill_parent",
        "invalid_role_plan",
        "invalid_fanout_complete",
        "invalid_future_fanout_lease_expires_at",
    }
    assert "backfill_recovery_audited_at" in prefix[0].counters
    assert valid_missing_lease.status == "running"
    assert valid_missing_lease.counters["fanout_complete"] is True
    assert len(dispatched) == 1
