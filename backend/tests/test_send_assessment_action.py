"""Tests for the agent-side send_assessment action.

These exercise the action through the agent's Actor type, with the
GitHub repo provisioning running in mock mode (GITHUB_MOCK_MODE=true is
set in conftest) and the invite dispatch patched to a no-op so we don't
hit the email service. The billing gate is satisfied by giving the test
org a large credits balance.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.actions import send_assessment as send_assessment_module
from app.actions.send_assessment import run as send_assessment_run
from app.actions.types import Actor
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.task import Task


# Same SQLite-PK workaround as test_agent_runtime_tools.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_runs": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — fired by SQLA
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentRun, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_org(db) -> Organization:
    org = Organization(
        name="Send Test Org",
        slug=f"send-org-{id(db)}",
        credits_balance=1_000_000,  # plenty for the billing gate
        candidate_feedback_enabled=True,
    )
    db.add(org)
    db.flush()
    return org


def _make_task(db, org: Organization | None = None, name: str = "Coding task", task_key: str = "test-task") -> Task:
    task = Task(
        name=name,
        task_key=task_key,
        organization_id=org.id if org else None,
        repo_structure={"files": [{"path": "README.md", "content": "Welcome"}]},
        is_active=True,
    )
    db.add(task)
    db.flush()
    return task


def _make_role(db, org: Organization, *, tasks: list[Task] | None = None) -> Role:
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    if tasks:
        role.tasks = list(tasks)
    db.add(role)
    db.flush()
    return role


def _make_application(
    db, *, org: Organization, role: Role, name: str = "Cand", email: str = "c@x.test"
) -> CandidateApplication:
    candidate = Candidate(
        organization_id=org.id,
        email=email,
        full_name=name,
        position="Engineer",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _make_agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="manual",
        status="running",
        model_version="claude-3-5-haiku-latest",
        prompt_version="agent.v3.test",
    )
    db.add(run)
    db.flush()
    return run


# Patch the invite dispatch globally for these tests — we don't want emails.
@pytest.fixture(autouse=True)
def _silence_invite_dispatch():
    with patch(
        "app.domains.integrations_notifications.invite_flow.dispatch_assessment_invite",
        return_value="manual",
    ):
        yield


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_send_assessment_happy_path_creates_assessment_and_advances_to_invited(db):
    org = _make_org(db)
    task = _make_task(db, org)
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        application_id=int(app.id),
    )
    db.commit()

    assert result.status == "sent"
    assert result.assessment is not None
    assert result.assessment.candidate_id == app.candidate_id
    assert result.assessment.role_id == role.id
    assert result.assessment.task_id == task.id
    assert result.assessment.duration_minutes == 90

    # Application moved to invited.
    db.refresh(app)
    assert app.pipeline_stage == "invited"


def test_send_assessment_idempotent_when_active_assessment_exists(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="task-idem")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    first = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    db.commit()

    second = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    assert first.status == "sent"
    assert second.status == "already_exists"
    assert second.assessment.id == first.assessment.id


# ---------------------------------------------------------------------------
# Misconfiguration paths
# ---------------------------------------------------------------------------


def test_send_assessment_refuses_when_role_has_no_tasks(db):
    org = _make_org(db)
    role = _make_role(db, org, tasks=[])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        send_assessment_run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), application_id=int(app.id),
        )
    assert exc.value.status_code == 422
    assert "no tasks linked" in str(exc.value.detail).lower()


def test_send_assessment_returns_misconfigured_when_role_has_multiple_tasks(db):
    """Action returns a soft-fail status rather than raising — agents shouldn't crash."""
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="t1", name="Task 1")
    t2 = _make_task(db, org, task_key="t2", name="Task 2")
    role = _make_role(db, org, tasks=[t1, t2])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    assert result.status == "misconfigured"
    assert result.assessment is None
    assert "linked tasks" in (result.detail or "")


def test_send_assessment_picks_specific_task_when_id_passed(db):
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="pick-1", name="Task 1")
    t2 = _make_task(db, org, task_key="pick-2", name="Task 2")
    role = _make_role(db, org, tasks=[t1, t2])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
        task_id=int(t2.id),
    )
    db.commit()
    assert result.status == "sent"
    assert result.assessment.task_id == t2.id


def test_send_assessment_refuses_unrelated_task(db):
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="rel-1")
    other_task = _make_task(db, org, task_key="other-task", name="Other")
    role = _make_role(db, org, tasks=[t1])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        send_assessment_run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), application_id=int(app.id),
            task_id=int(other_task.id),
        )
    assert exc.value.status_code == 422
    assert "not linked" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# Billing gate
# ---------------------------------------------------------------------------


def test_send_assessment_returns_insufficient_credits_when_gate_fails(db):
    org = _make_org(db)
    org.credits_balance = 0  # tip the gate
    db.flush()
    task = _make_task(db, org, task_key="task-broke")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    # Force the metering reserve to fail with InsufficientCreditsError.
    from app.services.usage_metering_service import InsufficientCreditsError

    # `_meter_reserve` is imported locally inside the gate function, so we
    # patch the underlying ``reserve`` symbol on usage_metering_service.
    with patch(
        "app.services.usage_metering_service.reserve",
        side_effect=InsufficientCreditsError(
            organization_id=int(org.id), required=1, available=0
        ),
    ):
        result = send_assessment_run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), application_id=int(app.id),
        )

    assert result.status == "insufficient_credits"
    assert result.assessment is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_send_assessment_rejects_out_of_range_duration(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="dur")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    from fastapi import HTTPException

    for bad in (10, 200, 0):
        with pytest.raises(HTTPException) as exc:
            send_assessment_run(
                db, Actor.agent(int(run.id)),
                organization_id=int(org.id), application_id=int(app.id),
                duration_minutes=bad,
            )
        assert exc.value.status_code == 422


def test_send_assessment_refuses_application_without_candidate_email(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="noemail")
    role = _make_role(db, org, tasks=[task])
    candidate = Candidate(
        organization_id=org.id,
        email="",  # missing
        full_name="No Email",
        position="Eng",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    run = _make_agent_run(db, role)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        send_assessment_run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), application_id=int(app.id),
        )
    assert exc.value.status_code == 422
