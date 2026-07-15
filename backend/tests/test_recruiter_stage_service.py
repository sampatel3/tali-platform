from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.pipeline_service import transition_stage
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.services.recruiter_stage_service import (
    current_recruiter_stage,
    recruiter_stage_context,
    set_recruiter_stage,
    sync_from_external,
)


def _seed(db, *, pipeline_stage: str = "review"):
    org = Organization(name="Hiring Axis Org", slug=f"hiring-axis-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Engineer", source="manual")
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"hiring-axis-{id(db)}@example.test",
        full_name="Candidate",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status=pipeline_stage,
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return org, role, app


def test_advanced_initializes_screening_without_claiming_interview(db):
    _org, _role, app = _seed(db)

    transition_stage(
        db,
        app=app,
        to_stage="advanced",
        source="recruiter",
        actor_type="recruiter",
        actor_id=7,
    )
    db.flush()

    assert app.pipeline_stage == "advanced"
    assert app.recruiter_stage == "screening"
    assert app.recruiter_stage_source == "recruiter"
    events = (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == app.id)
        .all()
    )
    assert {event.event_type for event in events} == {
        "pipeline_stage_changed",
        "recruiter_stage_changed",
    }


def test_external_offer_updates_hiring_axis_not_tali_evaluation(db):
    _org, _role, app = _seed(db, pipeline_stage="review")

    changed = sync_from_external(
        db,
        app=app,
        raw_stage="Executive approval",
        provider="workable",
        provider_stage_kind="offer",
    )

    assert changed is True
    assert app.pipeline_stage == "review"
    assert app.recruiter_stage == "offer"
    assert app.application_outcome == "open"


def test_external_stage_can_legitimately_round_trip(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")
    app.recruiter_stage = "screening"
    app.recruiter_stage_source = "system"

    assert sync_from_external(
        db, app=app, raw_stage="Executive approval", provider="workable",
        provider_stage_kind="offer",
    )
    assert sync_from_external(
        db, app=app, raw_stage="Client shortlist", provider="workable",
        provider_stage_kind="shortlisted",
    )
    assert sync_from_external(
        db, app=app, raw_stage="Executive approval", provider="workable",
        provider_stage_kind="offer",
    )
    db.flush()

    assert app.recruiter_stage == "offer"
    changes = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "recruiter_stage_changed",
        )
        .count()
    )
    assert changes == 3


def test_unknown_provider_label_clears_stale_stage_and_surfaces_needs_mapping(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")
    app.recruiter_stage = "interviewing"
    app.recruiter_stage_source = "sync"
    app.workable_candidate_id = "workable-candidate-1"
    app.integration_sync_state = {"sync_status": "success", "source": "workable"}

    changed = sync_from_external(
        db,
        app=app,
        raw_stage="Founder coffee chat",
        provider="workable",
    )

    assert changed is False
    assert app.pipeline_stage == "advanced"
    assert app.recruiter_stage is None
    assert current_recruiter_stage(app) is None
    assert app.integration_sync_state["sync_status"] == "needs_mapping"
    assert app.integration_sync_state["sync_exception"] == {
        "code": "needs_mapping",
        "scope": "recruiter_stage",
        "provider": "workable",
        "raw_stage": "Founder coffee chat",
    }
    context = recruiter_stage_context(app)
    assert context["stage"] is None
    assert context["logistics_automation"]["status"] == "needs_mapping"


def test_semantic_stage_recovers_from_mapping_exception(db):
    _org, _role, app = _seed(db, pipeline_stage="review")
    sync_from_external(
        db,
        app=app,
        raw_stage="Unknown custom stage",
        provider="workable",
    )

    changed = sync_from_external(
        db,
        app=app,
        raw_stage="Client conversation",
        provider="workable",
        provider_stage_kind="interview",
    )

    assert changed is True
    assert app.pipeline_stage == "review"
    assert app.recruiter_stage == "interviewing"
    assert app.integration_sync_state["sync_status"] == "success"
    assert "sync_exception" not in app.integration_sync_state
    assert app.integration_sync_state["hiring_stage_sync"]["status"] == "mapped"


def test_external_hired_sets_outcome_without_manufacturing_handoff(db):
    _org, _role, app = _seed(db, pipeline_stage="review")

    sync_from_external(
        db,
        app=app,
        raw_stage="Placed",
        provider="bullhorn",
        force_stage="hired",
    )

    assert app.recruiter_stage == "hired"
    assert app.application_outcome == "hired"
    assert app.pipeline_stage == "review"


def test_agent_cannot_choose_offer_or_hire_without_explicit_authorization(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")

    with pytest.raises(HTTPException) as exc_info:
        set_recruiter_stage(
            db,
            app=app,
            to_stage="offer",
            source="agent",
            actor_type="agent",
        )

    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as fabricated_basis:
        set_recruiter_stage(
            db,
            app=app,
            to_stage="hired",
            source="agent",
            actor_type="agent",
            authorization_basis="model_says_approved",
        )
    assert fabricated_basis.value.status_code == 403


def test_migration_screening_fallback_yields_to_existing_external_offer(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")
    app.recruiter_stage = "screening"
    app.recruiter_stage_source = "migration"
    app.external_stage_normalized = "advanced"
    app.external_stage_raw = "Offer Extended"

    assert current_recruiter_stage(app) == "offer"


def test_native_post_handoff_reports_calendar_integration_required(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")
    app.recruiter_stage = "screening"
    app.recruiter_stage_source = "system"

    context = recruiter_stage_context(app)

    assert context["stage"] == "screening"
    assert context["provider"] == "native"
    assert context["workflow_owner"] == "agent"
    assert context["decision_owner"] == "human_hitl"
    assert context["logistics_automation"] == {
        "status": "integration_required",
        "required_integration": "calendar",
        "manual_coordination_is_default": False,
        "last_sync_status": None,
        "last_synced_at": None,
    }


def test_external_context_reports_ownership_without_claiming_live_sync_health(db):
    _org, _role, app = _seed(db, pipeline_stage="advanced")
    app.workable_candidate_id = "candidate-1"
    app.recruiter_stage = "interviewing"
    app.recruiter_stage_source = "sync"
    app.integration_sync_state = {"sync_status": "success"}

    context = recruiter_stage_context(app)

    assert context["provider"] == "workable"
    assert context["workflow_owner"] == "external_ats"
    assert context["decision_owner"] == "external_ats"
    assert context["logistics_automation"]["status"] == "external_ats_owned"
    assert context["logistics_automation"]["last_sync_status"] == "success"
