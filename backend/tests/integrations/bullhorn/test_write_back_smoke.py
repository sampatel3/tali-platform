"""Write-back smoke for the Bullhorn provider (PR-5 §6, build plan line 89).

Drives the REAL write helpers (``write_back``) authed against the live fake over
a real DB session, plus the resolver→provider wiring. Covers exactly what the
task asks: move / reject / note round-trip against the fake, and the unmapped-
status typed error (never guessed) in both non-strict and strict modes.

Only the transport is real; the fake's clock/counters are deterministic. Object
storage + Anthropic are never touched (pure status/note writes).
"""

from __future__ import annotations

from unittest.mock import Mock

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


def test_provider_exception_payload_is_never_returned_or_logged(db, caplog):
    """Token-bearing provider errors collapse to stable retry-safe messages."""
    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Interview Scheduled",
        taali_stage="advanced",
        is_reject=False,
    )
    leaked = "BhRestToken=LIVE-SECRET&corpToken=PRIVATE"

    class _ExplodingClient:
        def update_job_submission_status(self, **_kwargs):
            raise RuntimeError(leaked)

        def create_note(self, **_kwargs):
            raise RuntimeError(leaked)

    client = _ExplodingClient()
    moved = write_back.move_submission_status(
        db,
        org=org,
        client=client,
        submission_id="123",
        taali_intent="advanced",
    )
    noted = write_back.post_note(
        db,
        org=org,
        client=client,
        candidate_id="456",
        body="hello",
    )

    assert moved["code"] == "api_error"
    assert noted["code"] == "api_error"
    assert leaked not in str(moved)
    assert leaked not in str(noted)
    assert leaked not in "\n".join(record.getMessage() for record in caplog.records)


def test_invited_intent_requires_exactly_one_explicit_stage_mapping(db):
    org = _org(db)
    assert write_back.resolve_remote_status(
        db, org, taali_intent="invited"
    ) is None

    _seed_map(
        db,
        org,
        remote_status="Assessment Sent",
        taali_stage="invited",
        is_reject=False,
    )
    assert write_back.resolve_remote_status(
        db, org, taali_intent="invited"
    ) == "Assessment Sent"

    _seed_map(
        db,
        org,
        remote_status="Coding Challenge",
        taali_stage="invited",
        is_reject=False,
    )
    assert write_back.resolve_remote_status(
        db, org, taali_intent="invited"
    ) is None


def test_reject_and_advance_targets_are_never_selected_by_row_age(db):
    org = _org(db)
    for remote in ("Rejected A", "Rejected B"):
        _seed_map(
            db,
            org,
            remote_status=remote,
            taali_stage="review",
            is_reject=True,
        )
    for remote in ("Interview A", "Interview B"):
        _seed_map(
            db,
            org,
            remote_status=remote,
            taali_stage="advanced",
            is_reject=False,
        )

    assert write_back.resolve_remote_status(
        db, org, taali_intent="rejected"
    ) is None
    assert write_back.resolve_remote_status(
        db, org, taali_intent="advanced"
    ) is None

    org.bullhorn_config = {
        "rejectedJobResponseStatus": "Rejected B",
        "interviewScheduledJobResponseStatus": "Interview B",
    }
    db.flush()
    assert write_back.resolve_remote_status(
        db, org, taali_intent="rejected"
    ) == "Rejected B"
    assert write_back.resolve_remote_status(
        db, org, taali_intent="advanced"
    ) == "Interview B"



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


def test_provider_reject_posts_supplied_movement_reason_after_status_success(
    db, monkeypatch
):
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org(
        "reject-note",
        status_list=["New Lead", "Client Rejected"],
    )
    cand = state.make_candidate(
        bh_org, name="Decision Candidate", email="decision@example.com"
    )
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org,
        candidate_id=cand["id"],
        job_order_id=job["id"],
        status="New Lead",
    )
    _seed_map(
        db,
        org,
        remote_status="Client Rejected",
        taali_stage="review",
        is_reject=True,
    )
    app = _linked_app(
        db,
        org,
        submission_id=sub["id"],
        candidate_bh_id=str(cand["id"]),
    )
    app.role.bullhorn_job_order_id = str(job["id"])
    db.commit()
    reason = (
        "TAALI · Candidate rejected by recruiter\n\n"
        "Role: Eng\n"
        "Reason: Does not meet the configured role requirements."
    )

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)
        result = BullhornProvider(org, db).reject_application(
            app=app,
            role=app.role,
            reason=reason,
        )

    assert result["success"] is True
    assert result["config"]["movement_note_status"] == "posted"
    assert (
        state.orgs["reject-note"].entities["JobSubmission"][sub["id"]]["status"]
        == "Client Rejected"
    )
    notes = list(state.orgs["reject-note"].entities.get("Note", {}).values())
    assert len(notes) == 1
    assert notes[0]["personReference"]["id"] == cand["id"]
    assert "TAALI · Candidate rejected" in notes[0]["comments"]
    assert "The candidate was rejected in Taali." in notes[0]["comments"]
    assert "Does not meet the configured role requirements" not in notes[0]["comments"]


def test_provider_reject_note_failure_does_not_retry_confirmed_status(
    db, monkeypatch
):
    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Client Rejected",
        taali_stage="review",
        is_reject=True,
    )
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Client:
        status_writes = 0
        note_writes = 0

        def update_job_submission_status(self, **_kwargs):
            self.status_writes += 1
            return {"changedEntityId": 321}

        def create_note(self, **_kwargs):
            self.note_writes += 1
            raise RuntimeError("note endpoint unavailable")

    client = _Client()
    monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)

    with strict_workable_writes():
        result = BullhornProvider(org, db).reject_application(
            app=app,
            role=app.role,
            reason="Rejected in Taali following recruiter review.",
        )

    assert result["success"] is True
    assert result["config"]["movement_note_status"] == "failed"
    assert result["config"]["movement_note_code"] == "api_error"
    assert client.status_writes == 1
    assert client.note_writes == 1
    assert app.bullhorn_status == "Client Rejected"


def test_provider_reject_replaces_assessment_content_with_fixed_movement_copy(
    db, monkeypatch
):
    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Client Rejected",
        taali_stage="review",
        is_reject=True,
    )
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Client:
        status_writes = 0
        note_writes = 0
        note_bodies = []

        def update_job_submission_status(self, **_kwargs):
            self.status_writes += 1
            return {"changedEntityId": 321}

        def create_note(self, **kwargs):
            self.note_writes += 1
            self.note_bodies.append(kwargs["comments"])
            return {"changedEntityId": 999}

    client = _Client()
    monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)

    result = BullhornProvider(org, db).reject_application(
        app=app,
        role=app.role,
        reason="Assessment completed with a score of 42/100.",
    )

    assert result["success"] is True
    assert result["config"]["movement_note_status"] == "posted"
    assert client.status_writes == 1
    assert client.note_writes == 1
    assert "Assessment" not in client.note_bodies[0]
    assert "42/100" not in client.note_bodies[0]
    assert "The candidate was rejected in Taali." in client.note_bodies[0]
    assert app.bullhorn_status == "Client Rejected"


@pytest.mark.parametrize("failing_helper", ["composition", "guard"])
def test_provider_reject_note_preparation_exception_keeps_confirmed_movement(
    db, monkeypatch, failing_helper
):
    from app.components.integrations.bullhorn import provider as provider_module

    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Client Rejected",
        taali_stage="review",
        is_reject=True,
    )
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Client:
        status_writes = 0
        note_writes = 0

        def update_job_submission_status(self, **_kwargs):
            self.status_writes += 1
            return {"changedEntityId": 321}

        def create_note(self, **_kwargs):
            self.note_writes += 1
            return {"changedEntityId": 999}

    def _raise_note_error(*_args, **_kwargs):
        raise RuntimeError(f"{failing_helper} unavailable")

    client = _Client()
    monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)
    if failing_helper == "composition":
        monkeypatch.setattr(
            provider_module, "build_workable_reject_note", _raise_note_error
        )
    else:
        monkeypatch.setattr(
            provider_module, "contains_assessment_lifecycle_content", _raise_note_error
        )

    with strict_workable_writes():
        result = BullhornProvider(org, db).reject_application(
            app=app,
            role=app.role,
            reason="Rejected in Taali following recruiter review.",
        )

    assert result["success"] is True
    assert result["config"]["movement_note_status"] == "failed"
    assert result["config"]["movement_note_code"] == "unexpected_error"
    assert client.status_writes == 1
    assert client.note_writes == 0
    assert app.bullhorn_status == "Client Rejected"


@pytest.mark.parametrize(
    "body",
    [
        "Assessment invitation sent.",
        "Take-home exercise is ready.",
        "Coding challenge completed.",
        "Report: https://www.taali.ai/share/private-result",
        "Role: Assessment complete — score 91/100",
    ],
)
def test_provider_post_note_blocks_assessment_lifecycle_content(
    db, monkeypatch, body
):
    org = _org(db)

    def _unexpected_client(_self):
        raise AssertionError("blocked content must not construct a Bullhorn client")

    monkeypatch.setattr(BullhornProvider, "_client", _unexpected_client)

    result = BullhornProvider(org, db).post_note(
        candidate_id="654",
        member_id="",
        body=body,
    )

    assert result["success"] is False
    assert result["code"] == "assessment_lifecycle_content_blocked"
    assert result["error"] == "assessment_lifecycle_content_blocked"
    assert result["config"]["ats"] == "bullhorn"


def test_provider_post_note_allows_exact_trusted_assessment_role_name(db, monkeypatch):
    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    app.role.name = "Assessment Engineer"
    db.flush()

    class _Client:
        note_writes = 0

        def create_note(self, **_kwargs):
            self.note_writes += 1
            return {"changedEntityId": 999}

    client = _Client()
    monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)

    result = BullhornProvider(org, db).post_note(
        candidate_id="654",
        member_id="",
        body=(
            "TAALI · Candidate advanced\n"
            "Role: Assessment Engineer\n"
            "Reason: The candidate was approved for progression."
        ),
        role=app.role,
    )

    assert result["success"] is True
    assert client.note_writes == 1


def test_provider_reject_blocks_assessment_copy_hidden_in_template_role_line(
    db, monkeypatch
):
    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Client Rejected",
        taali_stage="review",
        is_reject=True,
    )
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Client:
        status_writes = 0
        note_writes = 0

        def update_job_submission_status(self, **_kwargs):
            self.status_writes += 1
            return {"changedEntityId": 321}

        def create_note(self, **_kwargs):
            self.note_writes += 1
            return {"changedEntityId": 999}

    client = _Client()
    monkeypatch.setattr(BullhornProvider, "_client", lambda _self: client)

    result = BullhornProvider(org, db).reject_application(
        app=app,
        role=app.role,
        reason="Below threshold",
        note_template="Role: Assessment complete — score 91/100",
        threshold_100=55,
    )

    assert result["success"] is True
    assert result["config"]["movement_note_status"] == "blocked_assessment_content"
    assert client.status_writes == 1
    assert client.note_writes == 0


def test_related_role_note_failure_does_not_replay_confirmed_bullhorn_move(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.candidate_application_event import CandidateApplicationEvent
    from app.models.role import ROLE_KIND_SISTER

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    related = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=app.role_id,
    )
    db.add(related)
    db.commit()

    class _Provider:
        move_calls = 0

        def move_application(self, **_kwargs):
            self.move_calls += 1
            app.bullhorn_status = "Interview Scheduled"
            return {
                "success": True,
                "config": {"remote_status": "Interview Scheduled"},
            }

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers,
        "_bullhorn_provider",
        lambda _db, _org, _app: provider,
    )

    def _failed_note(*_args, **_kwargs):
        raise WorkableWritebackError(
            action="note",
            code="api_error",
            message="rate limited",
            retriable=True,
        )

    monkeypatch.setattr(
        op_handlers, "_post_confirmed_related_role_bullhorn_note", _failed_note
    )

    result = op_handlers.run_move_stage(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_intent": "advanced",
            "acting_role_id": int(related.id),
            "reason": "Recruiter confirmed the shared ATS move",
        },
    )

    assert result == {"status": "ok", "application_id": app.id}
    assert provider.move_calls == 1
    db.expire_all()
    moved = db.get(CandidateApplication, app.id)
    assert moved.pipeline_stage == "advanced"
    assert moved.bullhorn_status == "Interview Scheduled"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type
            == "bullhorn_movement_note_failed",
        )
        .count()
        == 1
    )


def test_exact_target_bullhorn_write_is_successful_silent_noop(db):
    org = _org(db)
    _seed_map(
        db,
        org,
        remote_status="Interview Scheduled",
        taali_stage="advanced",
        is_reject=False,
    )
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    app.bullhorn_status = "Interview Scheduled"
    db.commit()

    class _Client:
        status_writes = 0

        def update_job_submission_status(self, **_kwargs):
            self.status_writes += 1
            return {"changedEntityId": 321}

    client = _Client()
    result = write_back.move_submission_status(
        db,
        org=org,
        client=client,
        submission_id="321",
        taali_intent="advanced",
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["code"] == "already_at_target"
    assert result["config"]["movement_performed"] is False
    assert client.status_writes == 0


def test_exact_target_bullhorn_move_handler_never_posts_related_role_note(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.role import ROLE_KIND_SISTER

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    related = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=app.role_id,
    )
    db.add(related)
    db.commit()

    class _Provider:
        move_calls = 0

        def move_application(self, **_kwargs):
            self.move_calls += 1
            return {
                "success": True,
                "skipped": True,
                "code": "already_at_target",
                "config": {
                    "remote_status": "Interview Scheduled",
                    "movement_performed": False,
                },
            }

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: provider
    )
    post_note = Mock()
    monkeypatch.setattr(
        op_handlers, "_post_confirmed_related_role_bullhorn_note", post_note
    )

    result = op_handlers.run_move_stage(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_intent": "advanced",
            "acting_role_id": int(related.id),
        },
    )

    assert result == {
        "status": "skipped",
        "reason": "already_at_target",
        "application_id": int(app.id),
    }
    assert provider.move_calls == 1
    post_note.assert_not_called()
    db.expire_all()
    assert db.get(CandidateApplication, app.id).pipeline_stage == "advanced"


def test_confirmed_advanced_bullhorn_move_posts_fixed_related_role_note(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.role import ROLE_KIND_SISTER

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    related = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=app.role_id,
    )
    db.add(related)
    db.commit()

    class _Provider:
        posted_bodies: list[str] = []

        def move_application(self, **_kwargs):
            app.bullhorn_status = "Interview Scheduled"
            return {
                "success": True,
                "code": "ok",
                "config": {"remote_status": "Interview Scheduled"},
            }

        def post_note(self, **kwargs):
            self.posted_bodies.append(kwargs["body"])
            return {"success": True, "code": "ok", "config": {}}

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: provider
    )

    result = op_handlers.run_move_stage(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_intent": "advanced",
            "acting_role_id": int(related.id),
        },
    )

    assert result == {"status": "ok", "application_id": int(app.id)}
    assert provider.posted_bodies == [
        "TAALI · Candidate advanced for a related role\n"
        f"Role: {related.name}\n"
        f"Original ATS role: {app.role.name}\n"
        "Reason: The candidate met the advance criteria for the related role."
    ]


def test_exact_target_manual_outcome_confirms_state_without_movement_event(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.candidate_application_event import CandidateApplicationEvent

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Provider:
        def reject_application(self, **_kwargs):
            return {
                "success": True,
                "skipped": True,
                "code": "already_at_target",
                "config": {
                    "remote_status": "Client Rejected",
                    "movement_performed": False,
                },
            }

    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: _Provider()
    )

    result = op_handlers.run_manual_outcome(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_outcome": "rejected",
        },
    )

    assert result == {
        "status": "skipped",
        "reason": "already_at_target",
        "application_id": int(app.id),
    }
    db.expire_all()
    refreshed = db.get(CandidateApplication, app.id)
    state = refreshed.integration_sync_state["outcome_writeback"]
    assert state["status"] == "confirmed"
    assert state["target_outcome"] == "rejected"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "bullhorn_rejected",
        )
        .count()
        == 0
    )


def test_manual_rejection_checkpoints_status_before_at_most_once_note(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    checkpoint = {"commits": 0}
    real_commit = db.commit

    def _commit():
        real_commit()
        checkpoint["commits"] += 1

    monkeypatch.setattr(db, "commit", _commit)

    class _Provider:
        status_writes = 0
        note_writes = 0
        status_only_flags = []

        def reject_application(self, *, include_movement_note, **_kwargs):
            self.status_only_flags.append(include_movement_note)
            if app.bullhorn_status == "Client Rejected":
                return {
                    "success": True,
                    "skipped": True,
                    "code": "already_at_target",
                    "config": {
                        "remote_status": "Client Rejected",
                        "movement_performed": False,
                    },
                }
            self.status_writes += 1
            app.bullhorn_status = "Client Rejected"
            return {
                "success": True,
                "code": "ok",
                "config": {
                    "remote_status": "Client Rejected",
                    "movement_performed": True,
                },
            }

        def post_rejection_movement_note(self, **_kwargs):
            # The status/event transaction is durable before the
            # non-idempotent Note create begins.
            assert checkpoint["commits"] == 1
            assert db.get(CandidateApplication, app.id).bullhorn_status == "Client Rejected"
            self.note_writes += 1
            return {
                "success": True,
                "config": {"movement_note_status": "posted"},
            }

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: provider
    )
    payload = {
        "application_id": int(app.id),
        "target_outcome": "rejected",
        "reason": "Candidate did not meet the role requirements.",
    }

    first = op_handlers.run_manual_outcome(db, org, app, payload)
    redelivery = op_handlers.run_manual_outcome(db, org, app, payload)

    assert first == {"status": "ok", "application_id": int(app.id)}
    assert redelivery == {
        "status": "skipped",
        "reason": "already_at_target",
        "application_id": int(app.id),
    }
    assert provider.status_only_flags == [False, False]
    assert provider.status_writes == 1
    assert provider.note_writes == 1


def test_manual_rejection_note_failure_does_not_replay_status_or_note(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.candidate_application_event import CandidateApplicationEvent

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")

    class _Provider:
        status_writes = 0
        note_attempts = 0

        def reject_application(self, **_kwargs):
            if app.bullhorn_status == "Client Rejected":
                return {
                    "success": True,
                    "skipped": True,
                    "code": "already_at_target",
                    "config": {"remote_status": "Client Rejected"},
                }
            self.status_writes += 1
            app.bullhorn_status = "Client Rejected"
            return {
                "success": True,
                "code": "ok",
                "config": {"remote_status": "Client Rejected"},
            }

        def post_rejection_movement_note(self, **_kwargs):
            self.note_attempts += 1
            raise RuntimeError("note endpoint unavailable")

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: provider
    )
    payload = {
        "application_id": int(app.id),
        "target_outcome": "rejected",
        "reason": "Candidate did not meet the role requirements.",
    }

    first = op_handlers.run_manual_outcome(db, org, app, payload)
    redelivery = op_handlers.run_manual_outcome(db, org, app, payload)

    assert first == {"status": "ok", "application_id": int(app.id)}
    assert redelivery["reason"] == "already_at_target"
    assert provider.status_writes == 1
    assert provider.note_attempts == 1
    failure = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type
            == "bullhorn_movement_note_failed",
        )
        .one()
    )
    assert failure.event_metadata["action"] == "manual_rejection_movement_note"


@pytest.mark.parametrize(
    "target_intent", ["applied", "invited", "in_assessment", "review"]
)
def test_related_role_bullhorn_note_requires_outbound_advanced_move(
    db, monkeypatch, target_intent
):
    from app.components.integrations.bullhorn import op_handlers
    from app.models.role import ROLE_KIND_SISTER

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    related = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=app.role_id,
    )
    db.add(related)
    db.commit()

    class _Provider:
        def move_application(self, **_kwargs):
            return {
                "success": True,
                "code": "ok",
                "config": {"remote_status": f"Mapped {target_intent}"},
            }

    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: _Provider()
    )
    post_note = Mock()
    monkeypatch.setattr(
        op_handlers, "_post_confirmed_related_role_bullhorn_note", post_note
    )

    result = op_handlers.run_move_stage(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_intent": target_intent,
            "acting_role_id": int(related.id),
        },
    )

    assert result == {"status": "ok", "application_id": int(app.id)}
    post_note.assert_not_called()


def test_bullhorn_note_failure_audit_error_cannot_replay_confirmed_move(
    db, monkeypatch
):
    from app.components.integrations.bullhorn import op_handlers
    from app.domains.assessments_runtime import pipeline_service
    from app.models.role import ROLE_KIND_SISTER

    org = _org(db)
    app = _linked_app(db, org, submission_id="321", candidate_bh_id="654")
    related = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="manual",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=app.role_id,
    )
    db.add(related)
    db.commit()

    class _Provider:
        move_calls = 0

        def move_application(self, **_kwargs):
            self.move_calls += 1
            app.bullhorn_status = "Interview Scheduled"
            return {
                "success": True,
                "code": "ok",
                "config": {"remote_status": "Interview Scheduled"},
            }

    provider = _Provider()
    monkeypatch.setattr(
        op_handlers, "_bullhorn_provider", lambda _db, _org, _app: provider
    )
    monkeypatch.setattr(
        op_handlers,
        "_post_confirmed_related_role_bullhorn_note",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("note endpoint unavailable")
        ),
    )
    original_append = pipeline_service.append_application_event
    original_rollback = db.rollback
    rollback_calls: list[bool] = []

    def _append(*args, **kwargs):
        if kwargs.get("event_type") == "bullhorn_movement_note_failed":
            raise RuntimeError("audit store unavailable")
        return original_append(*args, **kwargs)

    def _rollback():
        rollback_calls.append(True)
        return original_rollback()

    monkeypatch.setattr(pipeline_service, "append_application_event", _append)
    monkeypatch.setattr(db, "rollback", _rollback)

    result = op_handlers.run_move_stage(
        db,
        org,
        app,
        {
            "application_id": int(app.id),
            "target_intent": "advanced",
            "acting_role_id": int(related.id),
        },
    )

    assert result == {"status": "ok", "application_id": int(app.id)}
    assert provider.move_calls == 1
    assert len(rollback_calls) >= 2
    db.expire_all()
    moved = db.get(CandidateApplication, app.id)
    assert moved.bullhorn_status == "Interview Scheduled"
    assert moved.pipeline_stage == "advanced"


def test_sequential_provider_writes_reuse_durably_rotated_refresh_token(
    db, monkeypatch
):
    """A stage write consumes R1; a fresh provider for the note must use R2."""
    from app.platform.config import settings
    from app.platform.secrets import decrypt_text, encrypt_text

    state = FakeBullhornState()
    bh_org = state.make_org(
        "rotate-write",
        status_list=["New Lead", "Interview Scheduled"],
    )
    cand = state.make_candidate(
        bh_org, name="Rotate Candidate", email="rotate@example.com"
    )
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )

    with live_bullhorn_server(state) as server:
        boot: dict[str, str] = {}
        bootstrap = BullhornAuth(
            username=bh_org.username,
            client_id=bh_org.client_id,
            client_secret=bh_org.client_secret,
            refresh_token=None,
            password=bh_org.password,
            discovery_url=server.discovery_url,
            persist_tokens=lambda **values: boot.update(values),
        )
        bootstrap.authorize_with_password()
        first_refresh = boot["refresh_token"]

        org = _org(
            db,
            bullhorn_connected=True,
            bullhorn_username=bh_org.username,
            bullhorn_client_id=bh_org.client_id,
            bullhorn_client_secret=encrypt_text(
                bh_org.client_secret, settings.SECRET_KEY
            ),
            bullhorn_refresh_token=encrypt_text(
                first_refresh, settings.SECRET_KEY
            ),
            bullhorn_rest_url=boot.get("rest_url"),
        )
        _seed_map(
            db,
            org,
            remote_status="Interview Scheduled",
            taali_stage="advanced",
            is_reject=False,
        )
        app = _linked_app(
            db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"])
        )
        app.role.bullhorn_job_order_id = str(job["id"])
        db.commit()

        def _discover(auth):
            auth._oauth_url = f"{server.base_url}/oauth"  # noqa: SLF001
            auth._cached_rest_url = (  # noqa: SLF001
                f"{server.base_url}/rest-services/fake/"
            )
            return auth._oauth_url, auth._cached_rest_url  # noqa: SLF001

        monkeypatch.setattr(BullhornAuth, "discover", _discover)

        moved = BullhornProvider(org, db).move_application(
            candidate_id=str(sub["id"]),
            target_stage="advanced",
            role=app.role,
        )
        assert moved["success"] is True
        second_refresh = decrypt_text(
            org.bullhorn_refresh_token, settings.SECRET_KEY
        )
        assert second_refresh and second_refresh != first_refresh

        noted = BullhornProvider(org, db).post_note(
            candidate_id=str(cand["id"]),
            member_id="",
            body="Sequential decision summary",
            role=app.role,
        )
        assert noted["success"] is True
        third_refresh = decrypt_text(
            org.bullhorn_refresh_token, settings.SECRET_KEY
        )
        assert third_refresh and third_refresh != second_refresh


def test_post_note_html_escapes_body(db):
    """Bullhorn's Note.comments is an HTML field: angle brackets / ampersands
    must be escaped and newlines turned into <br /> so recruiter text renders
    literally (never as markup) and keeps its line breaks."""
    org = _org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("wbhtml", status_list=["New Lead"])
    cand = state.make_candidate(bh_org, name="HTML Candidate", email="html@example.com")

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        raw = "Strong <b>C++</b> & Rust\nSecond line"
        note = write_back.post_note(
            db, org=org, client=client, candidate_id=str(cand["id"]), body=raw
        )
        assert note["success"] is True
        stored = [r for r in state.orgs["wbhtml"].entities.get("Note", {}).values()]
        assert len(stored) == 1
        comments = stored[0]["comments"]
        # Angle brackets and ampersand escaped; newline -> <br />.
        assert comments == "Strong &lt;b&gt;C++&lt;/b&gt; &amp; Rust<br />Second line"
        # No raw markup survives.
        assert "<b>" not in comments


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
    org.workable_connected = True
    org.workable_access_token = "workable-token"
    org.workable_subdomain = "incumbent"
    db.commit()
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
        assert app.integration_sync_state["outcome_writeback"]["status"] == "confirmed"
        assert (
            app.integration_sync_state["outcome_writeback"]["target_outcome"]
            == "rejected"
        )
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

        # A forged legacy post-note op is denied even when it claims the old
        # related-role purpose.  Only the confirmed move handler may post that
        # fixed summary now.
        related_role_note = (
            "TAALI · Candidate advanced for a related role\n"
            "Role: AI Engineer\n"
            "Original ATS role: Data Platform Lead\n"
            "Reason: The candidate met the advance criteria for the related role."
        )
        notes_before = len(state.orgs["op"].entities.get("Note", {}))
        res_note = runner.execute_op(
            db,
            organization_id=org.id,
            op_type=runner.OP_POST_NOTE,
            payload={
                "application_id": app.id,
                "body": related_role_note,
                "note_purpose": runner.NOTE_PURPOSE_RELATED_ROLE_MOVEMENT,
                "actor_type": "agent",
                "source": "agent",
            },
        )
        assert res_note == {
            "status": "skipped",
            "reason": "standalone_ats_notes_disabled",
            "application_id": int(app.id),
        }
        assert len(state.orgs["op"].entities.get("Note", {})) == notes_before
        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == "bullhorn_note_posted",
            )
            .count()
            == 0
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
        runner.surface_op_failure(
            db,
            organization_id=org.id,
            op_type=runner.OP_MANUAL_OUTCOME,
            payload={"application_id": app.id, "target_outcome": "rejected"},
            error=exc.value,
        )

    from app.models.candidate_application_event import CandidateApplicationEvent

    surfaced = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "bullhorn_writeback_failed",
        )
        .one()
    )
    assert "Bullhorn didn't accept" in surfaced.reason
    assert surfaced.event_metadata["code"] == "needs_mapping"
    db.refresh(app)
    receipt = app.integration_sync_state["outcome_writeback"]
    assert receipt["status"] == "failed"
    assert receipt["target_outcome"] == "rejected"
    assert receipt["error_code"] == "needs_mapping"


# --- automated-reject paths write back to Bullhorn (drift fix B2) -------------


def test_reject_for_cv_gap_writes_back_to_bullhorn(db, monkeypatch):
    """The CV-gap auto-reject path (added on main after this branch's base) must
    write back to Bullhorn for a Bullhorn org, not silently reject locally."""
    from app.platform import config as config_mod
    from app.services import application_automation_service as automation

    monkeypatch.setattr(config_mod.settings, "BULLHORN_ENABLED", True, raising=False)
    org = _connected_org(db)
    state = FakeBullhornState()
    bh_org = state.make_org("cvgap", status_list=["New Lead", "Client Rejected"])
    cand = state.make_candidate(bh_org, name="Gap Candidate", email="gap@example.com")
    job = state.make_job_order(bh_org, title="Eng", is_open=True)
    sub = state.make_job_submission(
        bh_org, candidate_id=cand["id"], job_order_id=job["id"], status="New Lead"
    )
    _seed_map(db, org, remote_status="Client Rejected", taali_stage="review", is_reject=True)
    app = _linked_app(db, org, submission_id=sub["id"], candidate_bh_id=str(cand["id"]))
    role = db.query(Role).filter(Role.id == app.role_id).first()

    with live_bullhorn_server(state) as server:
        client = _authed_service(server, bh_org)
        monkeypatch.setattr(BullhornProvider, "_client", lambda self: client)

        result = automation.reject_for_cv_gap(
            db=db,
            org=org,
            app=app,
            role=role,
            actor_type="agent",
            actor_id=None,
            reason="No CV on file",
        )
        assert result["performed"] is True
        assert result.get("bullhorn_written") is True
        # Wrote the org's rejected-category status to the JobSubmission.
        assert state.orgs["cvgap"].entities["JobSubmission"][sub["id"]]["status"] == "Client Rejected"
        # The helper writes in-session (the caller owns the commit, matching the
        # Workable path); commit + reload proves both changes persist.
        db.commit()
        db.refresh(app)
        assert app.application_outcome == "rejected"
        assert app.bullhorn_status == "Client Rejected"
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
