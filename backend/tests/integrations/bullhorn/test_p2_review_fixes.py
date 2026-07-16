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

import pytest

from app.components.integrations.bullhorn import event_handlers, sync_jobs, sync_runner
from app.components.integrations.bullhorn.provider import BullhornProvider
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

        def sync_org(self, db, org, *, mode):
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


# ---------------------------------------------------------------------------
# Fix 2 — a closed JobOrder UPDATED event soft-deletes, not re-activates
# ---------------------------------------------------------------------------


class _FakeJobOrderClient:
    """Minimal client: returns a single JobOrder payload for search_job_orders."""

    def __init__(self, job_order: dict | None):
        self._job_order = job_order

    def search_job_orders(self, *, fields: str, query: str = "isOpen:true") -> list[dict]:
        return [self._job_order] if self._job_order is not None else []


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


def test_finalize_pre_screen_returns_none_on_failed_writeback(db, monkeypatch):
    """When the Bullhorn write-back fails, the pre-screen finalizer returns None
    (falls through to the caller's fallback), NOT a bullhorn_written result, and
    does not flip the local outcome to rejected."""
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

    assert outcome is None
    db.expire_all()
    fresh = db.query(CandidateApplication).filter(CandidateApplication.id == app.id).first()
    assert fresh.application_outcome == "open"
