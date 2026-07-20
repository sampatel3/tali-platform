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
from fastapi import HTTPException
from sqlalchemy import event

from app.actions.send_assessment import run as _send_assessment_run
from app.actions.types import Actor
from app.components.scoring.freshness import capture_score_generation
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.organization import Organization
from app.models.role import Role, role_tasks
from app.models.task import Task
from app.models.assessment_experiment import (
    ASSIGNMENT_METHOD_FORCED,
    ASSIGNMENT_METHOD_RANDOM,
    ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT,
    EXPERIMENT_STATUS_ACTIVE,
    AssessmentExperiment,
    AssessmentExperimentArm,
)


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
        auto_send_assessment=True,
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
        cv_match_score=80.0,
    )
    db.add(app)
    db.flush()
    return app


def _make_experiment(
    db,
    org: Organization,
    role: Role,
    arm_tasks: list[Task],
    *,
    status: str = EXPERIMENT_STATUS_ACTIVE,
    knob_overrides: list[dict | None] | None = None,
    weights: list[int] | None = None,
    key: str = "exp-ab",
    salt: str = "fixed-salt",
) -> AssessmentExperiment:
    exp = AssessmentExperiment(
        organization_id=org.id,
        role_id=role.id,
        key=key,
        name="A/B trial",
        status=status,
        experiment_type="task",
        salt=salt,
    )
    db.add(exp)
    db.flush()
    for idx, task in enumerate(arm_tasks):
        arm = AssessmentExperimentArm(
            experiment_id=exp.id,
            arm_key=chr(ord("A") + idx),
            task_id=task.id,
            weight=(weights[idx] if weights else 1),
            knob_overrides=(knob_overrides[idx] if knob_overrides else None),
            is_active=True,
        )
        db.add(arm)
    db.flush()
    db.refresh(exp)
    return exp


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


def send_assessment_run(db, actor, **kwargs):
    """Invoke the automatic action with the producer's score provenance.

    Production callers capture this token while evaluating the candidate. The
    action intentionally refuses an automatic send when it is omitted, so the
    mechanics-focused tests use the same explicit contract.
    """
    application = db.get(CandidateApplication, int(kwargs["application_id"]))
    acting_role_id = kwargs.get("role_id") or getattr(application, "role_id", None)
    role = db.get(Role, int(acting_role_id)) if acting_role_id is not None else None
    kwargs.setdefault(
        "expected_score_generation",
        (
            capture_score_generation(
                db, role=role, application_id=int(application.id)
            )
            if application is not None and role is not None
            else None
        ),
    )
    kwargs.setdefault("expected_decision_type", "send_assessment")
    return _send_assessment_run(db, actor, **kwargs)


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


def test_send_assessment_happy_path_creates_assessment_and_queues_delivery(db):
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

    assert result.status == "queued"
    assert result.assessment is not None
    assert result.assessment.candidate_id == app.candidate_id
    assert result.assessment.role_id == role.id
    assert result.assessment.task_id == task.id
    # Default duration now comes from task.duration_minutes (30) rather than a
    # hardcoded 90 — Sam's "60 min is way too long" feedback after the pilot
    # dry-run. Explicit override + experiment-knob still take precedence.
    assert result.assessment.duration_minutes == 30

    # Queue acceptance is not candidate contact. Provider confirmation owns the
    # later atomic transition to invited.
    db.refresh(app)
    assert app.pipeline_stage == "review"


def test_send_assessment_does_not_claim_sent_when_queue_rejects_invite(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="queue-down")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    run_id = int(run.id)
    with patch(
        "app.domains.integrations_notifications.invite_flow.dispatch_assessment_invite",
        side_effect=RuntimeError("broker down"),
    ), pytest.raises(HTTPException) as exc:
        # Mirrors maybe_auto_execute_decision: the decision/run is flushed in
        # the outer transaction and the action runs inside a caller savepoint.
        with db.begin_nested():
            send_assessment_run(
                db,
                Actor.agent(run_id),
                organization_id=int(org.id),
                application_id=int(app.id),
            )

    assert exc.value.status_code == 503
    assert "no send was confirmed" in str(exc.value.detail).lower()
    assert db.query(AgentRun).filter(AgentRun.id == run_id).one_or_none() is not None
    db.refresh(app)
    assert app.pipeline_stage == "review"
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0
    db.commit()


def test_send_assessment_repo_failure_preserves_callers_outer_transaction(db):
    """Repository failure must roll back only the action, not its decision/run."""
    from app.services.assessment_repository_service import AssessmentRepositoryError

    org = _make_org(db)
    task = _make_task(db, org, task_key="repo-down")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)
    run_id = int(run.id)

    with patch(
        "app.actions.send_assessment.AssessmentRepositoryService.create_assessment_branch",
        side_effect=AssessmentRepositoryError("github down"),
    ), pytest.raises(HTTPException) as exc:
        with db.begin_nested():
            send_assessment_run(
                db,
                Actor.agent(run_id),
                organization_id=int(org.id),
                application_id=int(app.id),
            )

    assert exc.value.status_code == 500
    assert db.query(AgentRun).filter(AgentRun.id == run_id).one_or_none() is not None
    db.refresh(app)
    assert app.pipeline_stage == "review"
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0
    db.commit()


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
    assert first.status == "queued"
    assert second.status == "already_exists"
    assert second.assessment.id == first.assessment.id


def test_automatic_send_is_held_when_live_role_is_disabled(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="disabled-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)
    role.agentic_mode_enabled = False
    db.flush()

    result = send_assessment_run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        application_id=int(app.id),
    )

    assert result.status == "blocked"
    assert result.assessment is None
    assert "disabled" in (result.detail or "")
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


def test_automatic_send_rechecks_live_send_toggle_before_provisioning(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="toggle-off-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    db.query(Role).filter(Role.id == role.id).update(
        {"auto_send_assessment": False}, synchronize_session=False
    )
    assert role.auto_send_assessment is True

    with patch(
        "app.actions.send_assessment.AssessmentRepositoryService",
        side_effect=AssertionError("disabled send must stop before provisioning"),
    ):
        result = send_assessment_run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert result.status == "blocked"
    assert "auto_send_assessment is disabled" in (result.detail or "")
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


def test_automatic_send_rechecks_live_auto_skip_policy_before_provisioning(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="skip-policy-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    db.query(Role).filter(Role.id == role.id).update(
        {"auto_skip_assessment": True}, synchronize_session=False
    )
    assert role.auto_skip_assessment is False

    with patch(
        "app.actions.send_assessment.AssessmentRepositoryService",
        side_effect=AssertionError("skipped stage must stop before provisioning"),
    ):
        result = send_assessment_run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert result.status == "blocked"
    assert "now skips assessments" in (result.detail or "")
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


def test_automatic_send_reloads_live_task_links_before_provisioning(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="unlinked-policy-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    # The worker's role.tasks collection is stale, but the durable role policy
    # no longer links this task.
    db.execute(
        role_tasks.delete().where(
            role_tasks.c.role_id == int(role.id),
            role_tasks.c.task_id == int(task.id),
        )
    )
    assert [int(row.id) for row in role.tasks] == [int(task.id)]

    with patch(
        "app.actions.send_assessment.AssessmentRepositoryService",
        side_effect=AssertionError("unlinked task must stop before provisioning"),
    ):
        result = send_assessment_run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert result.status == "misconfigured"
    assert "no active tasks linked" in (result.detail or "")
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


def test_automatic_send_reloads_live_task_active_state_before_provisioning(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="deactivated-policy-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    # Simulate a Task mutation committed while this worker still holds a stale
    # role.tasks identity-map collection.
    db.query(Task).filter(Task.id == task.id).update(
        {"is_active": False}, synchronize_session=False
    )
    assert task.is_active is True

    with patch(
        "app.actions.send_assessment.AssessmentRepositoryService",
        side_effect=AssertionError("inactive task must stop before provisioning"),
    ):
        result = send_assessment_run(
            db,
            Actor.agent(int(run.id)),
            organization_id=int(org.id),
            application_id=int(app.id),
        )

    assert result.status == "misconfigured"
    assert "no active tasks linked" in (result.detail or "")
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


def test_automatic_send_is_held_when_score_generation_was_replaced(db):
    org = _make_org(db)
    task = _make_task(db, org, task_key="replacement-score-send")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)
    generation_a = capture_score_generation(
        db, role=role, application_id=int(app.id)
    )
    assert generation_a is not None and generation_a.job_id is None
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
        )
    )
    db.flush()

    result = send_assessment_run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        application_id=int(app.id),
        expected_score_generation=generation_a,
    )

    assert result.status == "blocked"
    assert result.assessment is None
    assert "score refresh" in (result.detail or "").lower()
    assert db.query(Assessment).filter(Assessment.application_id == app.id).count() == 0


# ---------------------------------------------------------------------------
# Misconfiguration paths
# ---------------------------------------------------------------------------


def test_send_assessment_returns_misconfigured_when_role_has_no_tasks(db):
    """A role with zero linked tasks is a recruiter-config gap, not a crash:
    the action returns a soft ``misconfigured`` status (same as the ambiguous
    multiple-tasks case) so approving the agent's recommendation surfaces a
    clear signal instead of 422-ing and re-queueing the decision in a loop."""
    org = _make_org(db)
    role = _make_role(db, org, tasks=[])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    assert result.status == "misconfigured"
    assert result.assessment is None
    assert "no active tasks linked" in (result.detail or "").lower()


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
    assert result.status == "queued"
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


# ---------------------------------------------------------------------------
# A/B experiment assignment
# ---------------------------------------------------------------------------


def test_send_assessment_legacy_single_task_records_method(db):
    """Role with one task and no experiment → single_task_default, no experiment."""
    org = _make_org(db)
    task = _make_task(db, org, task_key="legacy-single")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    db.commit()
    assert result.status == "queued"
    assert result.assessment.assignment_method == ASSIGNMENT_METHOD_SINGLE_TASK_DEFAULT
    assert result.assessment.experiment_id is None
    assert result.assessment.experiment_arm_id is None


def test_send_assessment_random_assignment_records_arm_metadata(db):
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="ab-a", name="Arm A")
    t2 = _make_task(db, org, task_key="ab-b", name="Arm B")
    role = _make_role(db, org, tasks=[t1, t2])
    exp = _make_experiment(db, org, role, [t1, t2])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    db.commit()
    assert result.status == "queued"
    a = result.assessment
    assert a.assignment_method == ASSIGNMENT_METHOD_RANDOM
    assert a.experiment_id == exp.id
    assert a.experiment_arm_id is not None
    assert a.assignment_key == f"{exp.id}:{app.candidate_id}:{role.id}"
    assert a.task_id in (t1.id, t2.id)


def test_send_assessment_random_split_uses_both_arms(db):
    """Across many candidates the deterministic hash spreads over both arms."""
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="split-a", name="Arm A")
    t2 = _make_task(db, org, task_key="split-b", name="Arm B")
    role = _make_role(db, org, tasks=[t1, t2])
    _make_experiment(db, org, role, [t1, t2])
    run = _make_agent_run(db, role)

    seen: set[int] = set()
    for i in range(24):
        app = _make_application(db, org=org, role=role, email=f"split-{i}@x.test")
        result = send_assessment_run(
            db, Actor.agent(int(run.id)),
            organization_id=int(org.id), application_id=int(app.id),
        )
        db.commit()
        assert result.status == "queued"
        seen.add(result.assessment.task_id)
    assert seen == {t1.id, t2.id}


def test_send_assessment_forced_task_id_under_active_experiment(db):
    """An explicit task_id is recorded as forced (excluded from the random cohort)."""
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="forced-a", name="Arm A")
    t2 = _make_task(db, org, task_key="forced-b", name="Arm B")
    role = _make_role(db, org, tasks=[t1, t2])
    _make_experiment(db, org, role, [t1, t2])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
        task_id=int(t1.id),
    )
    db.commit()
    assert result.status == "queued"
    assert result.assessment.assignment_method == ASSIGNMENT_METHOD_FORCED
    assert result.assessment.task_id == t1.id


def test_send_assessment_knob_override_duration_and_weights(db):
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="knob-a", name="Arm A")
    t2 = _make_task(db, org, task_key="knob-b", name="Arm B")
    role = _make_role(db, org, tasks=[t1, t2])
    knobs = {"duration_minutes": 45, "score_weights": {"task_completion": 0.5}, "calibration_enabled": False}
    _make_experiment(db, org, role, [t1, t2], knob_overrides=[knobs, knobs])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    result = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
        duration_minutes=90,  # overridden by the knob
    )
    db.commit()
    a = result.assessment
    assert a.duration_minutes == 45
    assert a.knob_variant_applied == knobs
    assert a.score_weights_override == {"task_completion": 0.5}
    assert a.calibration_enabled is False


def test_send_assessment_arm_stable_across_void_and_reinvite(db):
    org = _make_org(db)
    t1 = _make_task(db, org, task_key="reinvite-a", name="Arm A")
    t2 = _make_task(db, org, task_key="reinvite-b", name="Arm B")
    role = _make_role(db, org, tasks=[t1, t2])
    _make_experiment(db, org, role, [t1, t2])
    app = _make_application(db, org=org, role=role)
    run = _make_agent_run(db, role)

    first = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    db.commit()
    first_arm = first.assessment.experiment_arm_id

    # Void the first attempt, then re-invite.
    voided = db.query(Assessment).filter(Assessment.id == first.assessment.id).first()
    voided.is_voided = True
    from app.components.assessments.repository import utcnow
    voided.voided_at = utcnow()
    db.commit()

    second = send_assessment_run(
        db, Actor.agent(int(run.id)),
        organization_id=int(org.id), application_id=int(app.id),
    )
    db.commit()
    assert second.status == "queued"
    assert second.assessment.id != first.assessment.id
    assert second.assessment.experiment_arm_id == first_arm


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
        cv_match_score=80.0,
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


@pytest.mark.parametrize(
    ("application_outcome", "pipeline_stage"),
    [("rejected", "review"), ("hired", "review"), ("open", "advanced")],
)
def test_send_assessment_blocks_resolved_application(
    db, application_outcome, pipeline_stage
):
    org = _make_org(db)
    task = _make_task(db, org, task_key=f"resolved-{application_outcome}-{pipeline_stage}")
    role = _make_role(db, org, tasks=[task])
    app = _make_application(db, org=org, role=role)
    app.application_outcome = application_outcome
    app.pipeline_stage = pipeline_stage
    run = _make_agent_run(db, role)
    db.flush()

    result = send_assessment_run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        application_id=int(app.id),
    )

    assert result.status == "blocked"
    assert "resolved" in (result.detail or "").lower()
    assert (
        db.query(Assessment)
        .filter(Assessment.application_id == int(app.id))
        .count()
        == 0
    )
