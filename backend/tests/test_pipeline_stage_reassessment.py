"""Re-assessment stage transitions.

Regression for the 2026-06-25 incident: starting a (re)assessment 500'd with
"Failed to start assessment session" when the candidate's application was in the
`review` pipeline stage.

``start_or_resume_assessment`` moves the application to ``in_assessment`` via
``transition_stage(..., source="system")`` when a PENDING/never-started
assessment is started. The system guard only whitelisted ``invited ->
in_assessment`` and ``in_assessment -> review``, so a candidate already in
``review`` — a prior attempt submitted, or auto-finalized on timeout (PR #698) —
hit ``409 System transition review->in_assessment is not allowed``, which the
start service swallowed and re-raised as a generic 500.

The fix whitelists ``review -> in_assessment`` for the system source. These tests
pin that edge open, confirm the normal ``invited -> in_assessment`` start still
works, and confirm the guard stays strict for transitions we did NOT open.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.pipeline_service import transition_stage
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role


def _seed_app(db, *, stage: str, outcome: str = "open") -> CandidateApplication:
    org = Organization(name="Reassess Org", slug=f"reassess-{stage}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(
        organization_id=org.id, email=f"c-{stage}-{id(db)}@x.test", full_name="Candidate"
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="system",
        application_outcome=outcome,
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _start_assessment_transition(db, app: CandidateApplication) -> CandidateApplication:
    """Exactly the call ``start_or_resume_assessment`` makes when a candidate
    starts a freshly issued assessment."""
    return transition_stage(
        db,
        app=app,
        to_stage="in_assessment",
        source="system",
        actor_type="system",
        reason="Candidate started assessment",
        metadata={"assessment_id": 1234},
    )


def test_review_to_in_assessment_is_allowed_for_reassessment(db):
    """The fix: a candidate in `review` can start a new assessment."""
    app = _seed_app(db, stage="review")

    _start_assessment_transition(db, app)
    db.commit()

    assert app.pipeline_stage == "in_assessment"
    # status_from_pipeline maps the in_assessment stage to the legacy in_progress.
    assert app.status == "in_progress"

    event = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "pipeline_stage_changed",
        )
        .order_by(CandidateApplicationEvent.id.desc())
        .first()
    )
    assert event is not None
    assert event.from_stage == "review"
    assert event.to_stage == "in_assessment"


def test_invited_to_in_assessment_still_works(db):
    """Regression guard: the normal first-start path must be untouched."""
    app = _seed_app(db, stage="invited")

    _start_assessment_transition(db, app)
    db.commit()

    assert app.pipeline_stage == "in_assessment"
    assert app.status == "in_progress"


def test_system_guard_stays_strict_for_unopened_edges(db):
    """We opened exactly one edge — `applied -> in_assessment` (no invite first)
    must still be rejected by the system guard, so the fix isn't a free-for-all."""
    app = _seed_app(db, stage="applied")

    with pytest.raises(HTTPException) as excinfo:
        _start_assessment_transition(db, app)

    assert excinfo.value.status_code == 409
    assert "applied->in_assessment" in str(excinfo.value.detail)
    # The rejected transition must not have mutated the row.
    assert app.pipeline_stage == "applied"
