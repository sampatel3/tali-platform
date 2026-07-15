"""Compatibility coverage for Workable hiring-stage reconciliation.

External interview/offer/hire observations update the provider-neutral hiring
axis. They never manufacture Tali's explicit ``advanced`` evaluation handoff.
"""
from __future__ import annotations

from app.domains.assessments_runtime.pipeline_service import (
    reconcile_post_handover_advanced,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _seed(db, *, workable_stage, pipeline_stage="applied", outcome="open"):
    org = Organization(name="O", slug=f"post-handoff-{id(db)}-{workable_stage}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire an engineer",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email=f"post-handoff-{id(db)}@x.test",
        full_name="C",
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
        application_outcome=outcome,
        source="workable",
        workable_stage=workable_stage,
        cv_match_score=40.0,
    )
    db.add(app)
    db.flush()
    return org, role, app


def test_offer_updates_hiring_axis_without_advancing_tali(db):
    _org, role, app = _seed(db, workable_stage="Offer")

    assert reconcile_post_handover_advanced(db, app=app, role=role) is False

    assert app.pipeline_stage == "applied"
    assert app.recruiter_stage == "offer"
    assert app.application_outcome == "open"


def test_interview_updates_hiring_axis_without_advancing_tali(db):
    _org, role, app = _seed(db, workable_stage="Technical Interview")

    assert reconcile_post_handover_advanced(db, app=app, role=role) is False

    assert app.pipeline_stage == "applied"
    assert app.recruiter_stage == "interviewing"


def test_hired_records_hiring_stage_and_outcome_without_advancing_tali(db):
    _org, role, app = _seed(db, workable_stage="Hired")

    assert reconcile_post_handover_advanced(db, app=app, role=role) is False

    assert app.pipeline_stage == "applied"
    assert app.recruiter_stage == "hired"
    assert app.application_outcome == "hired"


def test_already_advanced_stays_advanced_and_syncs_detail(db):
    _org, role, app = _seed(
        db,
        workable_stage="Final Interview",
        pipeline_stage="advanced",
    )

    assert reconcile_post_handover_advanced(db, app=app, role=role) is False

    assert app.pipeline_stage == "advanced"
    assert app.recruiter_stage == "interviewing"


def test_non_hiring_stage_is_noop(db):
    _org, role, app = _seed(db, workable_stage="Applied")

    assert reconcile_post_handover_advanced(db, app=app, role=role) is False

    assert app.pipeline_stage == "applied"
    assert app.recruiter_stage is None


def test_enqueue_score_skips_disqualified(db, monkeypatch):
    from app.services import cv_score_orchestrator as orchestrator

    _org, _role, app = _seed(db, workable_stage="Applied")
    app.cv_text = "some real cv text"
    app.workable_disqualified = True
    db.add(app)
    db.commit()
    monkeypatch.setattr(
        orchestrator.settings,
        "ANTHROPIC_API_KEY",
        "test-key",
        raising=False,
    )

    assert orchestrator.enqueue_score(db, app) is None
