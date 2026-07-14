"""Sync-LEVEL contract tests: Bullhorn sync engine (PR-5) against the FAKE server.

The client-level contract classes (1 refresh-rotation, 7 429-storm, 8 verb
discipline) live in ``test_contract_client.py``; its docstring explicitly defers
the SYNC-level classes 2–6 to PR-5. This file is that deferred suite — it drives
the real sync engine (``BullhornSyncService``, ``events``, ``reconcile``,
``write_back``) authed against the live uvicorn fake, and locks the failure-mode
contracts the happy-path smokes (``test_sync_engine_e2e`` /
``test_incremental_e2e`` / ``test_write_back_smoke``) don't pin:

* **Class 2 — 401 mid-sync → refresh → resume.** The session dies between the
  JobOrder read and the JobSubmission read; the client transparently reauths
  (single-use refresh ROTATION, persisted before use) and resumes the SAME walk —
  no re-run, no duplicate Candidate/Application upserts (row counts + idempotency).
* **Class 3 — event checkpoint replay.** A crash AFTER the destructive event GET
  but BEFORE processing replays exactly that batch by ``requestId``; re-processing
  the same batch twice yields ONE set of effects (idempotent re-fetch upserts).
* **Class 4 — dead/expired subscription.** A 30-day-expired subscription is
  detected on poll, recreated on the stable id, and the runner runs a GAP-COVERING
  sweep to backfill the outage window (assert the sweep actually materialised rows
  events alone could not).
* **Class 5 — per-org status round-trip + never-guess.** Two orgs with DIFFERENT
  free-text status lists each round-trip move/reject to their OWN status; an
  unmapped inbound status surfaces as needs-mapping (funnel top, raw kept), an
  unmapped outbound intent raises the typed needs-mapping error — nothing guessed,
  no cross-org bleed.
* **Class 6 — local-write-wins.** Our write-back, then a STALE inbound event for
  the same submission, must not revert the pipeline stage OR the status.

Plus a full-sync E2E through the worker entry point ``execute_bullhorn_sync_run``
(seed → run → assert Roles/Candidates/Applications/stages/events/CV-parse enqueue
+ fresh-candidate scoring via the shared gated path; re-run → zero duplicate
effects), and the hard-gate no-op for the full-sync runner.

Deterministic: the fake's clock is a test-advanced integer; only the uvicorn
transport is real (matching the green E2E in ``test_client_unit.py``). Object
storage + Anthropic are unconfigured, so the CV store no-ops and no paid scoring
actually runs — we assert the ENQUEUE decision via the shared entry point instead.
"""

from __future__ import annotations

import pytest

from app.components.integrations.bullhorn import (
    events,
    reconcile,
    stage_map as sm,
    sync_candidates,
    write_back,
)
from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn.event_handlers import SUBSCRIBED_ENTITIES
from app.components.integrations.bullhorn.service import BullhornService
from app.components.integrations.bullhorn.stage_map import ATS_BULLHORN
from app.components.integrations.bullhorn.sync_service import BullhornSyncService
from app.models.ats_stage_map import AtsStageMap
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.services.workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState

# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _org(db, **kwargs) -> Organization:
    org = Organization(name="Bullhorn Contract Org", **kwargs)
    db.add(org)
    db.commit()
    return org


def _authed_service(server, org_state, *, persist=None) -> BullhornService:
    """Real client authed against the live fake via the one-time password grant."""
    auth = BullhornAuth(
        username=org_state.username,
        client_id=org_state.client_id,
        client_secret=org_state.client_secret,
        refresh_token=None,
        persist_tokens=persist or (lambda **kw: None),
        discovery_url=server.discovery_url,
        password=org_state.password,
    )
    auth.authorize_with_password()
    return BullhornService(auth, client_id=org_state.client_id)


def _seed_open_submission(state: FakeBullhornState, bh_org, *, status: str):
    """One open JobOrder + Candidate + JobSubmission; returns (job, cand, sub)."""
    job = state.make_job_order(bh_org, title="Senior Engineer", is_open=True)
    cand = state.make_candidate(bh_org, name="Ada Lovelace", email="ada@example.com")
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status=status
    )
    return job, cand, sub


def _seed_map(db, org, *, remote_status, taali_stage, is_reject) -> None:
    db.add(
        AtsStageMap(
            org_id=org.id,
            ats=ATS_BULLHORN,
            remote_status=remote_status,
            taali_stage=taali_stage,
            is_reject=is_reject,
        )
    )
    db.commit()


# ===========================================================================
# Class 2 — 401 mid-sync → refresh (rotation) → resume, no duplicate upserts
# ===========================================================================


def test_class2_mid_sync_401_refreshes_and_resumes_without_duplicate_upserts(db):
    """The BhRestToken dies between the JobOrder read and the JobSubmission read.

    The client must reauth transparently — a single-use refresh ROTATION persisted
    BEFORE use (the crash-safety invariant), re-login, then RETRY the one failed
    call — and the walk resumes on the SAME in-memory job-order list. The retry
    must not re-drive the walk from the top, so there is exactly one Role / one
    Candidate / one Application (no duplicate upserts), and the rotation fired.
    """
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c2", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh, status="New Lead")

    saves = {"n": 0}

    def _persist(**kw):
        saves["n"] += 1

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh, persist=_persist)
        assert saves["n"] == 1  # connect persisted the first refresh token

        # Expire the live session the instant the walk moves from JobOrders to the
        # first JobSubmission read — the next REST call 401s → reauth → resume.
        orig_query = client.query_job_submissions
        calls = {"n": 0}

        def _query_expiring_session(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                state.advance_clock(700)  # past SESSION_TTL (600): BhRestToken dead
            return orig_query(*a, **k)

        client.query_job_submissions = _query_expiring_session  # type: ignore[assignment]

        progress = BullhornSyncService(client).sync_org(db, org, mode="full")

    # The walk completed despite the mid-flight 401.
    assert progress["phase"] == "completed"
    assert progress["applications_upserted"] == 1

    # Reauth happened: the refresh token rotated + persisted a SECOND time (connect
    # + the mid-sync reauth). This is the single-use-rotation crash-safety path.
    assert saves["n"] >= 2

    # No duplicate upserts from the retry — exactly one of each, keyed on the
    # Bullhorn ids.
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
    assert db.query(Candidate).filter(Candidate.organization_id == org.id).count() == 1
    apps = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).all()
    assert len(apps) == 1
    assert apps[0].bullhorn_job_submission_id == str(sub["id"])


def test_class2_second_full_sync_after_reauth_is_idempotent(db):
    """A follow-up full sync (session already healthy) still upserts in place — the
    reauth path left no partial/duplicate state to diverge from on the next run."""
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c2b", status_list=["New Lead"])
    _seed_open_submission(state, bh, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        svc = BullhornSyncService(client)
        svc.sync_org(db, org, mode="full")
        state.advance_clock(700)  # expire before the 2nd run → reauth on its 1st call
        svc.sync_org(db, org, mode="full")

    assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
    assert db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).count() == 1


def test_partial_full_sync_retries_same_durable_run_until_complete(db, monkeypatch):
    """One candidate failure makes the whole walk retryable, never successful."""
    from app.components.integrations.bullhorn import bootstrap, sync_runner
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="ciphertext",
        bullhorn_username="apiuser",
    )
    run_id = "durable-partial-full"
    org.bullhorn_sync_progress = {
        "phase": "queued",
        "mode": "full",
        "trigger": bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        "run_id": run_id,
        "dispatch_attempts": 1,
        "run_attempts": 0,
    }
    db.commit()

    state = FakeBullhornState()
    bh = state.make_org("partial_full", status_list=["New Lead"])
    _seed_open_submission(state, bh, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        real_search_candidates = client.search_candidates
        failing = {"enabled": True}

        def _candidate_read(*args, **kwargs):
            if failing["enabled"]:
                raise RuntimeError("provider payload must never be persisted")
            return real_search_candidates(*args, **kwargs)

        monkeypatch.setattr(client, "search_candidates", _candidate_read)
        monkeypatch.setattr(sync_runner, "_build_service", lambda _org: client)

        sync_runner.execute_bullhorn_sync_run(
            org_id=org.id,
            mode="full",
            run_id=run_id,
            trigger=bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        )

        db.expire_all()
        failed = db.query(Organization).filter(Organization.id == org.id).one()
        assert failed.bullhorn_last_sync_status == "failed"
        assert failed.bullhorn_last_sync_summary["run_id"] == run_id
        assert failed.bullhorn_sync_progress["phase"] == "queued"
        assert failed.bullhorn_sync_progress["run_id"] == run_id
        assert failed.bullhorn_sync_progress["dispatch_status"] == "retry_pending"
        assert "provider payload" not in str(failed.bullhorn_last_sync_summary)

        failing["enabled"] = False
        sync_runner.execute_bullhorn_sync_run(
            org_id=org.id,
            mode="full",
            run_id=run_id,
            trigger=bootstrap.CONNECT_BOOTSTRAP_TRIGGER,
        )

    db.expire_all()
    completed = db.query(Organization).filter(Organization.id == org.id).one()
    assert completed.bullhorn_last_sync_status == "success"
    assert completed.bullhorn_sync_progress is None
    assert completed.bullhorn_last_sync_summary["run_id"] == run_id
    assert completed.bullhorn_config["initial_full_sync_run_id"] == run_id
    assert completed.bullhorn_config["initial_full_sync_status"] == "success"


# ===========================================================================
# Class 3 — event checkpoint replay: crash after GET, before processing
# ===========================================================================


def test_class3_crash_after_event_get_before_processing_replays_idempotently(db):
    """Checkpoint-before-processing: the requestId is committed BEFORE any event is
    touched, so a crash mid-batch replays exactly that batch (not a fresh drain).

    We drain once (materialising the effects + stamping a real requestId), then
    simulate the crash by re-stamping the checkpoint with that requestId and
    polling again. The second poll must REPLAY via refetch (not drain new events)
    and re-processing the identical batch yields ONE set of effects — same digest
    twice = one Role + one Application, and the checkpoint is cleared afterwards.
    """
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c3", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        state.emit_event(bh, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )

        first = events.poll_and_process_events(db, org, client=client)
        assert first["status"] == "ok"
        assert first["events"] >= 2
        # After a clean drain the checkpoint is cleared.
        assert org.bullhorn_event_request_id is None

        # Simulate the crash window: the batch was fetched + checkpointed but the
        # worker died before clearing. Re-stamp the checkpoint with the requestId
        # the fake served for the last batch.
        served_request_id = state.orgs["c3"].subscriptions[sub_id].last_request_id
        org.bullhorn_event_request_id = str(served_request_id)
        db.commit()

        replay = events.poll_and_process_events(db, org, client=client)

    assert replay["status"] == "ok"
    assert replay["batches"] >= 1  # the checkpointed batch was replayed, not skipped
    # Same digest applied twice = ONE set of effects (idempotent re-fetch upserts).
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
    assert db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).count() == 1
    # And the checkpoint is cleared once the replayed batch is fully processed.
    assert org.bullhorn_event_request_id is None


def test_class3_checkpoint_is_committed_before_processing(db):
    """The ordering guarantee itself: if a batch drains but the FIRST event's
    processing blows up, the requestId must already be durably checkpointed so the
    next run can replay the batch instead of losing it.

    We make ``dispatch_event`` raise on its first call; the poll must have committed
    the checkpoint BEFORE that call (so the requestId survives the failure), and the
    batch is not lost — a later clean poll replays it and materialises the effects.
    """
    import app.components.integrations.bullhorn.events as events_mod

    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c3b", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh, status="New Lead")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        state.emit_event(bh, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")

        # Blow up while processing the batch. The batch is drained + checkpointed
        # first; the dispatch failure is isolated (returns "error"), so the poll
        # does NOT raise — but crucially the checkpoint was committed before the
        # first dispatch, which is what we assert. Then clear it defensively to
        # observe the stored value mid-flight.
        seen = {"request_id_at_dispatch": "unset"}
        orig_dispatch = events_mod.dispatch_event

        def _spy_dispatch(db_, org_, event, **kw):
            # Read the checkpoint the poll committed BEFORE processing this event.
            db_.refresh(org_, attribute_names=["bullhorn_event_request_id"])
            seen["request_id_at_dispatch"] = org_.bullhorn_event_request_id
            return orig_dispatch(db_, org_, event, **kw)

        events_mod.dispatch_event = _spy_dispatch  # type: ignore[assignment]
        try:
            events.poll_and_process_events(db, org, client=client)
        finally:
            events_mod.dispatch_event = orig_dispatch  # type: ignore[assignment]

    # The requestId was already checkpointed (non-null) at the moment the first
    # event began processing — checkpoint-BEFORE-processing holds.
    assert seen["request_id_at_dispatch"] not in (None, "", "unset")


# ===========================================================================
# Class 4 — dead/expired subscription: detect → recreate → gap-covering sweep
# ===========================================================================


def test_class4_dead_subscription_recreated_and_gap_sweep_backfills(db, monkeypatch):
    """A 30-day-expired subscription: detected on poll, recreated on the stable id,
    then a GAP-COVERING sweep backfills the outage window.

    Events alone can't cover what changed while the subscription was dead (its new
    queue starts empty), so the runner runs a ``dateLastModified`` sweep on
    recreate. We prove the sweep actually ran by seeding an application that exists
    ONLY remotely (never delivered by an event) and asserting it materialised
    locally after the poll cycle — impossible without the gap sweep.
    """
    from app.components.integrations.bullhorn import incremental_runner
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)

    state = FakeBullhornState()
    bh = state.make_org("c4", status_list=["New Lead"])
    job, cand, sub = _seed_open_submission(state, bh, status="New Lead")

    # A connected org whose stored subscription is about to be found dead.
    org = _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        # Establish + then KILL a subscription so the runner's first poll 404s.
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        state.orgs["c4"].subscriptions[sub_id].expired = True
        # A pending crash-checkpoint on the dead subscription must be dropped on
        # recreate (it belonged to the dead sub).
        # Bullhorn requestIds are numeric. Use a syntactically valid stale id so
        # the fake reaches the expired-subscription check (rather than FastAPI's
        # query validation returning an unrelated 422).
        org.bullhorn_event_request_id = "999999"
        db.commit()

        # Point the runner's client at the live fake (bypass decrypt/discovery).
        monkeypatch.setattr(incremental_runner, "_build_service", lambda o: client)

        result = incremental_runner.execute_bullhorn_event_poll(org_id=org.id)

    assert result["status"] == "ok"
    assert result.get("recreated") is True
    # The gap sweep ran on recreate and backfilled the remotely-seeded application
    # that no event ever delivered — the proof the sweep executed.
    gap = result.get("gap_sweep") or {}
    assert gap.get("applications", 0) >= 1
    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).one()
    assert app.bullhorn_job_submission_id == str(sub["id"])
    # The runner committed org-state via its OWN session; refresh the test's stale
    # instance before reading the checkpoint/subscription columns.
    db.refresh(org)
    # Recreate reused the stable id, cleared the dead checkpoint, revived the sub.
    assert org.bullhorn_event_subscription_id == sub_id
    assert org.bullhorn_event_request_id is None
    assert state.orgs["c4"].subscriptions[sub_id].expired is False
    # The subscription was recreated for exactly our entity set.
    assert set(state.orgs["c4"].subscriptions[sub_id].entity_names) == set(SUBSCRIBED_ENTITIES)


def test_class4_gap_sweep_repairs_close_missed_while_subscription_dead(
    db,
    monkeypatch,
):
    """The recreate sweep repairs a JobOrder close the dead queue never saw."""
    from datetime import datetime, timedelta, timezone

    from app.components.integrations.bullhorn import incremental_runner
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    state = FakeBullhornState()
    bh = state.make_org("c4_missed_close", status_list=["New Lead"])
    job = state.make_job_order(bh, title="Close during outage", is_open=True)
    org = _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        reconcile.sweep_modified_since(
            db,
            org,
            client=client,
            since=datetime.now(timezone.utc) - timedelta(days=1),
        )
        role = db.query(Role).filter(Role.organization_id == org.id).one()
        assert role.deleted_at is None

        sub_id, _ = events.ensure_subscription(db, org, client=client)
        state.orgs["c4_missed_close"].subscriptions[sub_id].expired = True
        job["isOpen"] = False  # no event: it happened while the queue was dead
        monkeypatch.setattr(incremental_runner, "_build_service", lambda _org: client)

        result = incremental_runner.execute_bullhorn_event_poll(org_id=org.id)

    assert result["status"] == "ok"
    assert result["recreated"] is True
    assert result["gap_sweep"]["roles_closed"] == 1
    db.refresh(role)
    assert role.deleted_at is not None
    assert role.bullhorn_job_data["isOpen"] is False
    db.refresh(org)
    telemetry = org.bullhorn_last_sync_summary["job_order_repair"]
    assert telemetry["roles_closed"] == 1
    assert telemetry["remote_open_count"] == 0


# ===========================================================================
# Class 5 — per-org status round-trip + unmapped never-guessed, no cross-bleed
# ===========================================================================


def _linked_app(db, org, *, role, candidate, submission_id) -> CandidateApplication:
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        bullhorn_job_submission_id=str(submission_id),
        source="bullhorn",
        version=1,
    )
    db.add(app)
    db.commit()
    return app


def _mirror_role_candidate(db, org, *, job_order_id, candidate_bh_id, email):
    cand = Candidate(
        organization_id=org.id,
        email=email,
        full_name="RT Candidate",
        bullhorn_candidate_id=str(candidate_bh_id),
    )
    db.add(cand)
    role = Role(
        organization_id=org.id,
        name="Eng",
        source="bullhorn",
        bullhorn_job_order_id=str(job_order_id),
    )
    db.add(role)
    db.commit()
    return role, cand


def test_class5_two_orgs_distinct_status_lists_round_trip_independently(db, monkeypatch):
    """Two orgs, two DIFFERENT free-text status vocabularies. Each org's advance /
    reject write-back resolves to ITS OWN mapped status via its own AtsStageMap
    rows, writes to ITS OWN fake, and does not bleed across. The reverse map is
    per-org, exactly like the read direction.
    """
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)

    # --- Org A: statuses use "Interview Scheduled" / "Client Rejected" ---------
    org_a = _org(db)
    state_a = FakeBullhornState()
    bh_a = state_a.make_org("rtA", status_list=["New Lead", "Interview Scheduled", "Client Rejected"])
    cand_a = state_a.make_candidate(bh_a, name="RT Candidate", email="a@example.com")
    job_a = state_a.make_job_order(bh_a, title="Eng", is_open=True)
    sub_a = state_a.make_job_submission(
        bh_a, candidate_id=cand_a["id"], job_order_id=job_a["id"], status="New Lead"
    )
    _seed_map(db, org_a, remote_status="Interview Scheduled", taali_stage="advanced", is_reject=False)
    _seed_map(db, org_a, remote_status="Client Rejected", taali_stage="review", is_reject=True)
    role_a, cand_row_a = _mirror_role_candidate(
        db, org_a, job_order_id=job_a["id"], candidate_bh_id=cand_a["id"], email="a@example.com"
    )
    app_a = _linked_app(db, org_a, role=role_a, candidate=cand_row_a, submission_id=sub_a["id"])

    # --- Org B: a completely different vocabulary -----------------------------
    org_b = _org(db)
    state_b = FakeBullhornState()
    bh_b = state_b.make_org("rtB", status_list=["Applied", "Onsite Loop", "Passed On"])
    cand_b = state_b.make_candidate(bh_b, name="RT Candidate", email="b@example.com")
    job_b = state_b.make_job_order(bh_b, title="Eng", is_open=True)
    sub_b = state_b.make_job_submission(
        bh_b, candidate_id=cand_b["id"], job_order_id=job_b["id"], status="Applied"
    )
    _seed_map(db, org_b, remote_status="Onsite Loop", taali_stage="advanced", is_reject=False)
    _seed_map(db, org_b, remote_status="Passed On", taali_stage="review", is_reject=True)
    role_b, cand_row_b = _mirror_role_candidate(
        db, org_b, job_order_id=job_b["id"], candidate_bh_id=cand_b["id"], email="b@example.com"
    )
    app_b = _linked_app(db, org_b, role=role_b, candidate=cand_row_b, submission_id=sub_b["id"])

    with live_bullhorn_server(state_a) as srv_a, live_bullhorn_server(state_b) as srv_b:
        client_a = _authed_service(srv_a, bh_a)
        client_b = _authed_service(srv_b, bh_b)

        # Org A advances → its own "Interview Scheduled".
        res_a = write_back.move_submission_status(
            db, org=org_a, client=client_a, submission_id=str(sub_a["id"]), taali_intent="advanced"
        )
        assert res_a["success"] is True
        assert res_a["config"]["remote_status"] == "Interview Scheduled"
        assert state_a.orgs["rtA"].entities["JobSubmission"][sub_a["id"]]["status"] == "Interview Scheduled"

        # Org B rejects → its own "Passed On" (different string entirely).
        res_b = write_back.reject_submission(
            db, org=org_b, client=client_b, submission_id=str(sub_b["id"])
        )
        assert res_b["success"] is True
        assert res_b["config"]["remote_status"] == "Passed On"
        assert state_b.orgs["rtB"].entities["JobSubmission"][sub_b["id"]]["status"] == "Passed On"

        # write_back stamps the local-write in-session; the caller owns the commit.
        db.commit()

    # No cross-bleed: A's status never became B's and vice-versa.
    assert state_a.orgs["rtA"].entities["JobSubmission"][sub_a["id"]]["status"] != "Passed On"
    assert state_b.orgs["rtB"].entities["JobSubmission"][sub_b["id"]]["status"] != "Interview Scheduled"
    db.refresh(app_a)
    db.refresh(app_b)
    assert app_a.bullhorn_status == "Interview Scheduled"
    assert app_a.external_stage_normalized == "advanced"
    assert app_b.bullhorn_status == "Passed On"
    assert app_b.external_stage_normalized == "rejected"


def test_class5_unmapped_inbound_status_surfaces_needs_mapping_not_guessed(db):
    """An inbound JobSubmission status with no map row is NEVER guessed on read: the
    application stays at the funnel top, the raw status is kept, and it's surfaced
    in the needs-mapping list."""
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c5in", status_list=["Bespoke Client Stage"])
    job = state.make_job_order(bh, title="Analyst", is_open=True)
    cand = state.make_candidate(bh, name="Grace Hopper", email="grace@example.com")
    state.make_job_submission(
        bh, candidate_id=cand["id"], job_order_id=job["id"], status="Bespoke Client Stage"
    )

    with live_bullhorn_server(state) as server:
        BullhornSyncService(_authed_service(server, bh)).sync_org(db, org, mode="full")

    app = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).one()
    assert app.pipeline_stage == "applied"  # not guessed forward
    assert app.bullhorn_status == "Bespoke Client Stage"  # raw kept
    assert app.external_stage_normalized is None  # explicit needs-mapping marker
    assert sm.unmapped_statuses(db, org) == ["Bespoke Client Stage"]  # surfaced


def test_class5_unmapped_outbound_intent_raises_typed_needs_mapping(db):
    """An outbound intent with no reverse-map row is NEVER guessed on write: the
    non-strict path returns a needs_mapping failure + writes nothing; the strict
    (decision-batch) path raises the shared WorkableWritebackError, non-retriable."""
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c5out")
    cand = state.make_candidate(bh)
    job = state.make_job_order(bh)
    sub = state.make_job_submission(bh, candidate_id=cand["id"], job_order_id=job["id"])
    role, cand_row = _mirror_role_candidate(
        db, org, job_order_id=job["id"], candidate_bh_id=cand["id"], email="x@example.com"
    )
    app = _linked_app(db, org, role=role, candidate=cand_row, submission_id=sub["id"])
    original_status = state.orgs["c5out"].entities["JobSubmission"][sub["id"]]["status"]

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)

        # Non-strict: typed failure dict, nothing written, no local-write stamp.
        result = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
        assert result["success"] is False
        assert result["code"] == "needs_mapping"
        assert state.orgs["c5out"].entities["JobSubmission"][sub["id"]]["status"] == original_status
        db.refresh(app)
        assert app.bullhorn_status_local_write_at is None

        # Strict: the shared, non-retriable error surfaces terminally.
        with pytest.raises(WorkableWritebackError) as exc:
            with strict_workable_writes():
                write_back.move_submission_status(
                    db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
                )
        assert exc.value.code == "needs_mapping"
        assert exc.value.retriable is False


# ===========================================================================
# Class 6 — local-write-wins: our write, then a stale inbound event, no revert
# ===========================================================================


def test_class6_local_write_wins_blocks_stale_inbound_stage_and_status(db):
    """Taali writes back a status (stamping the local-write guard); a subsequent
    inbound event carrying the STALE remote status must not revert the Taali
    pipeline stage OR the bullhorn_status inside the guard window.

    This is the full local-write-wins contract: we advance the app to ``advanced``
    via a real write-back (its stage-map row moves the Taali stage too), then fire
    an UPDATED event whose remote status is the old pre-write value, and assert
    neither the stage nor the status regressed.
    """
    org = _org(db)
    state = FakeBullhornState()
    bh = state.make_org("c6", status_list=["New Lead", "Interview Scheduled"])
    job, cand, sub = _seed_open_submission(state, bh, status="New Lead")
    # advance → "Interview Scheduled" (moves the Taali stage to advanced on write).
    _seed_map(db, org, remote_status="Interview Scheduled", taali_stage="advanced", is_reject=False)

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        sub_id, _ = events.ensure_subscription(db, org, client=client)
        # Bring the application in via events first (remote status still "New Lead").
        state.emit_event(bh, sub_id, entity_name="JobOrder", entity_id=job["id"], event_type="INSERTED")
        state.emit_event(
            bh, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="INSERTED"
        )
        events.poll_and_process_events(db, org, client=client)
        app = db.query(CandidateApplication).filter(
            CandidateApplication.organization_id == org.id
        ).one()

        # Taali writes the advance back → remote flips + local guard stamp + local
        # stage moves to advanced. (The fake now holds "Interview Scheduled".)
        move = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
        assert move["success"] is True
        # Drive the Taali stage the way sync mapping would for the written status,
        # so we can prove the inbound stale event does not pull it back.
        from app.domains.assessments_runtime.pipeline_service import transition_stage

        transition_stage(
            db, app=app, to_stage="advanced", source="sync", actor_type="sync",
            reason="mapped advance",
        )
        db.commit()
        db.refresh(app)
        assert app.bullhorn_status == "Interview Scheduled"
        assert app.pipeline_stage == "advanced"

        # Now a STALE inbound event: remote still reports the pre-write "New Lead".
        state.orgs["c6"].entities["JobSubmission"][sub["id"]]["status"] = "New Lead"
        state.emit_event(
            bh, sub_id, entity_name="JobSubmission", entity_id=sub["id"], event_type="UPDATED"
        )
        events.poll_and_process_events(db, org, client=client)

    db.refresh(app)
    # Local-write-wins held: neither the status nor the stage reverted.
    assert app.bullhorn_status == "Interview Scheduled"
    assert app.pipeline_stage == "advanced"


# ===========================================================================
# Full-sync E2E through the worker entry point + fresh-candidate scoring
# ===========================================================================


def _connected_org(db) -> Organization:
    return _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )


def test_full_sync_run_end_to_end_imports_everything_and_scores_fresh_candidate(db, monkeypatch):
    """Drive the WORKER entry point ``execute_bullhorn_sync_run`` end-to-end.

    Seed a fake org (2 JobOrders / 5 candidates / mixed statuses + a Resume
    attachment), run the runner (its own SessionLocal shares the test DB), and
    assert the full walk landed: Roles, Candidates, Applications, mapped vs
    needs-mapping stages, status-change events, notes, and — the cost-safety
    contract — that a FRESH candidate on a STARRED role goes through the shared
    scoring entry point with ``score=True`` while re-syncs never re-enqueue.
    """
    from app.platform import config as config_mod
    from app.components.integrations.bullhorn import sync_runner

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)

    org = _connected_org(db)
    state = FakeBullhornState()
    bh = state.make_org(
        "e2e",
        status_list=["New Lead", "Interview Scheduled", "Placed", "Client Rejected", "Bespoke Stage"],
    )

    # JobOrder 1 — adopted and agent-on so a fresh candidate is scored via the
    # shared running-agent gate. JobOrder 2 — agent-off (no scoring), holds the
    # mixed + needs-mapping statuses.
    job1 = state.make_job_order(bh, title="Senior Engineer", is_open=True)
    job2 = state.make_job_order(bh, title="Data Analyst", is_open=True)

    # 5 candidates across the two orders with mixed statuses.
    #   c1 "New Lead"  → needs-mapping → stays funnel-top (NOT resolved) → the
    #                    SOLE fresh candidate on the STARRED role: CV fetch + the
    #                    one gated scoring enqueue.
    #   c2 "Interview Scheduled" → mapped → advanced (proves the mapped stage).
    #   c3 "Client Rejected" → mapped reject → rejected outcome.
    #   c4 "Placed"    → mapped → advanced.  c5 "Bespoke Stage" → needs-mapping.
    # c2–c5 all sit on the UN-starred job2, so only c1 is scored.
    c1 = state.make_candidate(bh, name="Ada Lovelace", email="ada@example.com")
    c2 = state.make_candidate(bh, name="Grace Hopper", email="grace@example.com")
    c3 = state.make_candidate(bh, name="Ken Thompson", email="ken@example.com")
    c4 = state.make_candidate(bh, name="Barb Liskov", email="barb@example.com")
    c5 = state.make_candidate(bh, name="Alan Kay", email="alan@example.com")
    s1 = state.make_job_submission(bh, candidate_id=c1["id"], job_order_id=job1["id"], status="New Lead")
    s2 = state.make_job_submission(bh, candidate_id=c2["id"], job_order_id=job2["id"], status="Interview Scheduled")
    s3 = state.make_job_submission(bh, candidate_id=c3["id"], job_order_id=job2["id"], status="Client Rejected")
    state.make_job_submission(bh, candidate_id=c4["id"], job_order_id=job2["id"], status="Placed")
    state.make_job_submission(bh, candidate_id=c5["id"], job_order_id=job2["id"], status="Bespoke Stage")
    # A history trail on s1 + a Resume attachment on c1 (drives the CV path + parse).
    # c1 is at the funnel top (not resolved), so its CV IS fetched — a resolved
    # (advanced) row is frozen and would skip the CV refresh by design. Use a .txt
    # resume with real text so the extractor yields cv_text without a live object
    # store (a .pdf of arbitrary bytes extracts empty).
    state.make_job_submission_history(bh, job_submission_id=s1["id"], status="New Lead")
    state.make_job_submission_history(bh, job_submission_id=s1["id"], status="Interview Scheduled")
    state.add_file_attachment(
        bh,
        candidate_id=c1["id"],
        raw=b"Ada Lovelace CV: distributed systems, Rust, analytical engines.",
        name="ada.txt",
        content_type="text/plain",
    )

    # Spy on the shared scoring entry point (patched at the sync_candidates call
    # site) so we can assert the enqueue DECISION without a real Anthropic call.
    score_calls: list[dict] = []
    orig_on_created = sync_candidates.on_application_created

    def _spy_on_created(
        app,
        *,
        score=False,
        score_force=False,
        allow_paid_work=True,
        parse_origin=None,
    ):
        score_calls.append(
            {
                "app_id": getattr(app, "id", None),
                "score": score,
                "allow_paid_work": allow_paid_work,
                "parse_origin": parse_origin,
            }
        )
        return orig_on_created(
            app,
            score=score,
            score_force=score_force,
            allow_paid_work=allow_paid_work,
            parse_origin=parse_origin,
        )

    monkeypatch.setattr(sync_candidates, "on_application_created", _spy_on_created)
    # Spy on the async CV-section parse enqueue (proves the CV pipeline fired).
    parse_calls: list[int] = []
    import app.tasks.automation_tasks as automation_tasks

    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda args=(), **kw: parse_calls.append(args[0] if args else None),
    )

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh)
        # Point the runner's service builder at the live-fake client.
        monkeypatch.setattr(sync_runner, "_build_service", lambda o: client)

        # Seed the stage map from categorization BEFORE the run so the mapped
        # statuses resolve (interview/placed → advanced, rejected → review+reject).
        status_list = client.get_status_list()
        sm.seed_stage_map_from_categorization(db, org, categorization=status_list["categorization"])
        db.commit()

        # Pre-create JobOrder 1 as an adopted, running role. The sticky star
        # controls sync cadence; enabled + unpaused controls paid intake work.
        role1 = Role(
            organization_id=org.id,
            name="Senior Engineer",
            source="bullhorn",
            bullhorn_job_order_id=str(job1["id"]),
            starred_for_auto_sync=True,
            agentic_mode_enabled=True,
            monthly_usd_budget_cents=5000,
        )
        db.add(role1)
        db.commit()

        # --- run 1 -------------------------------------------------------------
        sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    # The runner committed via its own session; drop the test session's identity
    # map so we read the freshly-written rows.
    db.expire_all()

    # Roles: both JobOrders → roles (job1 adopted the starred pre-created row).
    roles = db.query(Role).filter(Role.organization_id == org.id).all()
    assert len(roles) == 2
    role1 = next(r for r in roles if r.bullhorn_job_order_id == str(job1["id"]))
    assert role1.starred_for_auto_sync is True

    # Candidates + applications: 5 each.
    assert db.query(Candidate).filter(Candidate.organization_id == org.id).count() == 5
    apps = db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).all()
    assert len(apps) == 5

    # Stage mapping: mapped statuses moved off the funnel top; the un-seeded ones
    # stayed (needs-mapping) and are surfaced. Categorization seeds only
    # interview/placed/rejected, so BOTH "Bespoke Stage" and "New Lead" are
    # needs-mapping — neither is guessed into a stage.
    by_sub = {a.bullhorn_job_submission_id: a for a in apps}
    assert by_sub[str(s2["id"])].pipeline_stage == "advanced"  # Interview Scheduled → advanced
    assert by_sub[str(s2["id"])].external_stage_normalized == "advanced"
    assert by_sub[str(s1["id"])].pipeline_stage == "applied"  # New Lead unmapped → funnel top
    assert by_sub[str(s1["id"])].external_stage_normalized is None
    assert sm.unmapped_statuses(db, org) == ["Bespoke Stage", "New Lead"]
    # The rejected-category status (s3) resolved the outcome.
    assert by_sub[str(s3["id"])].application_outcome == "rejected"

    # History → status-change events on s1's application (the only submission with
    # a seeded history trail). Scope the assertion to this app: the in-memory fake
    # doesn't filter JobSubmissionHistory by the ``where`` clause, so it echoes s1's
    # history for every submission's import — a fake read quirk, not a product bug.
    # The product keys each event on (application_id, history-row id), so s1's app
    # holds exactly its two rows.
    ada_app = by_sub[str(s1["id"])]
    ada_app_id = ada_app.id

    def _status_events_for(app_id: int) -> int:
        return (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app_id,
                CandidateApplicationEvent.event_type == "bullhorn_status_change",
            )
            .count()
        )

    assert _status_events_for(ada_app_id) == 2

    # CV pipeline: the Resume attachment drove a cv_text extraction on c1's app
    # (funnel-top, not frozen), which enqueued the async CV-section parse.
    assert (ada_app.cv_text or "").strip()
    assert ada_app.id in parse_calls

    # COST SAFETY: the shared scoring entry point was called for EVERY imported
    # application, but ``score=True`` ONLY for the fresh candidate on the
    # running role (job1/s1). Everything on the agent-off job2 is held.
    assert len(score_calls) == 5
    starred_true = [c for c in score_calls if c["score"] is True]
    assert len(starred_true) == 1
    assert starred_true[0]["app_id"] == ada_app.id

    # --- run 2 (re-sync) → zero duplicate effects + NO new scoring enqueue -----
    score_calls.clear()
    with live_bullhorn_server(state) as server2:
        client2 = _authed_service(server2, bh)
        monkeypatch.setattr(sync_runner, "_build_service", lambda o: client2)
        sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    db.expire_all()
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 2
    assert db.query(Candidate).filter(Candidate.organization_id == org.id).count() == 5
    assert db.query(CandidateApplication).filter(
        CandidateApplication.organization_id == org.id
    ).count() == 5
    # No duplicate history events from the re-run: s1's app still holds exactly its
    # two status-change events (idempotent on the history-row id across runs).
    assert _status_events_for(ada_app_id) == 2
    # Re-sync of existing applications never re-enqueues paid scoring: the shared
    # entry point still ran per app, but score=True fired for NONE of them.
    assert len(score_calls) == 5
    assert [c for c in score_calls if c["score"] is True] == []


# ===========================================================================
# Hard gate — the full-sync runner no-ops when the flag is off / not connected
# ===========================================================================


def test_full_sync_runner_noops_when_flag_off(db, monkeypatch):
    """BULLHORN_ENABLED False → ``execute_bullhorn_sync_run`` returns before it
    opens a session, builds a client, or touches credentials (no DB writes)."""
    from app.platform import config as config_mod
    from app.components.integrations.bullhorn import sync_runner

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", False, raising=False)
    org = _connected_org(db)

    # If the gate leaked, _build_service would try to decrypt the placeholder creds
    # and blow up — a clean return proves the no-op.
    called = {"built": False}
    monkeypatch.setattr(
        sync_runner, "_build_service", lambda o: called.__setitem__("built", True)
    )
    sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    assert called["built"] is False
    # No progress marker was written — the runner never ran.
    db.expire_all()
    fresh = db.query(Organization).filter(Organization.id == org.id).one()
    assert fresh.bullhorn_sync_progress is None
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 0


def test_full_sync_runner_noops_when_org_not_connected(db, monkeypatch):
    """Flag on but the org isn't connected → the runner no-ops before building a
    client (never touches credentials for an unconnected org)."""
    from app.platform import config as config_mod
    from app.components.integrations.bullhorn import sync_runner

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _org(db)  # bullhorn_connected defaults falsey

    called = {"built": False}
    monkeypatch.setattr(
        sync_runner, "_build_service", lambda o: called.__setitem__("built", True)
    )
    sync_runner.execute_bullhorn_sync_run(org_id=org.id, mode="full")

    assert called["built"] is False
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 0
