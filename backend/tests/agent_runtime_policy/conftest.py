"""Shared helpers for agent_runtime policy-bridge tests."""

from __future__ import annotations

from app.decision_policy.bootstrap import bootstrap_org
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role


def make_world(db, *, jd_text: str = "Hire me", cv_text: str = "Strong python"):
    org = Organization(name="Pol Org", slug=f"pol-org-{id(db)}", default_score_threshold=65)
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        description=jd_text,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    candidate = Candidate(organization_id=org.id, email="c@x.test", full_name="C")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        cv_text=cv_text,
    )
    db.add(app)
    db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    return org, role, candidate, app


def add_event(
    db,
    *,
    application_id: int,
    organization_id: int,
    event_type: str,
    actor_type: str = "recruiter",
    to_stage: str | None = None,
    to_outcome: str | None = None,
    actor_id: int | None = 7,
) -> CandidateApplicationEvent:
    ev = CandidateApplicationEvent(
        application_id=application_id,
        organization_id=organization_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        to_stage=to_stage,
        to_outcome=to_outcome,
    )
    db.add(ev)
    db.flush()
    return ev
