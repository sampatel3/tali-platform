"""End-to-end smoke for the Bullhorn full-sync engine against the LIVE fake.

Drives the REAL ``BullhornService`` (authed against the uvicorn-backed fake) →
``BullhornSyncService.sync_org`` over a real DB session, and asserts the full
walk landed: JobOrder→Role, JobSubmission→Candidate+CandidateApplication, status
mapping (mapped → Taali stage; unmapped → funnel top + raw status kept),
JobSubmissionHistory→events, Notes→agent-visible context.

Only the transport is real; the fake's clock/counters are deterministic. Object
storage + Celery are unconfigured/eager in the test env, so the CV store no-ops
(returns None → skipped) and the gated scoring enqueue is off (roles are not
starred), keeping the smoke free of network/Anthropic calls.
"""

from __future__ import annotations

from app.components.integrations.bullhorn import stage_map as sm
from app.components.integrations.bullhorn.auth import BullhornAuth
from app.components.integrations.bullhorn.service import BullhornService
from app.components.integrations.bullhorn.sync_service import BullhornSyncService
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import Role
from tests.fakes.bullhorn_fakes import live_bullhorn_server
from tests.fakes.bullhorn_state import FakeBullhornState


def _org(db) -> Organization:
    org = Organization(name="Bullhorn E2E Org")
    db.add(org)
    db.commit()
    return org


def _authed_service(server, org_state) -> BullhornService:
    """Real client authed against the live fake via the one-time password grant."""
    auth = BullhornAuth(
        username=org_state.username,
        client_id=org_state.client_id,
        client_secret=org_state.client_secret,
        refresh_token=None,
        persist_tokens=lambda **kw: None,  # rotation persistence not under test here
        discovery_url=server.discovery_url,
        password=org_state.password,
    )
    auth.authorize_with_password()
    return BullhornService(auth, client_id=org_state.client_id)


def _seed_note(state: FakeBullhornState, org_state, *, candidate_id: int, comments: str) -> int:
    """Seed a Bullhorn Note about a candidate (via the generic entity table)."""
    note_id = state._next()  # noqa: SLF001 — test seeding uses the state counter
    state._put_entity(  # noqa: SLF001
        org_state,
        "Note",
        {
            "id": note_id,
            "comments": comments,
            "action": "Other",
            "personReference": {"id": candidate_id},
            "commentingPerson": {"name": "Jo Recruiter"},
            "dateAdded": state.now,
        },
    )
    return note_id


def test_full_sync_maps_status_and_imports_candidate_history_and_notes(db):
    org = _org(db)
    state = FakeBullhornState()
    # The fake org's categorization designates these as interview/placed/rejected.
    bh_org = state.make_org("e2e", status_list=["New Lead", "Interview Scheduled", "Placed", "Client Rejected"])

    job = state.make_job_order(bh_org, title="Senior Engineer", is_open=True)
    cand = state.make_candidate(bh_org, name="Ada Lovelace", email="ada@example.com")
    # dateAdded = the remote-ATS applied date (epoch millis). 2026-01-02 00:00 UTC.
    applied_millis = 1767312000000
    sub = state.make_job_submission(
        bh_org,
        candidate_id=cand["id"],
        job_order_id=job["id"],
        status="Interview Scheduled",
        dateAdded=applied_millis,
    )
    # History trail + two notes about the candidate.
    state.make_job_submission_history(bh_org, job_submission_id=sub["id"], status="New Lead")
    state.make_job_submission_history(bh_org, job_submission_id=sub["id"], status="Interview Scheduled")
    _seed_note(state, bh_org, candidate_id=cand["id"], comments="Strong systems background.")
    _seed_note(state, bh_org, candidate_id=cand["id"], comments="Client asked to fast-track.")

    with live_bullhorn_server(state) as server:
        service = BullhornSyncService(_authed_service(server, bh_org))
        # Pre-seed the stage map from the org's categorization settings so
        # "Interview Scheduled" resolves to a Taali stage (connect-time behavior).
        status_list = service.client.get_status_list()
        sm.seed_stage_map_from_categorization(db, org, categorization=status_list["categorization"])
        db.commit()

        progress = service.sync_org(db, org, mode="full")

    # --- role upserted from the JobOrder -----------------------------------
    role = db.query(Role).filter(Role.organization_id == org.id).one()
    assert role.bullhorn_job_order_id == str(job["id"])
    assert role.source == "bullhorn"
    assert "Senior Engineer" in (role.job_spec_text or "")

    # --- candidate + application upserted ----------------------------------
    candidate = db.query(Candidate).filter(Candidate.organization_id == org.id).one()
    assert candidate.bullhorn_candidate_id == str(cand["id"])
    assert candidate.email == "ada@example.com"
    assert candidate.full_name == "Ada Lovelace"

    app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
    assert app.bullhorn_job_submission_id == str(sub["id"])
    assert app.source == "bullhorn"
    # raw remote status preserved
    assert app.bullhorn_status == "Interview Scheduled"
    # remote-ATS applied date (dateAdded) mapped onto workable_created_at so the
    # applied-date decision surfaces have a real date for Bullhorn apps.
    assert app.workable_created_at is not None
    assert app.workable_created_at.year == 2026 and app.workable_created_at.month == 1
    # "Interview Scheduled" was mapped (categorization → advanced) so the Taali
    # stage moved off the funnel top.
    assert app.pipeline_stage == "advanced"

    # --- history → events ---------------------------------------------------
    status_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "bullhorn_status_change",
        )
        .count()
    )
    assert status_events == 2

    # --- notes → agent-visible context -------------------------------------
    note_events = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "recruiter_note",
        )
        .all()
    )
    assert len(note_events) == 2
    assert all(e.event_metadata.get("source") == "bullhorn" for e in note_events)

    assert progress["applications_upserted"] == 1
    assert progress["history_events"] == 2
    assert progress["notes_imported"] == 2
    assert progress["phase"] == "completed"

    # --- COST SAFETY: importing candidates must NOT enqueue paid scoring ----
    # The role isn't starred_for_auto_sync (Bullhorn imports never auto-star),
    # so the gated scoring enqueue is off — no CvScoreJob is created. This is
    # the hard rule: sync ingests cleanly; paid re-evaluation is recruiter-only.
    assert db.query(CvScoreJob).count() == 0


def test_unmapped_status_keeps_funnel_top_and_surfaces_for_mapping(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("e2e2", status_list=["Bespoke Client Stage"])
    job = state.make_job_order(bh_org, title="Analyst", is_open=True)
    cand = state.make_candidate(bh_org, name="Grace Hopper", email="grace@example.com")
    state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="Bespoke Client Stage"
    )

    with live_bullhorn_server(state) as server:
        service = BullhornSyncService(_authed_service(server, bh_org))
        service.sync_org(db, org, mode="full")

    app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
    # Unmapped: NEVER guessed → stays at funnel top, raw status kept.
    assert app.pipeline_stage == "applied"
    assert app.bullhorn_status == "Bespoke Client Stage"
    # And it's surfaced as needs-mapping.
    assert sm.unmapped_statuses(db, org) == ["Bespoke Client Stage"]


def test_reject_mapped_status_resolves_application(db):
    """A status mapped ``is_reject`` sets the rejected outcome (row resolves)."""
    from app.domains.assessments_runtime.role_support import is_resolved

    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org(
        "e2e_rej",
        status_list=["Client Rejected"],
        categorization={
            "interviewScheduledJobResponseStatus": "X",
            "confirmedJobResponseStatus": "Y",
            "rejectedJobResponseStatus": "Client Rejected",
        },
    )
    job = state.make_job_order(bh_org, title="J", is_open=True)
    cand = state.make_candidate(bh_org, name="Rej Ected", email="rej@example.com")
    state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="Client Rejected"
    )

    with live_bullhorn_server(state) as server:
        service = BullhornSyncService(_authed_service(server, bh_org))
        status_list = service.client.get_status_list()
        sm.seed_stage_map_from_categorization(db, org, categorization=status_list["categorization"])
        db.commit()
        service.sync_org(db, org, mode="full")

    app = db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).one()
    assert app.application_outcome == "rejected"
    assert app.bullhorn_status == "Client Rejected"
    assert is_resolved(app) is True


def test_resync_is_idempotent(db):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("e2e3", status_list=["New Lead"])
    job = state.make_job_order(bh_org, title="SRE", is_open=True)
    cand = state.make_candidate(bh_org, name="Ken Thompson", email="ken@example.com")
    sub = state.make_job_submission(bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead")
    state.make_job_submission_history(bh_org, job_submission_id=sub["id"], status="New Lead")

    with live_bullhorn_server(state) as server:
        service = BullhornSyncService(_authed_service(server, bh_org))
        service.sync_org(db, org, mode="full")
        service.sync_org(db, org, mode="full")  # second pass

    # No duplicate role / candidate / application / history event.
    assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
    assert db.query(Candidate).filter(Candidate.organization_id == org.id).count() == 1
    assert db.query(CandidateApplication).filter(CandidateApplication.organization_id == org.id).count() == 1
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.event_type == "bullhorn_status_change")
        .count()
        == 1
    )
