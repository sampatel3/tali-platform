"""A dead Workable sync worker must not lock the org out of syncing for hours.

Incident (2026-06-05): a full sync run died mid-flight, leaving its row
``status='running'`` with a dead heartbeat. The in-progress guard then blocked
every subsequent 5-min sync, so new Workable comments stopped arriving — until
the 6h absolute reaper finally fired. Fix: recover on a STALE HEARTBEAT
(``updated_at``), not just total age, both in the on-demand kick-off path and the
periodic reaper.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.domains.workable_sync.routes import _finalize_stale_running_runs
from app.models.organization import Organization
from app.models.workable_sync_run import WorkableSyncRun
from tests.conftest import TestingSessionLocal


def _org(db) -> Organization:
    org = Organization(name="Reaper Org", slug=f"reap-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _run(db, org, *, started_min_ago, updated_min_ago, status="running"):
    now = datetime.now(timezone.utc)
    run = WorkableSyncRun(
        organization_id=org.id, mode="full", status=status, phase="syncing_candidates",
        jobs_total=1, jobs_processed=0, candidates_seen=0, candidates_upserted=0,
        applications_upserted=0, errors=[],
        started_at=now - timedelta(minutes=started_min_ago),
        updated_at=now - timedelta(minutes=updated_min_ago),
        finished_at=None,
    )
    db.add(run)
    db.flush()
    return run


def test_finalize_clears_stale_heartbeat_leaves_fresh(db):
    """The kick-off recovery path: a run with a dead heartbeat (>30m) is failed
    so a fresh sync can start; a still-heartbeating run is left running."""
    org = _org(db)
    stale = _run(db, org, started_min_ago=60, updated_min_ago=40)   # under 6h, dead beat
    fresh = _run(db, org, started_min_ago=60, updated_min_ago=2)    # alive

    cleared = _finalize_stale_running_runs(db, org.id)

    assert stale.id in cleared and fresh.id not in cleared
    assert stale.status == "failed" and stale.finished_at is not None
    assert fresh.status == "running"


@patch("app.platform.database.SessionLocal", TestingSessionLocal)
def test_reaper_task_clears_stale_heartbeat_run(db):
    """The periodic reaper finalizes a heartbeat-stale run even when it's well
    under the 6h absolute ceiling (the bug that locked the org out)."""
    org = _org(db)
    stale = _run(db, org, started_min_ago=90, updated_min_ago=45)
    fresh = _run(db, org, started_min_ago=90, updated_min_ago=3)
    db.commit()  # the task uses its own session — must see committed rows

    from app.tasks.assessment_tasks import reap_stuck_workable_sync_runs

    res = reap_stuck_workable_sync_runs()
    assert res["status"] == "ok" and res["reaped"] >= 1

    db.expire_all()
    assert db.get(WorkableSyncRun, stale.id).status == "failed"
    assert db.get(WorkableSyncRun, fresh.id).status == "running"
