"""Helpers for cohort-planner tests."""

from __future__ import annotations

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_intent import RoleIntent
from app.models.task import Task


_BIG_PK_COUNTERS: dict[str, int] = {
    "agent_runs": 0,
    "agent_needs_input": 0,
    "agent_decisions": 0,
    "role_intents": 0,
}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


for _model in (AgentRun, AgentNeedsInput, AgentDecision, RoleIntent):
    event.listen(_model, "before_insert", _assign_big_pk)


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
