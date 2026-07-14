"""Durability contracts for the automatic Bullhorn connect-time FULL sync."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.components.integrations.bullhorn import bootstrap, sync_runner
from app.components.integrations.bullhorn.errors import BullhornAuthError
from app.models.organization import Organization


def _connected_org(db) -> Organization:
    org = Organization(
        name="Bullhorn bootstrap",
        slug=f"bh-bootstrap-{uuid.uuid4().hex[:10]}",
        bullhorn_connected=True,
        bullhorn_username="api-user",
        bullhorn_client_id="client-id",
        bullhorn_client_secret="ciphertext",
        bullhorn_refresh_token="ciphertext",
    )
    db.add(org)
    db.commit()
    return org


def test_queue_failure_is_durable_and_recovered_without_manual_sync(
    db, monkeypatch
):
    org = _connected_org(db)
    intent = bootstrap.prepare_initial_full_sync(org)
    db.add(org)
    db.commit()

    monkeypatch.setattr(
        bootstrap,
        "_enqueue_initial_full_sync",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )
    signal = bootstrap.dispatch_initial_full_sync(
        db,
        org_id=org.id,
        intent=intent,
    )

    assert signal["status"] == "retry_pending"
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress["phase"] == "queued"
    assert fresh.bullhorn_sync_progress["last_dispatch_error"] == "queue_unavailable"
    assert fresh.bullhorn_sync_progress["dispatch_attempts"] == 1

    # More than the execution retry budget worth of broker failures must remain
    # recoverable: dispatch outages do not consume run_attempts.
    for expected_attempts in range(2, 9):
        result = bootstrap.recover_due_initial_syncs()
        assert result == {
            "status": "ok",
            "due": 1,
            "dispatched": 0,
            "deferred": 1,
            "failed": 0,
        }
        db.expire_all()
        fresh = db.query(Organization).filter(Organization.id == org.id).one()
        assert fresh.bullhorn_sync_progress["dispatch_attempts"] == expected_attempts
        assert fresh.bullhorn_sync_progress["run_attempts"] == 0

    dispatched: list[dict] = []
    monkeypatch.setattr(
        bootstrap,
        "_enqueue_initial_full_sync",
        lambda **kwargs: dispatched.append(kwargs),
    )
    result = bootstrap.recover_due_initial_syncs()

    assert result == {
        "status": "ok",
        "due": 1,
        "dispatched": 1,
        "deferred": 0,
        "failed": 0,
    }
    assert dispatched == [
        {
            "org_id": org.id,
            "run_id": intent.run_id,
            "mode": "full",
            "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        }
    ]
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress["dispatch_attempts"] == 9
    assert fresh.bullhorn_sync_progress["dispatch_status"] == "dispatching"


def test_failed_bootstrap_run_requeues_with_bounded_automatic_retry(db):
    org = _connected_org(db)
    org.bullhorn_sync_progress = {
        "phase": "job_orders",
        "mode": "full",
        "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        "run_id": "bootstrap-run",
        "dispatch_attempts": 1,
    }
    db.commit()

    sync_runner._finalize(db, org.id, completed=False, cancelled=False)

    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_last_sync_status == "failed"
    assert fresh.bullhorn_sync_progress["phase"] == "queued"
    assert fresh.bullhorn_sync_progress["dispatch_status"] == "retry_pending"
    assert fresh.bullhorn_sync_progress["last_run_error"] == "sync_failed"


def test_started_run_has_no_terminal_retry_cutoff_for_transient_failures(
    db, monkeypatch
):
    org = _connected_org(db)
    intent = bootstrap.prepare_initial_full_sync(org)
    org.bullhorn_sync_progress = {
        **org.bullhorn_sync_progress,
        "run_attempts": 50,
    }
    db.commit()
    dispatched: list[dict] = []
    monkeypatch.setattr(
        bootstrap,
        "_enqueue_initial_full_sync",
        lambda **kwargs: dispatched.append(kwargs),
    )

    signal = bootstrap.dispatch_initial_full_sync(
        db,
        org_id=org.id,
        intent=intent,
    )

    assert signal["status"] in {"queued", "running"}
    assert dispatched == [
        {
            "org_id": org.id,
            "run_id": intent.run_id,
            "mode": "full",
            "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        }
    ]
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress["run_attempts"] == 50
    assert fresh.bullhorn_sync_progress["dispatch_status"] == "dispatching"


def test_reconnect_replaces_stale_progress_with_dispatchable_full_sync(db):
    org = _connected_org(db)
    org.bullhorn_sync_progress = {
        "phase": "candidates",
        "mode": "full",
        "run_id": "dead-worker-run",
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
    }
    db.commit()

    intent = bootstrap.prepare_initial_full_sync(org)

    assert intent.should_dispatch is True
    assert intent.run_id != "dead-worker-run"
    assert org.bullhorn_sync_progress["phase"] == "queued"
    assert org.bullhorn_sync_progress["trigger"] == bootstrap.CONNECT_BOOTSTRAP_TRIGGER


def test_reconnect_adopts_fresh_progress_without_competing_dispatch(db):
    org = _connected_org(db)
    org.bullhorn_sync_progress = {
        "phase": "candidates",
        "mode": "full",
        "run_id": "live-worker-run",
        "trigger": "manual_sync",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()

    intent = bootstrap.prepare_initial_full_sync(org)

    assert intent == bootstrap.InitialSyncIntent(
        run_id="live-worker-run",
        should_dispatch=False,
    )
    assert org.bullhorn_sync_progress["trigger"] == "manual_sync"
    assert org.bullhorn_config["initial_sync_bootstrap"]["phase"] == "watching_active_full"


def test_reconnect_rekicks_its_own_queued_full_bootstrap(db):
    org = _connected_org(db)
    org.bullhorn_sync_progress = {
        "phase": "queued",
        "mode": "full",
        "run_id": "queued-bootstrap",
        "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dispatch_attempts": 3,
        "run_attempts": 0,
    }
    db.commit()

    intent = bootstrap.prepare_initial_full_sync(org)

    assert intent == bootstrap.InitialSyncIntent(
        run_id="queued-bootstrap",
        should_dispatch=True,
    )
    assert "initial_sync_bootstrap" not in (org.bullhorn_config or {})


def test_fresh_incremental_is_untouched_then_pending_full_sync_is_dispatched(
    db, monkeypatch
):
    org = _connected_org(db)
    incremental = {
        "phase": "candidates",
        "mode": "incremental",
        "run_id": "incremental-run",
        "trigger": "event_poll",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    org.bullhorn_sync_progress = incremental
    db.commit()

    intent = bootstrap.prepare_initial_full_sync(org)
    db.add(org)
    db.commit()

    assert intent.should_dispatch is False
    assert intent.run_id != "incremental-run"
    assert org.bullhorn_sync_progress == incremental
    pending = org.bullhorn_config["initial_sync_bootstrap"]
    assert pending["run_id"] == intent.run_id
    assert pending["mode"] == "full"
    assert pending["phase"] == "waiting_for_active_sync"
    assert bootstrap.initial_sync_status(org) == {
        "run_id": intent.run_id,
        "mode": "full",
        "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        "status": "queued",
        "phase": "waiting_for_active_sync",
        "dispatch_attempts": 0,
        "run_attempts": 0,
        "status_path": "/api/v1/bullhorn/sync/status",
    }

    # The unrelated run releases normally. Beat promotes the durable pending
    # marker and dispatches the required historical FULL walk automatically.
    org.bullhorn_sync_progress = None
    db.commit()
    dispatched: list[dict] = []
    monkeypatch.setattr(
        bootstrap,
        "_enqueue_initial_full_sync",
        lambda **kwargs: dispatched.append(kwargs),
    )

    result = bootstrap.recover_due_initial_syncs()

    assert result == {
        "status": "ok",
        "due": 1,
        "dispatched": 1,
        "deferred": 0,
        "failed": 0,
    }
    assert dispatched == [
        {
            "org_id": org.id,
            "run_id": intent.run_id,
            "mode": "full",
            "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        }
    ]
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress["mode"] == "full"
    assert fresh.bullhorn_sync_progress["run_id"] == intent.run_id
    assert "initial_sync_bootstrap" not in fresh.bullhorn_config


def test_completed_bootstrap_run_id_makes_recovery_delivery_idempotent(
    db, monkeypatch
):
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    org.bullhorn_config = {
        "initial_full_sync_run_id": "already-done",
        "initial_full_sync_status": "success",
    }
    db.commit()

    acquired = object()
    monkeypatch.setattr(sync_runner, "_acquire_mutex", lambda _org_id: acquired)
    monkeypatch.setattr(sync_runner, "_release_mutex", lambda _handle: None)
    monkeypatch.setattr(
        sync_runner,
        "_build_service",
        lambda _org: (_ for _ in ()).throw(AssertionError("must not run twice")),
    )

    sync_runner.execute_bullhorn_sync_run(
        org_id=org.id,
        mode="full",
        run_id="already-done",
        trigger=bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
    )

    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress is None
    assert fresh.bullhorn_config["initial_full_sync_run_id"] == "already-done"


def test_auth_failure_is_the_classified_hitl_terminal(db, monkeypatch):
    """Credential repair is terminal; ordinary execution failures are not."""
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    org.bullhorn_sync_progress = {
        "phase": "queued",
        "mode": "full",
        "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        "run_id": "auth-hitl-run",
        "dispatch_attempts": 1,
        "run_attempts": 0,
    }
    db.commit()
    acquired = object()
    monkeypatch.setattr(sync_runner, "_acquire_mutex", lambda _org_id: acquired)
    monkeypatch.setattr(sync_runner, "_release_mutex", lambda _handle: None)

    class _AuthFailedClient:
        def search_open_job_orders_complete(self, **_kwargs):
            raise BullhornAuthError("tokenized provider detail")

    monkeypatch.setattr(sync_runner, "_build_service", lambda _org: _AuthFailedClient())

    sync_runner.execute_bullhorn_sync_run(
        org_id=org.id,
        mode="full",
        run_id="auth-hitl-run",
        trigger=bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
    )

    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress is None
    assert fresh.bullhorn_last_sync_status == "failed"
    assert fresh.bullhorn_last_sync_summary["requires_human_action"] is True
    assert (
        fresh.bullhorn_last_sync_summary["failure_code"]
        == "bullhorn_reconnect_required"
    )
    assert "tokenized provider detail" not in str(fresh.bullhorn_last_sync_summary)
    assert fresh.bullhorn_config["initial_full_sync_status"] == "failed"
