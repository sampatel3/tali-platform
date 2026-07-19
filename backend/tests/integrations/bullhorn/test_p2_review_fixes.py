"""Regression tests for the three Codex P2 review fixes on the Bullhorn branch.

1. sync_runner: a second sync task that bails at the per-org lock check must NOT
   run finalization (it must not mark the holder's run failed or clear the
   holder's live ``bullhorn_sync_progress``).
2. event_handlers: a JobOrder UPDATED event for a just-closed job (isOpen=false)
   must soft-delete the local role, not re-activate it as an open role.
3. bullhorn auto-reject helpers: a failed provider write-back (needs_mapping /
   api_error) must NOT be treated as handled — no local-write success markers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from threading import Event

import pytest

from app.components.integrations.bullhorn import (
    event_handlers,
    reconcile,
    sync_candidates,
    sync_jobs,
    sync_runner,
)
from app.components.integrations.bullhorn.provider import BullhornProvider
from app.components.integrations.bullhorn.sync_service import (
    BullhornSyncCancelled,
    BullhornSyncIncomplete,
    BullhornSyncLeaseLost,
    BullhornSyncService,
)
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.services.role_change_audit import (
    ROLE_CHANGE_ACTION_RESTORED,
    ROLE_CHANGE_ACTION_SOFT_DELETED,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fix 1 — finalization is guarded on lock ownership
# ---------------------------------------------------------------------------


def _connected_org(db) -> Organization:
    org = Organization(
        name="BH Lock Org",
        slug=f"bh-{uuid.uuid4().hex[:10]}",
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )
    db.add(org)
    db.commit()
    return org


def test_second_runner_holding_lock_does_not_clear_holders_progress(db, monkeypatch):
    """A duplicate sync task that can't acquire the lock must leave the live
    run's status + progress marker untouched (no finalize)."""
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)

    org = _connected_org(db)
    # Simulate the live run: an in-flight progress marker + null last-sync status.
    org.bullhorn_sync_progress = {"phase": "candidates", "jobs_processed": 3}
    org.bullhorn_last_sync_status = None
    db.commit()

    # The lock is held by another task → _acquire_mutex returns None.
    monkeypatch.setattr(sync_runner, "_acquire_mutex", lambda org_id: None)

    finalize_calls: list[int] = []
    monkeypatch.setattr(
        sync_runner,
        "_finalize",
        lambda db, org_id, **kw: finalize_calls.append(org_id),
    )
    # The service should never be built — we bail before that.
    monkeypatch.setattr(
        sync_runner,
        "_build_service",
        lambda o: (_ for _ in ()).throw(AssertionError("must not build a service")),
    )

    sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    # Finalize was NOT called for the holder's run.
    assert finalize_calls == []
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).first()
    # The holder's live progress + status are untouched.
    assert fresh.bullhorn_sync_progress == {"phase": "candidates", "jobs_processed": 3}
    assert fresh.bullhorn_last_sync_status is None


def test_owning_runner_still_finalizes(db, monkeypatch):
    """The task that DOES acquire the lock finalizes as before (guard is
    ownership, not a blanket disable)."""
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)

    org = _connected_org(db)
    db.commit()

    monkeypatch.setattr(sync_runner, "_acquire_mutex", lambda org_id: object())
    monkeypatch.setattr(sync_runner, "_release_mutex", lambda handle: None)

    class _FakeSyncService:
        def __init__(self, *a, **k):
            pass

        def sync_org(self, db, org, *, mode, ownership_lost=None):
            assert ownership_lost is not None
            assert ownership_lost() is False
            return None

    monkeypatch.setattr(sync_runner, "_build_service", lambda o: object())
    monkeypatch.setattr(sync_runner, "BullhornSyncService", _FakeSyncService)

    finalize_calls: list[dict] = []
    monkeypatch.setattr(
        sync_runner,
        "_finalize",
        lambda db, org_id, **kw: finalize_calls.append({"org_id": org_id, **kw}),
    )

    sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    assert len(finalize_calls) == 1
    assert finalize_calls[0]["completed"] is True


class _FinalizationMutexClient:
    def __init__(self, current_owner: str) -> None:
        self.current_owner = current_owner

    def eval(self, _script, _num_keys, _key, expected_owner):
        return int(self.current_owner == expected_owner)


def _mutex_handle(*, current_owner: str, acquired_owner: str):
    return (
        _FinalizationMutexClient(current_owner),
        "bullhorn:lock:org",
        Event(),
        acquired_owner,
        Event(),
    )


def test_lease_lost_runner_preserves_replacement_run_state(db, monkeypatch):
    """A stale worker must not finalize progress committed by a new owner."""
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    stale_run_id = "stale-run"
    replacement_run_id = "replacement-run"
    org.bullhorn_sync_progress = {
        "phase": "queued",
        "run_id": stale_run_id,
        "trigger": "manual",
    }
    db.commit()

    handle = _mutex_handle(
        current_owner="replacement:owner",
        acquired_owner="stale:owner",
    )
    monkeypatch.setattr(sync_runner, "_acquire_mutex", lambda _org_id: handle)
    monkeypatch.setattr(sync_runner, "_release_mutex", lambda _handle: None)
    monkeypatch.setattr(sync_runner, "_build_service", lambda _org: object())

    class _LeaseLostAfterReplacementStarts:
        def __init__(self, _client):
            pass

        def sync_org(self, db_session, live_org, *, mode, ownership_lost=None):
            assert mode == "full"
            assert ownership_lost is not None
            live_org.bullhorn_sync_progress = {
                "phase": "candidates",
                "run_id": replacement_run_id,
                "jobs_processed": 7,
            }
            live_org.bullhorn_last_sync_status = "running"
            live_org.bullhorn_last_sync_summary = {
                "run_id": replacement_run_id,
                "status": "running",
                "replacement_sentinel": True,
            }
            db_session.commit()
            raise BullhornSyncLeaseLost()

    monkeypatch.setattr(
        sync_runner,
        "BullhornSyncService",
        _LeaseLostAfterReplacementStarts,
    )

    sync_runner.execute_bullhorn_sync_run(
        org_id=org.id,
        mode="full",
        run_id=stale_run_id,
        trigger="manual",
    )

    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress == {
        "phase": "candidates",
        "run_id": replacement_run_id,
        "jobs_processed": 7,
    }
    assert fresh.bullhorn_last_sync_status == "running"
    assert fresh.bullhorn_last_sync_summary == {
        "run_id": replacement_run_id,
        "status": "running",
        "replacement_sentinel": True,
    }


def test_finalizer_rechecks_exact_mutex_token_before_terminal_commit(db):
    """Heartbeat state alone cannot authorize a stale owner's terminal write."""
    org = _connected_org(db)
    run_id = "same-durable-run"
    progress = {
        "phase": "candidates",
        "run_id": run_id,
        "jobs_processed": 4,
    }
    org.bullhorn_sync_progress = progress
    org.bullhorn_last_sync_status = "running"
    db.commit()

    handle = _mutex_handle(
        current_owner="replacement:owner",
        acquired_owner="stale:owner",
    )

    finalized = sync_runner._finalize(
        db,
        org.id,
        completed=False,
        cancelled=False,
        expected_run_id=run_id,
        mutex_handle=handle,
    )

    assert finalized is False
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress == progress
    assert fresh.bullhorn_last_sync_status == "running"


def test_unknown_redis_lock_state_fails_closed(monkeypatch):
    """A Redis error is distinct from a held lock and must make the sync retry."""
    from app.tasks import assessment_tasks

    monkeypatch.setattr(
        assessment_tasks,
        "_acquire_workable_org_mutex",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(sync_runner.BullhornMutexUnavailable):
        sync_runner._acquire_mutex(42)


class _LeaseBoundaryClient:
    def __init__(self, jobs: list[dict], *, lose_after_submissions=None):
        self.jobs = jobs
        self.lose_after_submissions = lose_after_submissions
        self.open_reads = 0
        self.submission_reads: list[int] = []

    def search_open_job_orders_complete(self, *, fields):
        self.open_reads += 1
        return self.jobs

    def query_job_submissions_complete(self, *, job_order_id, fields):
        self.submission_reads.append(job_order_id)
        if self.lose_after_submissions is not None:
            self.lose_after_submissions["lost"] = True
        return []


def test_full_sync_lease_lost_before_first_provider_call(db):
    """A heartbeat loss observed before the walk makes zero provider calls."""
    org = _org(db)
    client = _LeaseBoundaryClient([])

    with pytest.raises(BullhornSyncLeaseLost):
        BullhornSyncService(client).sync_org(  # type: ignore[arg-type]
            db,
            org,
            ownership_lost=lambda: True,
        )

    assert client.open_reads == 0
    assert client.submission_reads == []


def test_full_sync_lease_lost_between_job_orders_stops_next_provider_call(
    db, monkeypatch
):
    """The stale holder finishes one safe item then never starts the second."""
    org = _org(db)
    client = _LeaseBoundaryClient(
        [
            {"id": 101, "title": "One", "isOpen": True},
            {"id": 102, "title": "Two", "isOpen": True},
        ]
    )
    service = BullhornSyncService(client)  # type: ignore[arg-type]
    lost = {"value": False}
    real_persist = service._persist_progress

    def _persist(db_session, org_obj, progress):
        real_persist(db_session, org_obj, progress)
        if progress.get("jobs_processed") == 1:
            lost["value"] = True

    monkeypatch.setattr(service, "_persist_progress", _persist)

    with pytest.raises(BullhornSyncLeaseLost):
        service.sync_org(db, org, ownership_lost=lambda: lost["value"])

    assert client.open_reads == 1
    assert client.submission_reads == [101]


def test_full_sync_lease_lost_after_provider_call_never_replays_in_stale_worker(db):
    """Loss during an external call is detected after it; the call runs once."""
    org = _org(db)
    lost = {"lost": False}
    client = _LeaseBoundaryClient(
        [{"id": 201, "title": "One", "isOpen": True}],
        lose_after_submissions=lost,
    )

    with pytest.raises(BullhornSyncLeaseLost):
        BullhornSyncService(client).sync_org(  # type: ignore[arg-type]
            db,
            org,
            ownership_lost=lambda: lost["lost"],
        )

    assert client.open_reads == 1
    assert client.submission_reads == [201]


class _CompleteEmptySubmissionClient(_LeaseBoundaryClient):
    def __init__(self, jobs: list[dict], *, fail_submission_page: bool = False):
        super().__init__(jobs)
        self.fail_submission_page = fail_submission_page

    def query_job_submissions_complete(self, *, job_order_id, fields):
        self.submission_reads.append(job_order_id)
        if self.fail_submission_page:
            raise RuntimeError("incomplete provider page")
        return []


def _active_bullhorn_application(db, org, *, job_order_id: str, submission_id: str):
    role = Role(
        organization_id=org.id,
        name="Remote role",
        source="bullhorn",
        bullhorn_job_order_id=job_order_id,
        bullhorn_job_data={"id": int(job_order_id), "isOpen": True},
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"missing-{uuid.uuid4().hex[:10]}@example.test",
        full_name="Missing submission",
        bullhorn_candidate_id="88001",
    )
    db.add_all([role, candidate])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        source="bullhorn",
        bullhorn_job_submission_id=submission_id,
    )
    db.add(app)
    db.commit()
    return app


def test_successful_full_sync_tombstones_submission_missing_from_complete_set(db):
    org = _org(db)
    app = _active_bullhorn_application(
        db,
        org,
        job_order_id="401",
        submission_id="501",
    )
    client = _CompleteEmptySubmissionClient(
        [{"id": 401, "title": "Remote role", "isOpen": True}]
    )

    progress = BullhornSyncService(client).sync_org(db, org)  # type: ignore[arg-type]

    db.refresh(app)
    assert progress["phase"] == "completed"
    assert progress["applications_deleted"] == 1
    assert app.deleted_at is not None


def test_failed_full_submission_page_never_tombstones_local_application(db):
    org = _org(db)
    app = _active_bullhorn_application(
        db,
        org,
        job_order_id="402",
        submission_id="502",
    )
    client = _CompleteEmptySubmissionClient(
        [{"id": 402, "title": "Remote role", "isOpen": True}],
        fail_submission_page=True,
    )

    with pytest.raises(BullhornSyncIncomplete):
        BullhornSyncService(client).sync_org(db, org)  # type: ignore[arg-type]

    db.refresh(app)
    assert app.deleted_at is None


def test_full_sync_user_cancellation_precedes_simultaneous_lease_loss(db):
    """Existing cancellation semantics remain distinct from lease failure."""
    org = _org(db)
    org.bullhorn_sync_progress = {"cancel_requested": True}
    db.commit()
    client = _LeaseBoundaryClient([])

    with pytest.raises(BullhornSyncCancelled):
        BullhornSyncService(client).sync_org(  # type: ignore[arg-type]
            db,
            org,
            ownership_lost=lambda: True,
        )

    assert client.open_reads == 0


class _ExactCandidateClient:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get_candidate_exact(self, candidate_id, *, fields):
        self.calls.append((str(candidate_id), fields))
        return {
            "id": int(candidate_id),
            "firstName": "Complete",
            "lastName": "Profile",
            "email": "complete@example.com",
            "phone": "555-0100",
        }


@pytest.mark.parametrize(
    "resolver_name",
    ["full", "reconcile", "event"],
)
def test_partial_nested_candidate_never_bypasses_exact_read(resolver_name):
    """Association expansions may contain a few profile fields but are not snapshots."""
    client = _ExactCandidateClient()
    submission = {
        "id": 7001,
        "candidate": {
            "id": 8001,
            "firstName": "Partial",
            "email": "partial@example.com",
        },
    }
    checkpoints: list[str] = []
    guard = lambda: checkpoints.append("checked")

    if resolver_name == "full":
        payload = BullhornSyncService(client)._resolve_candidate_payload(  # type: ignore[arg-type]
            submission,
            provider_guard=guard,
        )
    elif resolver_name == "reconcile":
        payload = reconcile._resolve_candidate_payload(  # type: ignore[attr-defined]
            client,
            submission,
            provider_guard=guard,
        )
    else:
        payload = event_handlers._resolve_candidate_payload(  # type: ignore[attr-defined]
            client,
            submission,
            provider_guard=guard,
        )

    assert payload["firstName"] == "Complete"
    assert payload["phone"] == "555-0100"
    assert client.calls == [("8001", sync_candidates.CANDIDATE_FIELDS)]
    assert checkpoints == ["checked", "checked"]


def test_full_sync_never_walks_submissions_for_workable_owned_role(db):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Workable authority",
        source="workable",
        workable_job_id="work-42",
        bullhorn_job_order_id="301",
        bullhorn_job_data={"id": 301, "isOpen": True, "title": "Old evidence"},
    )
    db.add(role)
    db.commit()
    client = _LeaseBoundaryClient(
        [{"id": 301, "title": "Bullhorn must not win", "isOpen": True}]
    )

    progress = BullhornSyncService(client).sync_org(db, org)  # type: ignore[arg-type]

    db.refresh(role)
    assert progress["authority_skipped"] == 1
    assert client.submission_reads == []
    assert role.name == "Workable authority"
    assert role.source == "workable"
    assert role.bullhorn_job_data["title"] == "Old evidence"


def test_workable_application_blocks_bullhorn_candidate_and_app_merge(db):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Dual role",
        source="bullhorn",
        bullhorn_job_order_id="9001",
    )
    candidate = Candidate(
        organization_id=org.id,
        email="workable@example.com",
        full_name="Workable Name",
        workable_candidate_id="work-candidate",
        bullhorn_candidate_id="8001",
    )
    db.add_all([role, candidate])
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        source="workable",
        workable_candidate_id="work-app",
        bullhorn_job_submission_id="7001",
        bullhorn_status="Workable status",
    )
    db.add(app)
    db.commit()

    result = sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission={
            "id": 7001,
            "jobOrder": {"id": 9001},
            "candidate": {"id": 8001},
            "status": "Bullhorn overwrite",
        },
        candidate_payload={
            "id": 8001,
            "name": "Bullhorn overwrite",
            "email": "bullhorn@example.com",
        },
        client=object(),  # blocked before any provider/CV call
        now=_now(),
    )

    assert result == {
        "candidate_upserted": 0,
        "application_upserted": 0,
        "authority_skipped": 1,
    }
    db.refresh(candidate)
    db.refresh(app)
    assert candidate.full_name == "Workable Name"
    assert candidate.email == "workable@example.com"
    assert app.source == "workable"
    assert app.bullhorn_status == "Workable status"


def test_bullhorn_linkage_evidence_does_not_overwrite_workable_candidate_profile(db):
    org = _org(db)
    candidate = Candidate(
        organization_id=org.id,
        email="workable-profile@example.com",
        full_name="Workable Profile",
        phone="+44 20 0000 0000",
        position="Workable Position",
        workable_candidate_id="work-profile",
        bullhorn_candidate_id="8101",
    )
    db.add(candidate)
    db.commit()

    resolved = sync_candidates._resolve_candidate(  # type: ignore[attr-defined]
        db,
        org,
        "8101",
        {
            "id": 8101,
            "email": "bullhorn-overwrite@example.com",
            "name": "Bullhorn Overwrite",
            "phone": "+1 555 9999",
            "occupation": "Bullhorn Position",
        },
    )

    assert resolved.id == candidate.id
    assert resolved.email == "workable-profile@example.com"
    assert resolved.full_name == "Workable Profile"
    assert resolved.phone == "+44 20 0000 0000"
    assert resolved.position == "Workable Position"
    assert resolved.bullhorn_data["name"] == "Bullhorn Overwrite"


@pytest.mark.parametrize("runner_name", ["event", "reconcile"])
def test_incremental_runner_lease_lost_before_provider_build_makes_no_call(
    db,
    monkeypatch,
    runner_name,
):
    from app.components.integrations.bullhorn import incremental_runner
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    monkeypatch.setattr(incremental_runner, "_acquire_mutex", lambda _org_id: object())
    monkeypatch.setattr(incremental_runner, "_release_mutex", lambda _handle: None)
    monkeypatch.setattr(incremental_runner, "_mutex_ownership_lost", lambda _handle: True)
    monkeypatch.setattr(
        incremental_runner,
        "_build_service",
        lambda _org: (_ for _ in ()).throw(
            AssertionError("lease loss must precede provider construction")
        ),
    )

    result = (
        incremental_runner.execute_bullhorn_event_poll(org_id=org.id)
        if runner_name == "event"
        else incremental_runner.execute_bullhorn_reconcile(org_id=org.id)
    )

    assert result == {"status": "retry_pending", "reason": "lease_lost"}


# ---------------------------------------------------------------------------
# Fix 2 — a closed JobOrder UPDATED event soft-deletes, not re-activates
# ---------------------------------------------------------------------------


class _FakeJobOrderClient:
    """Minimal client: returns a single JobOrder payload for search_job_orders."""

    def __init__(self, job_order: dict | None):
        self._job_order = job_order

    def get_job_order_exact(self, job_order_id, *, fields: str):
        if self._job_order is None:
            return None
        return self._job_order if str(self._job_order.get("id")) == str(job_order_id) else None


def _org(db) -> Organization:
    org = Organization(name="BH Close Org", slug=f"bhc-{uuid.uuid4().hex[:10]}")
    db.add(org)
    db.commit()
    return org


def test_update_event_for_closed_job_soft_deletes_role(db):
    """An UPDATED event whose re-fetched JobOrder is isOpen=false marks the local
    role closed (deleted_at set), instead of clearing deleted_at / re-activating."""
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Closed Role",
        source="bullhorn",
        bullhorn_job_order_id="777",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.commit()
    assert role.deleted_at is None

    client = _FakeJobOrderClient({"id": 777, "title": "Closed Role", "isOpen": False})
    outcome = event_handlers._handle_job_order(
        db, org, "777", client=client, now=_now()
    )

    assert outcome == "deleted_role"
    db.expire_all()
    fresh = db.query(Role).filter(Role.id == role.id).first()
    assert fresh.deleted_at is not None
    assert fresh.agentic_mode_enabled is False
    assert fresh.agent_paused_at is not None
    assert fresh.version == 2
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == ROLE_CHANGE_ACTION_SOFT_DELETED
    assert event.from_version == 1
    assert event.to_version == 2

    # A repeated close event must not consume another role revision.
    assert (
        event_handlers._handle_job_order(db, org, "777", client=client, now=_now())
        == "skipped"
    )
    db.refresh(fresh)
    assert fresh.version == 2
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .count()
        == 1
    )


def test_update_event_for_open_job_upserts_active_role(db):
    """Control: an isOpen=true JobOrder upserts as active (deleted_at cleared)."""
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Open Role",
        source="bullhorn",
        bullhorn_job_order_id="888",
        deleted_at=_now(),  # previously soft-deleted; a reopen restores it
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.commit()

    client = _FakeJobOrderClient({"id": 888, "title": "Open Role", "isOpen": True})
    outcome = event_handlers._handle_job_order(
        db, org, "888", client=client, now=_now()
    )

    assert outcome == "job_order"
    db.expire_all()
    fresh = db.query(Role).filter(Role.id == role.id).first()
    assert fresh.deleted_at is None
    assert fresh.agentic_mode_enabled is False
    assert fresh.agent_paused_at is not None
    assert "Bullhorn job restored" in (fresh.agent_paused_reason or "")
    assert fresh.version == 2
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == ROLE_CHANGE_ACTION_RESTORED
    assert event.from_version == 1
    assert event.to_version == 2


def test_complete_snapshot_close_stops_agent_and_audits_once(db):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Missing From Complete Snapshot",
        source="bullhorn",
        bullhorn_job_order_id="999",
        bullhorn_job_data={"id": 999, "isOpen": True},
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.commit()

    _open_ids, counts = sync_jobs.repair_roles_from_complete_open_snapshot(
        db,
        org,
        [],
        closed_at=_now(),
    )
    db.commit()

    assert counts["roles_closed"] == 1
    db.refresh(role)
    assert role.deleted_at is not None
    assert role.agentic_mode_enabled is False
    assert role.agent_paused_at is not None
    assert role.version == 2
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == ROLE_CHANGE_ACTION_SOFT_DELETED
    assert event.from_version == 1
    assert event.to_version == 2

    _open_ids, repeated = sync_jobs.repair_roles_from_complete_open_snapshot(
        db,
        org,
        [],
        closed_at=_now(),
    )
    db.commit()
    assert repeated["roles_closed"] == 0
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .count()
        == 1
    )


# ---------------------------------------------------------------------------
# Fix 3 — a failed Bullhorn write-back is NOT treated as handled
# ---------------------------------------------------------------------------


def _bullhorn_org(db) -> Organization:
    org = Organization(
        name="BH Reject Org",
        slug=f"bhr-{uuid.uuid4().hex[:10]}",
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )
    db.add(org)
    db.commit()
    return org


def _bullhorn_app(db, org, role) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id,
        email=f"c-{uuid.uuid4().hex[:10]}@x.test",
        full_name="C",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="bullhorn",
        bullhorn_job_submission_id="js-1",
    )
    db.add(app)
    db.flush()
    return app


def _seed_role(db, org) -> Role:
    role = Role(
        organization_id=org.id,
        name="BH Role",
        source="bullhorn",
        bullhorn_job_order_id="1",
        job_spec_text="Requirements\n- Python\n",
    )
    db.add(role)
    db.flush()
    return role


def _patch_provider(monkeypatch, module, org, db, *, result):
    """Route Bullhorn resolution to a provider whose reject_application returns
    ``result``. ``resolve_application_ats_provider`` is imported lazily inside
    the helper, so patch it at its source module."""
    import app.components.integrations.resolver as resolver_mod

    provider = BullhornProvider(org, db)
    monkeypatch.setattr(provider, "reject_application", lambda **kw: result)
    monkeypatch.setattr(
        resolver_mod,
        "resolve_application_ats_provider",
        lambda o, d, application: provider,
    )


def test_try_bullhorn_reject_needs_mapping_not_handled(db, monkeypatch):
    """A needs_mapping failure returns False (not handled) so the caller runs its
    fallback instead of marking the reject written."""
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    _patch_provider(
        monkeypatch,
        bar,
        org,
        db,
        result={"success": False, "code": "needs_mapping", "message": "no reject mapping"},
    )

    handled = bar.try_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type="agent",
        actor_id=None,
        reason="below threshold",
        trigger="auto_reject_pre_screen",
    )

    assert handled is False


def test_try_bullhorn_reject_api_error_not_handled(db, monkeypatch):
    """An api_error failure returns False (not handled)."""
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    _patch_provider(
        monkeypatch,
        bar,
        org,
        db,
        result={"success": False, "code": "api_error", "message": "502 from Bullhorn"},
    )

    handled = bar.try_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type="agent",
        actor_id=None,
        reason="below threshold",
        trigger="reject_cv_gap",
    )

    assert handled is False


def test_try_bullhorn_reject_unexpected_exception_fails_closed(db, monkeypatch):
    import app.components.integrations.resolver as resolver_mod
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    provider = BullhornProvider(org, db)

    def _boom(**_kwargs):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(provider, "reject_application", _boom)
    monkeypatch.setattr(
        resolver_mod,
        "resolve_application_ats_provider",
        lambda _o, _d, _application: provider,
    )

    handled = bar.try_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type="agent",
        actor_id=None,
        reason="below threshold",
        trigger="auto_reject_pre_screen",
    )
    assert handled is False
    assert app.application_outcome == "open"


def test_recruiter_bullhorn_reject_unknown_failure_is_retryable_and_secret_safe(
    db, monkeypatch, caplog
):
    import app.actions.reject_application as reject_action
    import app.components.integrations.resolver as resolver_mod
    from app.actions.types import Actor
    from app.services.workable_actions_service import WorkableWritebackError

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    provider = BullhornProvider(org, db)
    secret = "redis://:REJECT_SECRET@host"

    def _boom(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(provider, "reject_application", _boom)
    monkeypatch.setattr(
        resolver_mod,
        "resolve_application_ats_provider",
        lambda _o, _d, _application: provider,
    )

    with pytest.raises(WorkableWritebackError) as raised:
        reject_action._try_bullhorn_reject(
            db,
            app=app,
            org=org,
            actor=Actor.system(),
            reason="below threshold",
        )

    assert raised.value.code == "unexpected"
    assert raised.value.retriable is True
    assert secret not in str(raised.value)
    assert secret not in caplog.text


def test_try_bullhorn_reject_success_is_handled(db, monkeypatch):
    """Control: a successful write-back returns True (handled)."""
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    _patch_provider(
        monkeypatch,
        bar,
        org,
        db,
        result={"success": True, "code": "rejected", "config": {"remote_status": "Client Rejected"}},
    )

    handled = bar.try_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type="agent",
        actor_id=None,
        reason="below threshold",
        trigger="reject_cv_gap",
    )

    assert handled is True


def test_finalize_pre_screen_returns_explicit_failure_on_failed_writeback(db, monkeypatch):
    """A Bullhorn-owned failure cannot look like a non-Bullhorn application."""
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    role = _seed_role(db, org)
    app = _bullhorn_app(db, org, role)
    db.commit()

    _patch_provider(
        monkeypatch,
        bar,
        org,
        db,
        result={"success": False, "code": "api_error", "message": "boom"},
    )

    outcome = bar.finalize_pre_screen_bullhorn_reject(
        db,
        app=app,
        org=org,
        role=role,
        actor_type="agent",
        actor_id=None,
        decision={"reason": "below threshold", "snapshot": {}, "config": {}},
    )

    assert outcome["performed"] is False
    assert outcome["bullhorn_written"] is False
    assert outcome["bullhorn_writeback_failed"] is True
    db.expire_all()
    fresh = db.query(CandidateApplication).filter(CandidateApplication.id == app.id).first()
    assert fresh.application_outcome == "open"


def test_pre_screen_bullhorn_failure_with_mixed_ids_never_tries_workable(
    db, monkeypatch
):
    """Provider routing is exclusive even when legacy linkage has both IDs."""

    import app.services.application_automation_service as automation
    import app.services.bullhorn_auto_reject as bar

    org = _bullhorn_org(db)
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "mixed-provider"
    role = _seed_role(db, org)
    role.agentic_mode_enabled = True
    role.auto_reject = True
    role.auto_reject_pre_screen = True
    role.score_threshold = 50
    app = _bullhorn_app(db, org, role)
    app.workable_candidate_id = "legacy-workable-id"
    app.pre_screen_score_100 = 10
    app.genuine_pre_screen_score_100 = 10
    app.pre_screen_recommendation = "Below threshold"
    app.pre_screen_run_at = _now()
    db.commit()
    _patch_provider(
        monkeypatch,
        bar,
        org,
        db,
        result={"success": False, "code": "api_error", "message": "Bullhorn failed"},
    )
    decision = {
        "should_trigger": True,
        "state": "eligible",
        "reason": "Below threshold",
        "auto_disqualify_eligible": True,
        "config": {"threshold_100": 50},
        "snapshot": {"pre_screen_score": 10},
    }
    monkeypatch.setattr(automation, "evaluate_auto_reject_decision", lambda *a, **k: decision)
    workable = []
    monkeypatch.setattr(
        automation,
        "disqualify_candidate_in_workable",
        lambda **kwargs: workable.append(kwargs) or {"success": True},
    )

    result = automation.run_auto_reject_if_needed(
        db=db,
        org=org,
        app=app,
        role=role,
        actor_type="agent",
    )

    assert workable == []
    assert result["performed"] is False
    assert result["state"] == "awaiting_recruiter_approval"
    assert result["bullhorn_written"] is False
    assert app.application_outcome == "open"
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(app.id),
            AgentDecision.status == "pending",
        )
        .count()
        == 1
    )
