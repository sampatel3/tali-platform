"""Helpers for cohort-planner tests."""

from __future__ import annotations

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


def make_world(
    db,
    *,
    cv_text: str | None = "Senior python engineer with 8y SaaS",
    pre_screen: float | None = None,
    cv_match: float | None = None,
    application_outcome: str = "open",
    pipeline_stage: str = "review",
    cv_file_url: str | None = "https://example.com/cv.pdf",
    send_requires_approval: bool = True,
    with_task: bool = False,
):
    org = Organization(name="Cohort Org", slug=f"cohort-org-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        score_threshold=65,
        # Existing fixture knob inverts to the new auto_promote flag.
        auto_promote=not send_requires_approval,
    )
    db.add(role)
    db.flush()
    # send_assessment now advances directly to interview when the role has no
    # assessment task configured. Tests exercising the send path must attach one.
    if with_task:
        task = Task(organization_id=org.id, name=f"Assessment for {role.name}")
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.flush()
    candidate = Candidate(organization_id=org.id, email=f"c{id(db)}@x.test", full_name="C")
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=pipeline_stage,
        pipeline_stage_source="recruiter",
        cv_text=cv_text,
        cv_file_url=cv_file_url,
        pre_screen_score_100=pre_screen,
        cv_match_score=cv_match,
        application_outcome=application_outcome,
    )
    db.add(app)
    db.flush()
    return org, role, candidate, app
