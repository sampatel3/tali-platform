"""Deterministic public-apply knockout autonomy policy."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from app.domains.job_pages.screening_service import create_role_question
from app.models import (
    AgentDecision,
    Candidate,
    CandidateApplication,
    CandidateApplicationEvent,
    JobPage,
    Organization,
    Role,
    RoleBrief,
)
from app.platform.config import settings
from app.services import rate_limit
from app.services.rate_limit import reset_memory_buckets


@pytest.fixture(autouse=True)
def _public_apply_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ATS_PUBLIC_APPLY_ENABLED", True)
    monkeypatch.setattr(settings, "ATS_APPLY_RATE_LIMIT_PER_HOUR", 20)
    monkeypatch.setattr(rate_limit, "_get_redis", lambda: None)
    reset_memory_buckets()
    yield
    reset_memory_buckets()


def _seed_page(
    db,
    *,
    agentic: bool,
    auto_reject: bool = False,
    auto_reject_pre_screen: bool = False,
    paused: bool = False,
):
    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Knockout {suffix}", slug=f"knockout-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="manual",
        job_spec_text="Build reliable systems.",
        agentic_mode_enabled=agentic,
        auto_reject=auto_reject,
        auto_reject_pre_screen=auto_reject_pre_screen,
    )
    if paused:
        role.agent_paused_at = datetime.now(timezone.utc)
        role.agent_paused_reason = "recruiter paused role"
    db.add(role)
    db.flush()
    brief = RoleBrief(organization_id=org.id, role_id=role.id)
    db.add(brief)
    db.flush()
    page = JobPage(
        organization_id=org.id,
        brief_id=brief.id,
        token=f"knockout-{suffix}",
        status="open",
    )
    db.add(page)
    question = create_role_question(
        db,
        org.id,
        role.id,
        prompt="Are you authorized to work locally?",
        kind="boolean",
        required=True,
        knockout=True,
        knockout_expected=[True],
    )
    db.commit()
    return role, page, question


def _apply(client, page, monkeypatch, *, email: str):
    from app.services import document_service

    monkeypatch.setattr(
        document_service,
        "process_document_upload",
        lambda **_kwargs: {
            "file_url": "s3://test/knockout-cv.pdf",
            "filename": "knockout-cv.pdf",
            "extracted_text": "Experienced platform engineer",
            "text_preview": "Experienced platform engineer",
        },
    )
    monkeypatch.setattr(
        document_service, "load_stored_document_bytes", lambda _url: None
    )
    return client.post(
        f"/api/v1/public/job-pages/{page.token}/apply",
        data={"full_name": "Knockout Candidate", "email": email, "answers": "{}"},
        files={"resume": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )


def test_running_prescreen_opted_in_role_resolves_knockout_without_decision_hub(
    client, db, monkeypatch
):
    role, page, question = _seed_page(
        db,
        agentic=True,
        auto_reject_pre_screen=True,
    )

    response = _apply(
        client,
        page,
        monkeypatch,
        email="pre-screen@knockout.test",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "received"
    assert "knockout_passed" not in body
    assert "failed_question_ids" not in body

    db.expire_all()
    application = (
        db.query(CandidateApplication).filter_by(id=body["application_id"]).one()
    )
    assert application.application_outcome == "rejected"
    assert application.auto_reject_state == "rejected"
    assert application.auto_reject_reason == "Did not meet screening requirements"
    assert application.auto_reject_triggered_at is not None
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == application.id)
        .count()
        == 0
    )

    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application.id,
            CandidateApplicationEvent.event_type == "auto_rejected",
        )
        .one()
    )
    assert event.actor_type == "system"
    assert event.event_metadata["source"] == "knockout_screening"
    assert event.event_metadata["failed_question_ids"] == [question.id]
    assert event.event_metadata["ats_provider"] == "standalone"
    assert event.event_metadata["ats_written"] is False


@pytest.mark.parametrize(
    ("agentic", "auto_reject", "auto_reject_pre_screen", "paused"),
    [
        (True, False, False, False),  # both policies off
        (True, True, False, False),  # scored reject does not grant pre-screen
        (False, False, True, False),  # agent off
        (True, False, True, True),  # agent paused
    ],
)
def test_knockout_policy_off_or_role_ineligible_retains_hitl_card(
    client, db, monkeypatch, agentic, auto_reject, auto_reject_pre_screen, paused
):
    _role, page, _question = _seed_page(
        db,
        agentic=agentic,
        auto_reject=auto_reject,
        auto_reject_pre_screen=auto_reject_pre_screen,
        paused=paused,
    )

    response = _apply(
        client,
        page,
        monkeypatch,
        email=f"held-{uuid.uuid4().hex[:8]}@knockout.test",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "received"
    assert "knockout_passed" not in body
    assert "failed_question_ids" not in body

    db.expire_all()
    application = (
        db.query(CandidateApplication).filter_by(id=body["application_id"]).one()
    )
    assert application.application_outcome == "open"
    assert application.auto_reject_state == "awaiting_recruiter_approval"
    assert application.auto_reject_triggered_at is None
    decision = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application.id,
            AgentDecision.status == "pending",
        )
        .one()
    )
    assert decision.decision_type == "skip_assessment_reject"
    assert decision.evidence["source"] == "knockout_screening"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application.id,
            CandidateApplicationEvent.event_type == "agent_decision_queued",
        )
        .count()
        == 1
    )


def test_ats_writeback_failure_keeps_knockout_open_and_cards(client, db, monkeypatch):
    role, page, _question = _seed_page(
        db, agentic=True, auto_reject_pre_screen=True
    )
    candidate = Candidate(
        organization_id=role.organization_id,
        full_name="Restored ATS Candidate",
        email="ats-failure@knockout.test",
    )
    db.add(candidate)
    db.flush()
    existing = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="rejected",
        pipeline_stage="applied",
        application_outcome="rejected",
        source="workable",
        workable_candidate_id="workable-application-42",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(existing)
    db.commit()
    existing_id = int(existing.id)

    class _FailingWorkable:
        ats = "workable"

        @staticmethod
        def reject_application(**_kwargs):
            return {
                "success": False,
                "action": "disqualify",
                "code": "api_error",
                "message": "Workable rejected the write",
            }

    from app.components.integrations import resolver

    monkeypatch.setattr(
        resolver,
        "resolve_application_ats_provider",
        lambda _org, _db, _application: _FailingWorkable(),
    )

    response = _apply(
        client,
        page,
        monkeypatch,
        email="ats-failure@knockout.test",
    )

    assert response.status_code == 200, response.text
    assert response.json()["application_id"] == existing_id
    db.expire_all()
    application = db.query(CandidateApplication).filter_by(id=existing_id).one()
    assert application.application_outcome == "open"
    assert application.auto_reject_state == "awaiting_recruiter_approval"
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == existing_id,
            AgentDecision.status == "pending",
        )
        .count()
        == 1
    )
    event_types = {
        row.event_type
        for row in db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == existing_id)
        .all()
    }
    assert "workable_writeback_failed" in event_types
    assert "agent_decision_queued" in event_types
    assert "auto_rejected" not in event_types


def test_ats_writeback_success_precedes_local_knockout_reject(client, db, monkeypatch):
    role, page, _question = _seed_page(
        db, agentic=True, auto_reject_pre_screen=True
    )
    candidate = Candidate(
        organization_id=role.organization_id,
        full_name="Restored ATS Candidate",
        email="ats-success@knockout.test",
    )
    db.add(candidate)
    db.flush()
    existing = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="rejected",
        pipeline_stage="applied",
        application_outcome="rejected",
        source="workable",
        workable_candidate_id="workable-application-99",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(existing)
    db.flush()
    prior_card = AgentDecision(
        id=99001,
        organization_id=role.organization_id,
        role_id=role.id,
        application_id=existing.id,
        decision_type="skip_assessment_reject",
        recommendation="skip_assessment_reject",
        status="pending",
        reasoning="Prior knockout review",
        evidence={"source": "knockout_screening"},
        model_version="knockout_v1",
        prompt_version="knockout_screening.v1",
        active_capabilities={},
        token_spend={},
        idempotency_key=f"prior-knockout-{existing.id}",
        input_fingerprint={},
    )
    db.add(prior_card)
    db.commit()

    calls: list[int] = []

    class _SuccessfulWorkable:
        ats = "workable"

        @staticmethod
        def reject_application(*, app, **_kwargs):
            calls.append(int(app.id))
            return {"success": True, "action": "disqualify", "code": "ok"}

    from app.components.integrations import resolver

    monkeypatch.setattr(
        resolver,
        "resolve_application_ats_provider",
        lambda _org, _db, _application: _SuccessfulWorkable(),
    )

    response = _apply(
        client,
        page,
        monkeypatch,
        email="ats-success@knockout.test",
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    application = (
        db.query(CandidateApplication)
        .filter_by(id=response.json()["application_id"])
        .one()
    )
    assert calls == [application.id]
    assert application.application_outcome == "rejected"
    assert application.auto_reject_state == "rejected"
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == application.id,
            AgentDecision.status == "pending",
        )
        .count()
        == 0
    )
    db.refresh(prior_card)
    assert prior_card.status == "discarded"
    assert "deterministic knockout" in prior_card.resolution_note
    event_types = {
        row.event_type
        for row in db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == application.id)
        .all()
    }
    assert "workable_disqualified" in event_types
    assert "auto_rejected" in event_types
