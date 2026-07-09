"""Write-back smoke for the Bullhorn provider (PR-5 §6, build plan line 89).

Drives the REAL write helpers (``write_back``) authed against the live fake over
a real DB session, plus the resolver→provider wiring. Covers exactly what the
task asks: move / reject / note round-trip against the fake, and the unmapped-
status typed error (never guessed) in both non-strict and strict modes.

Only the transport is real; the fake's clock/counters are deterministic. Object
storage + Anthropic are never touched (pure status/note writes).
"""

from __future__ import annotations

import pytest

from app.components.integrations.bullhorn import write_back
from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn.provider import BullhornProvider
from app.components.integrations.bullhorn.service import BullhornService
from app.components.integrations.bullhorn.stage_map import ATS_BULLHORN
from app.components.integrations.resolver import resolve_ats_provider
from app.models.ats_stage_map import AtsStageMap
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.workable_actions_service import (
    WorkableWritebackError,
    strict_workable_writes,
)
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


# --- helpers -----------------------------------------------------------------


def _org(db, **kwargs) -> Organization:
    org = Organization(name="Bullhorn WB Org", **kwargs)
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


def _linked_app(db, org, *, submission_id, candidate_bh_id="900") -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id,
        email="wb@example.com",
        full_name="WB Candidate",
        bullhorn_candidate_id=candidate_bh_id,
    )
    db.add(cand)
    db.flush()
    role = Role(organization_id=org.id, name="Eng", source="bullhorn", bullhorn_job_order_id="500")
    db.add(role)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
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


# --- move / reject / note round-trip against the fake ------------------------


def test_move_reject_note_round_trip(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("wb", status_list=["New Lead", "Interview Scheduled", "Client Rejected"])
    cand = state.make_candidate(bh_org, name="WB Candidate", email="wb@example.com")
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )
    # Reverse map: advance → "Interview Scheduled", reject → "Client Rejected".
    _seed_map(db, org, remote_status="Interview Scheduled", taali_stage="advanced", is_reject=False)
    _seed_map(db, org, remote_status="Client Rejected", taali_stage="review", is_reject=True)
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)

        # --- move (advance) ---
        move = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
        assert move["success"] is True
        assert move["config"]["remote_status"] == "Interview Scheduled"
        # Remote reflects the write.
        assert state.orgs["wb"].entities["JobSubmission"][sub["id"]]["status"] == "Interview Scheduled"
        # Local-write-wins stamp set on the app (write_back stamps in-session; the
        # caller — the op handler in production — owns the commit). Commit here to
        # prove it persists, then reload.
        db.commit()
        db.refresh(app)
        assert app.bullhorn_status == "Interview Scheduled"
        assert app.bullhorn_status_local_write_at is not None

        # --- reject ---
        rej = write_back.reject_submission(db, org=org, client=client, submission_id=str(sub["id"]))
        assert rej["success"] is True
        assert rej["config"]["remote_status"] == "Client Rejected"
        assert state.orgs["wb"].entities["JobSubmission"][sub["id"]]["status"] == "Client Rejected"
        db.commit()
        db.refresh(app)
        assert app.bullhorn_status == "Client Rejected"

        # --- note ---
        note = write_back.post_note(
            db, org=org, client=client, candidate_id=str(cand["id"]), body="Great systems depth."
        )
        assert note["success"] is True
        notes = [
            r
            for r in state.orgs["wb"].entities.get("Note", {}).values()
            if r.get("comments") == "Great systems depth."
        ]
        assert len(notes) == 1
        assert notes[0]["personReference"]["id"] == cand["id"]


# --- advance never resolves to the placed/confirmed status -------------------


def test_advance_prefers_interview_over_placed_deterministically(db):
    """seed_stage_map_from_categorization maps BOTH 'Interview Scheduled' AND the
    confirmed 'Placed' status to advanced/non-reject (the common production shape).
    An advance write must deterministically pick 'Interview Scheduled' and NEVER
    'Placed' — writing Placed to Bullhorn on a mere advance would fire the org's
    placement/billing/client-notification workflows. Was a DB-row-order coin flip.
    """
    from app.components.integrations.bullhorn import stage_map as sm

    org = _org(db)
    sm.seed_stage_map_from_categorization(
        db,
        org,
        categorization={
            "interviewScheduledJobResponseStatus": "Interview Scheduled",
            "confirmedJobResponseStatus": "Placed",
            "rejectedJobResponseStatus": "Client Rejected",
        },
    )
    db.commit()
    # Both advanced rows exist and are non-reject — the ambiguous case.
    advanced_rows = (
        db.query(AtsStageMap)
        .filter(
            AtsStageMap.org_id == org.id,
            AtsStageMap.ats == ATS_BULLHORN,
            AtsStageMap.taali_stage == "advanced",
            AtsStageMap.is_reject.is_(False),
        )
        .count()
    )
    assert advanced_rows == 2
    # Resolve repeatedly: always the interview status, never the placed one.
    for _ in range(8):
        resolved = write_back.resolve_remote_status(db, org, taali_intent="advanced")
        assert resolved == "Interview Scheduled"
        assert resolved != "Placed"


def test_advance_never_resolves_to_the_configured_placed_status(db):
    """Hard guarantee, backend-agnostic: when the org's confirmed/placed status is
    the ONLY advanced-mapped row, an advance must NOT resolve to it — it surfaces
    needs-mapping instead. (SQLite can't reproduce the Postgres unordered-.first()
    coin flip, so we assert the exclusion contract directly: the pre-fix resolver
    returns 'Placed' here; the fixed one returns None → advance writes nothing.)
    Writing 'Placed' on a mere advance would fire the ATS placement/billing flows.
    """
    org = _org(db)
    _seed_map(db, org, remote_status="Placed", taali_stage="advanced", is_reject=False)
    # The connect-time seeder records the confirmed/placed status here; emulate it.
    org.bullhorn_config = {"confirmedJobResponseStatus": "Placed"}
    db.commit()

    resolved = write_back.resolve_remote_status(db, org, taali_intent="advanced")
    assert resolved != "Placed"
    assert resolved is None  # unmapped → caller surfaces needs-mapping, never guesses

    # And end-to-end: move returns a needs_mapping failure, writing nothing.
    state = FakeBullhornState()
    bh_org = state.make_org("plc", status_list=["New Lead", "Placed"])
    cand = state.make_candidate(bh_org)
    job = state.make_job_order(bh_org)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))
    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        result = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
    assert result["success"] is False
    assert result["code"] == "needs_mapping"
    assert state.orgs["plc"].entities["JobSubmission"][sub["id"]]["status"] == "New Lead"


def test_seeder_records_confirmed_placed_status_on_config(db):
    """seed_stage_map_from_categorization stamps the confirmed/placed status onto
    org.bullhorn_config so write-back can exclude it (the durable discriminator)."""
    from app.components.integrations.bullhorn import stage_map as sm

    org = _org(db)
    sm.seed_stage_map_from_categorization(
        db,
        org,
        categorization={
            "interviewScheduledJobResponseStatus": "Interview Scheduled",
            "confirmedJobResponseStatus": "Placed",
            "rejectedJobResponseStatus": "Client Rejected",
        },
    )
    db.commit()
    db.refresh(org)
    assert org.bullhorn_config["confirmedJobResponseStatus"] == "Placed"


def test_advance_write_back_never_posts_placed_status(db):
    """End-to-end: a recruiter advance against the fake writes 'Interview Scheduled'
    to the JobSubmission, never 'Placed', even with both advanced rows seeded."""
    from app.components.integrations.bullhorn import stage_map as sm

    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org(
        "adv", status_list=["New Lead", "Interview Scheduled", "Placed", "Client Rejected"]
    )
    cand = state.make_candidate(bh_org, name="Adv Candidate", email="adv@example.com")
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )
    # Seed exactly as connect does: interview + placed both → advanced/non-reject.
    sm.seed_stage_map_from_categorization(
        db,
        org,
        categorization={
            "interviewScheduledJobResponseStatus": "Interview Scheduled",
            "confirmedJobResponseStatus": "Placed",
            "rejectedJobResponseStatus": "Client Rejected",
        },
    )
    db.commit()
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        move = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
        assert move["success"] is True
        assert move["config"]["remote_status"] == "Interview Scheduled"
        # The remote JobSubmission got the interview status, NOT Placed.
        assert (
            state.orgs["adv"].entities["JobSubmission"][sub["id"]]["status"]
            == "Interview Scheduled"
        )


# --- unmapped status is surfaced, never guessed ------------------------------


def test_unmapped_intent_is_needs_mapping_not_guessed(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("wb2")
    cand = state.make_candidate(bh_org)
    job = state.make_job_order(bh_org)
    sub = state.make_job_submission(bh_org, candidate_id=cand["id"], job_order_id=job["id"])
    # No AtsStageMap rows seeded → nothing maps for "advanced".
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))
    original_remote_status = state.orgs["wb2"].entities["JobSubmission"][sub["id"]]["status"]

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)

        # Non-strict: a failure dict, code needs_mapping, and NOTHING written.
        result = write_back.move_submission_status(
            db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
        )
        assert result["success"] is False
        assert result["code"] == "needs_mapping"
        # Remote status untouched (no guessed write).
        assert (
            state.orgs["wb2"].entities["JobSubmission"][sub["id"]]["status"]
            == original_remote_status
        )
        db.refresh(app)
        assert app.bullhorn_status_local_write_at is None

        # Strict (decision-batch) mode: raises the shared WorkableWritebackError,
        # non-retriable so the op surfaces terminally instead of looping.
        with pytest.raises(WorkableWritebackError) as exc:
            with strict_workable_writes():
                write_back.move_submission_status(
                    db, org=org, client=client, submission_id=str(sub["id"]), taali_intent="advanced"
                )
        assert exc.value.code == "needs_mapping"
        assert exc.value.retriable is False


# --- resolver → provider wiring + precedence ---------------------------------


def test_resolver_returns_bullhorn_provider_for_connected_org(db, monkeypatch):
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )
    provider = resolve_ats_provider(org, db)
    assert isinstance(provider, BullhornProvider)
    assert provider.ats == "bullhorn"


def test_resolver_workable_takes_precedence_over_bullhorn(db, monkeypatch):
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _org(
        db,
        workable_connected=True,
        workable_access_token="tok",
        workable_subdomain="acme",
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )
    provider = resolve_ats_provider(org, db)
    # Dual-connected → the incumbent Workable wins (documented precedence rule).
    assert provider.ats == "workable"


def test_resolver_bullhorn_gated_off_returns_none(db, monkeypatch):
    from app.platform import config as config_mod

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", False, raising=False)
    org = _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )
    # Flag off → Bullhorn is never resolved (every hook a no-op for the org).
    assert resolve_ats_provider(org, db) is None


# --- op_runner routes to Bullhorn (dispatch seam + terminal surface) ---------


def _connected_org(db) -> Organization:
    """A Bullhorn-connected org (creds are placeholders; the provider's client is
    monkeypatched to the fake, so decrypt/auth aren't exercised here)."""
    return _org(
        db,
        bullhorn_connected=True,
        bullhorn_client_id="cid",
        bullhorn_refresh_token="rt",
        bullhorn_username="apiuser",
    )


def test_op_runner_routes_manual_outcome_and_note_to_bullhorn(db, monkeypatch):
    from app.platform import config as config_mod
    from app.services import workable_op_runner as runner

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("op", status_list=["New Lead", "Interview Scheduled", "Client Rejected"])
    cand = state.make_candidate(bh_org, name="WB Candidate", email="wb@example.com")
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )
    _seed_map(db, org, remote_status="Interview Scheduled", taali_stage="advanced", is_reject=False)
    _seed_map(db, org, remote_status="Client Rejected", taali_stage="review", is_reject=True)
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        # Point the provider's client at the live fake (bypass decrypt/discovery).
        monkeypatch.setattr(BullhornProvider, "_client", lambda self: client)

        # OP_MANUAL_OUTCOME (reject) routes through the Bullhorn handler.
        res = runner.execute_op(
            db,
            organization_id=org.id,
            op_type=runner.OP_MANUAL_OUTCOME,
            payload={"application_id": app.id, "target_outcome": "rejected", "reason": "not a fit"},
        )
        assert res["status"] == "ok"
        assert state.orgs["op"].entities["JobSubmission"][sub["id"]]["status"] == "Client Rejected"
        db.refresh(app)
        assert app.bullhorn_status == "Client Rejected"
        assert app.bullhorn_status_local_write_at is not None
        # The handler committed a bullhorn_rejected event.
        from app.models.candidate_application_event import CandidateApplicationEvent

        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == "bullhorn_rejected",
            )
            .count()
            == 1
        )

        # OP_POST_NOTE routes through the Bullhorn handler.
        res_note = runner.execute_op(
            db,
            organization_id=org.id,
            op_type=runner.OP_POST_NOTE,
            payload={"application_id": app.id, "body": "Solid interview."},
        )
        assert res_note["status"] == "ok"
        assert any(
            r.get("comments") == "Solid interview."
            for r in state.orgs["op"].entities.get("Note", {}).values()
        )


def test_op_runner_bullhorn_unmapped_reject_raises_for_terminal_surface(db, monkeypatch):
    """An unmapped reject under the op path raises WorkableWritebackError so the
    shell's surface_op_failure fires — the same terminal-failure surface Workable
    ops use (build plan item 5)."""
    from app.platform import config as config_mod
    from app.services import workable_op_runner as runner

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("op2")
    cand = state.make_candidate(bh_org)
    job = state.make_job_order(bh_org)
    sub = state.make_job_submission(bh_org, candidate_id=cand["id"], job_order_id=job["id"])
    # No is_reject stage-map row → reject is unmapped.
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        monkeypatch.setattr(BullhornProvider, "_client", lambda self: client)

        with pytest.raises(WorkableWritebackError) as exc:
            runner.execute_op(
                db,
                organization_id=org.id,
                op_type=runner.OP_MANUAL_OUTCOME,
                payload={"application_id": app.id, "target_outcome": "rejected"},
            )
        assert exc.value.code == "needs_mapping"
        assert exc.value.retriable is False
