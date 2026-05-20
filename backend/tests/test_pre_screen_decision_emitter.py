"""Pre-screen failures surface as Decision Hub cards instead of being
silently parked. Covers the new system-side emitter + the one-shot
backfill that catches up historical stranded apps.
"""
from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.pre_screen_decision_emitter import (
    backfill_existing_below_threshold,
    queue_pre_screen_reject,
)


def _seed(db, *, score: float | None = 35.0, threshold: float | None = 50.0, outcome: str = "open"):
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False)
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        pre_screen_score_100=score,
    )
    db.add(app); db.flush()
    return org, role, app


def test_queue_pre_screen_reject_creates_pending_decision(db):
    org, role, app = _seed(db, score=35.0, threshold=50.0)
    decision = queue_pre_screen_reject(
        db,
        organization_id=int(org.id),
        role=role,
        application=app,
        pre_screen_score=35.0,
        threshold=50.0,
    )
    assert decision is not None
    assert decision.decision_type == "skip_assessment_reject"
    assert decision.status == "pending"
    assert decision.agent_run_id is None  # system-emitted
    assert decision.application_id == app.id
    assert decision.role_id == role.id
    # Reasoning string includes both numbers so the recruiter can see why.
    assert "35" in (decision.reasoning or "")
    assert "50" in (decision.reasoning or "")


def test_queue_pre_screen_reject_is_idempotent(db):
    org, role, app = _seed(db)
    a = queue_pre_screen_reject(db, organization_id=org.id, role=role, application=app, pre_screen_score=35.0, threshold=50.0)
    b = queue_pre_screen_reject(db, organization_id=org.id, role=role, application=app, pre_screen_score=35.0, threshold=50.0)
    assert a is not None and b is not None
    assert a.id == b.id  # same row returned both times
    n = db.query(AgentDecision).filter(AgentDecision.application_id == app.id).count()
    assert n == 1


def test_backfill_creates_decisions_for_existing_below_threshold(db):
    """Simulates the prod scenario: 3 apps below threshold, all stranded
    (no decision rows yet). Backfill should create one decision per app."""
    org = Organization(name="Backfill Org", slug=f"bf-{id(db)}")
    db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", auto_reject=False)
    db.add(role); db.flush()
    for i in range(3):
        cand = Candidate(organization_id=org.id, email=f"c{i}@x.test", full_name=f"C{i}")
        db.add(cand); db.flush()
        db.add(
            CandidateApplication(
                organization_id=org.id,
                candidate_id=cand.id,
                role_id=role.id,
                status="applied",
                pipeline_stage="review",
                pipeline_stage_source="recruiter",
                application_outcome="open",
                source="manual",
                pre_screen_score_100=20.0 + i,  # all < 50
            )
        )
    # One control: score above threshold should NOT get a decision.
    cand = Candidate(organization_id=org.id, email="ok@x.test", full_name="OK")
    db.add(cand); db.flush()
    db.add(
        CandidateApplication(
            organization_id=org.id,
            candidate_id=cand.id,
            role_id=role.id,
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            source="manual",
            pre_screen_score_100=85.0,
        )
    )
    db.commit()

    summary = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary["created"] == 3
    assert summary["skipped_existing"] == 0
    assert summary["failed"] == 0

    # Re-running is a no-op.
    summary2 = backfill_existing_below_threshold(db, organization_id=int(org.id))
    assert summary2["created"] == 0
    assert summary2["skipped_existing"] == 3
