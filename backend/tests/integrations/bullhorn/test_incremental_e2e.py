"""End-to-end smoke for the Bullhorn INCREMENTAL layer against the LIVE fake.

Covers the event-driven path + fallback sweep + reconciliation the beat tasks
drive (build plan §6), against the real ``BullhornService`` authed over the
uvicorn-backed fake:

* subscription create → gap-covering sweep signal (``created`` True);
* destructive event drain with requestId CHECKPOINT-BEFORE-PROCESSING;
* crash replay: a stored checkpoint replays the last batch via ``refetch_events``;
* event dirty-flag → re-fetch entity via the full-sync upsert (INSERTED/UPDATED);
* DELETED event → local mirror soft-deleted;
* local-write-wins: an inbound status doesn't clobber a just-written-back one;
* subscription expiry → detected on poll → recreate + gap sweep;
* dateLastModified fallback sweep + count reconciliation;
* hard-gate: flag-off / not-connected → runners no-op.

Only the transport is real; the fake's clock/counters are deterministic. Object
storage + Celery are unconfigured/eager, so CV store no-ops and the gated scoring
enqueue is off (roles are never starred) — no network/Anthropic calls.
"""

from __future__ import annotations

from app.components.integrations.bullhorn import events, reconcile
from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn.event_handlers import SUBSCRIBED_ENTITIES
from app.components.integrations.bullhorn.service import BullhornService
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import Role
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


def _org(db, *, sub_id: str | None = None) -> Organization:
    org = Organization(name="Bullhorn Incremental Org")
    if sub_id:
        org.bullhorn_event_subscription_id = sub_id
    db.add(org)
    db.commit()
    return org


def _authed_service(server, org_state) -> BullhornService:
    auth = BullhornAuth(
        username=org_state.username,
        client_id=org_state.client_id,
        client_secret=org_state.client_secret,
        refresh_token=None,
        persist_tokens=lambda **kw: None,
        discovery_url=server.discovery_url,
        password=org_state.password,
    )
    auth.authorize_with_password()
    return BullhornService(auth, client_id=org_state.client_id)


def _seed_open_submission(state: FakeBullhornState, bh_org, *, status: str):
    """Seed one open JobOrder + Candidate + JobSubmission; return (job, cand, sub)."""
    job = state.make_job_order(bh_org, title="Senior Engineer", is_open=True)
    cand = state.make_candidate(bh_org, name="Ada Lovelace", email="ada@example.com")
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status=status
    )
    return job, cand, sub


# --- subscription lifecycle + gap signal -------------------------------------


def test_ensure_subscription_creates_and_signals_gap(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc1", status_list=["New Lead"])
    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, created = events.ensure_subscription(db, org, client=client)
        # First time: created → gap sweep required.
        assert created is True
        assert org.bullhorn_event_subscription_id == sub_id
        # The subscription exists in the fake for exactly our entities.
        assert set(state.orgs["inc1"].subscriptions[sub_id].entity_names) == set(SUBSCRIBED_ENTITIES)
        # Second call: id already stored → trusted WITHOUT a destructive probe.
        sub_id2, created2 = events.ensure_subscription(db, org, client=client)
        assert sub_id2 == sub_id
        assert created2 is False


def test_dead_subscription_is_detected_on_poll_then_recreated(db):
    """An expired subscription surfaces as ``subscription_dead`` on poll; recreate
    reuses the stable id, clears the checkpoint, and starts a fresh queue."""
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc_exp", status_list=["New Lead"])
    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, created = events.ensure_subscription(db, org, client=client)
        assert created is True
        # Simulate a pending crash-checkpoint on the (about-to-die) subscription.
        org.bullhorn_event_request_id = "stale-req-id"
        db.commit()
        # Force the subscription past its 30-day expiry (poll now 404s).
        state.orgs["inc_exp"].subscriptions[sub_id].expired = True
        poll = events.poll_and_process_events(db, org, client=client)
        assert poll["status"] == "subscription_dead"

        # Recreate: same stable id, fresh queue, checkpoint dropped.
        new_sub_id = events.recreate_subscription(db, org, client=client)
        assert new_sub_id == sub_id
        assert org.bullhorn_event_request_id is None
        assert state.orgs["inc_exp"].subscriptions[sub_id].expired is False


# --- destructive poll + checkpoint-before-processing -------------------------


def test_event_insert_refetches_and_upserts_via_full_sync(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc2", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        # A JobOrder must exist locally for a JobSubmission event to attach; the
        # gap sweep would do this, but here we emit an explicit JobOrder event
        # first, then the JobSubmission event.
        state.emit_event(bh_org, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )
        result = events.poll_and_process_events(db, org, client=client)

    assert result["status"] == "ok"
    assert result["events"] >= 2
    # Role + application materialized from the events via the full-sync upserts.
    role = db.query(Role).filter(Role.organization_id == org.id).one()
    assert role.bullhorn_job_order_id == str(job["id"])
    app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
    assert app.bullhorn_job_submission_id == str(sub["id"])
    assert app.bullhorn_status == "New Lead"
    # Checkpoint cleared after a fully-processed drain.
    assert org.bullhorn_event_request_id is None
    # COST SAFETY: event ingest of a non-starred role enqueues no paid scoring.
    assert db.query(CvScoreJob).count() == 0


def test_crash_replay_reprocesses_last_batch_from_checkpoint(db):
    """A stored requestId replays the last batch via refetch_events (idempotent)."""
    state = FakeBullhornState()
    bh_org = state.make_org("inc3", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        org = _org(db)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        # Emit + drain once so the fake stamps a real requestId on last_batch.
        state.emit_event(bh_org, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="UPDATED"
        )
        first = events.poll_and_process_events(db, org, client=client)
        assert first["events"] >= 2
        # Simulate a crash AFTER checkpoint but BEFORE clearing: re-stamp the
        # checkpoint with the last requestId the fake served.
        last_request_id = state.orgs["inc3"].subscriptions[sub_id].last_request_id
        org.bullhorn_event_request_id = str(last_request_id)
        db.commit()

        # Next poll must REPLAY (refetch) that batch, not drain new events.
        replay = events.poll_and_process_events(db, org, client=client)

    assert replay["status"] == "ok"
    assert replay["batches"] >= 1  # the replayed batch counted
    # Idempotent: still exactly one role + one application (no dupes from replay).
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
    assert db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).count() == 1
    assert org.bullhorn_event_request_id is None  # checkpoint cleared after replay


# --- DELETED event → soft-delete ---------------------------------------------


def test_delete_event_soft_deletes_local_application(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc4", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        # Bring the application in first.
        state.emit_event(bh_org, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )
        events.poll_and_process_events(db, org, client=client)
        app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
        assert app.deleted_at is None

        # Now a DELETED event for that submission → soft-delete.
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="DELETED"
        )
        events.poll_and_process_events(db, org, client=client)

    db.refresh(app)
    assert app.deleted_at is not None  # soft-deleted (mirrors remote disappearance)


# --- Note event → agent-visible context --------------------------------------


def test_note_event_imports_agent_visible_context(db):
    """A Note event resolves its personReference and imports the note as context."""
    from app.models.candidate_application_event import CandidateApplicationEvent

    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc_note", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh_org, status="New Lead")
    # Seed a Note about the candidate (via the generic entity table).
    note_id = state._next()  # noqa: SLF001 — test seeding uses the state counter
    state._put_entity(  # noqa: SLF001
        bh_org,
        "Note",
        {
            "id": note_id,
            "comments": "Client wants to fast-track this one.",
            "action": "Other",
            "personReference": {"id": cand["id"]},
            "commentingPerson": {"name": "Jo Recruiter"},
            "dateAdded": state.now,
        },
    )

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        # Bring the application in, then fire the Note event.
        state.emit_event(bh_org, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )
        state.emit_event(bh_org, sub_id, entity_name="Note", entity_id=note_id, event_type="INSERTED")
        events.poll_and_process_events(db, org, client=client)

    app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
    note_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "recruiter_note",
        )
        .all()
    )
    assert len(note_events) == 1
    assert note_events[0].event_metadata.get("for_agent") is True
    assert note_events[0].event_metadata.get("source") == "bullhorn"


# --- local-write-wins ---------------------------------------------------------


def test_local_write_wins_blocks_stale_inbound_status(db):
    """An inbound event status does NOT clobber a status Taali just wrote back."""
    from datetime import datetime, timezone

    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc5", status_list=["New Lead", "Interview Scheduled"])
    job, cand, sub = _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        state.emit_event(bh_org, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )
        events.poll_and_process_events(db, org, client=client)
        app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()

        # Taali writes a status back locally (recruiter move) and stamps the guard.
        app.bullhorn_status = "Interview Scheduled"
        app.bullhorn_status_local_write_at = datetime.now(timezone.utc)
        db.commit()

        # Remote still says "New Lead"; an inbound UPDATED event must NOT revert.
        state.emit_event(
            bh_org, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="UPDATED"
        )
        events.poll_and_process_events(db, org, client=client)

    db.refresh(app)
    # Guard held: local write-back preserved, stale remote status ignored.
    assert app.bullhorn_status == "Interview Scheduled"


# --- fallback sweep + reconciliation -----------------------------------------


def test_sweep_modified_since_upserts_without_events(db):
    from datetime import datetime, timedelta, timezone

    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc6", status_list=["New Lead"])
    _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        result = reconcile.sweep_modified_since(db, org, client=client, since=since)

    assert result["status"] == "ok"
    assert result["job_orders"] == 1
    assert result["applications"] == 1
    assert db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).count() == 1


def test_reconcile_counts_flags_no_discrepancy_when_synced(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc7", status_list=["New Lead"])
    _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        from datetime import datetime, timedelta, timezone

        reconcile.sweep_modified_since(
            db, org, client=client, since=datetime.now(timezone.utc) - timedelta(days=1)
        )
        summary = reconcile.reconcile_counts(db, org, client=client)

    assert summary["ok"] is True
    assert summary["entities"]["job_orders"] == {"remote": 1, "local": 1}
    assert summary["entities"]["job_submissions"] == {"remote": 1, "local": 1}
    # Recorded on the org for visibility.
    assert org.bullhorn_last_sync_summary["reconciliation"]["ok"] is True


def test_reconcile_counts_surfaces_discrepancy(db):
    """Remote has an extra open JobOrder the local mirror hasn't ingested."""
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("inc8", status_list=["New Lead"])
    _seed_open_submission(state, bh_org, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        from datetime import datetime, timedelta, timezone

        reconcile.sweep_modified_since(
            db, org, client=client, since=datetime.now(timezone.utc) - timedelta(days=1)
        )
        # Add a NEW open JobOrder remotely but do NOT sweep it in.
        state.make_job_order(bh_org, title="Uningested Role", is_open=True)
        summary = reconcile.reconcile_counts(db, org, client=client)

    assert summary["ok"] is False
    assert summary["discrepancies"]["job_orders"] == {"remote": 2, "local": 1}


# --- hard-gate: flag-off is a no-op ------------------------------------------


def test_incremental_runners_noop_when_flag_off(db, monkeypatch):
    """BULLHORN_ENABLED False → both incremental runners no-op (no DB/API work)."""
    from app.components.integrations.bullhorn import incremental_runner
    from app.platform.config import settings

    monkeypatch.setattr(settings, "BULLHORN_ENABLED", False)
    org = _org(db)
    assert incremental_runner.execute_bullhorn_event_poll(org_id=org.id) == {
        "status": "skipped",
        "reason": "disabled",
    }
    assert incremental_runner.execute_bullhorn_reconcile(org_id=org.id) == {
        "status": "skipped",
        "reason": "disabled",
    }


def test_incremental_runner_noop_when_org_not_connected(db, monkeypatch):
    """Flag on but org not connected → runner no-ops (never touches credentials)."""
    from app.components.integrations.bullhorn import incremental_runner
    from app.platform.config import settings

    monkeypatch.setattr(settings, "BULLHORN_ENABLED", True)
    org = _org(db)  # bullhorn_connected defaults falsey
    assert incremental_runner.execute_bullhorn_event_poll(org_id=org.id) == {
        "status": "skipped",
        "reason": "not_connected",
    }
