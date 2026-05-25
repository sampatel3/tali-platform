"""Tests for the A/B experiment comparison analytics endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.assessment import Assessment, AssessmentStatus
from app.models.assessment_experiment import (
    AssessmentExperiment,
    AssessmentExperimentArm,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.task import Task
from app.models.user import User

from .conftest import auth_headers


def _org_id_for_email(db, email: str) -> int:
    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    return int(user.organization_id)


def _completed_assessment(db, **kw) -> Assessment:
    base = dict(
        status=AssessmentStatus.COMPLETED,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        completed_at=datetime.now(timezone.utc),
        completed_due_to_timeout=False,
        assignment_method="random",
        is_voided=False,
        total_duration_seconds=1800,
        tab_switch_count=1,
    )
    base.update(kw)
    a = Assessment(**base)
    db.add(a)
    db.flush()
    return a


def test_experiment_comparison_groups_arms_and_signals(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id_for_email(db, email)

    t1 = Task(name="Arm A", task_key="cmp-a", organization_id=org_id, is_active=True)
    t2 = Task(name="Arm B", task_key="cmp-b", organization_id=org_id, is_active=True)
    db.add_all([t1, t2])
    db.flush()
    role = Role(organization_id=org_id, name="GenAI Eng", source="manual")
    role.tasks = [t1, t2]
    db.add(role)
    db.flush()

    exp = AssessmentExperiment(
        organization_id=org_id, role_id=role.id, key="cmp-exp",
        name="GenAI A/B", status="active", experiment_type="task", salt="s",
    )
    db.add(exp)
    db.flush()
    arm_a = AssessmentExperimentArm(experiment_id=exp.id, arm_key="A", task_id=t1.id, weight=1, is_active=True)
    arm_b = AssessmentExperimentArm(experiment_id=exp.id, arm_key="B", task_id=t2.id, weight=1, is_active=True)
    db.add_all([arm_a, arm_b])
    db.flush()

    # One candidate on arm A advanced; the rest open.
    def _app(stage="review", outcome="open", suffix="x"):
        cand = Candidate(organization_id=org_id, email=f"cmp-{suffix}@t.test", full_name="C")
        db.add(cand)
        db.flush()
        ap = CandidateApplication(
            organization_id=org_id, candidate_id=cand.id, role_id=role.id,
            status="applied", pipeline_stage=stage, pipeline_stage_source="recruiter",
            application_outcome=outcome, source="manual",
        )
        db.add(ap)
        db.flush()
        return cand, ap

    c1, a1 = _app(stage="advanced", outcome="hired", suffix="1")
    c2, a2 = _app(suffix="2")
    c3, a3 = _app(suffix="3")

    _completed_assessment(
        db, organization_id=org_id, candidate_id=c1.id, application_id=a1.id, role_id=role.id,
        task_id=t1.id, token="cmp-tok-1", experiment_id=exp.id, experiment_arm_id=arm_a.id, taali_score=82.0,
    )
    _completed_assessment(
        db, organization_id=org_id, candidate_id=c2.id, application_id=a2.id, role_id=role.id,
        task_id=t1.id, token="cmp-tok-2", experiment_id=exp.id, experiment_arm_id=arm_a.id, taali_score=44.0,
    )
    _completed_assessment(
        db, organization_id=org_id, candidate_id=c3.id, application_id=a3.id, role_id=role.id,
        task_id=t2.id, token="cmp-tok-3", experiment_id=exp.id, experiment_arm_id=arm_b.id, taali_score=61.0,
    )
    db.commit()

    resp = client.get(f"/api/v1/analytics/experiments/comparison?experiment_id={exp.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["min_sample_threshold"] == 20
    assert body["winner"] is None  # pilot — never auto-declared below threshold
    arms = {a["arm_key"]: a for a in body["arms"]}
    assert set(arms) == {"A", "B"}
    assert arms["A"]["n_completed"] == 2
    assert arms["B"]["n_completed"] == 1
    assert arms["A"]["small_sample"] is True
    # Discrimination: arm A spread between 44 and 82.
    assert arms["A"]["discrimination"]["score"]["count"] == 2
    assert arms["A"]["discrimination"]["score"]["min"] == 44.0
    assert arms["A"]["discrimination"]["score"]["max"] == 82.0
    # Outcome: arm A has one advanced/hired candidate (denominator present).
    assert arms["A"]["outcome"]["advanced"] == 1
    assert arms["A"]["outcome"]["hired"] == 1
    assert arms["A"]["outcome"]["n_with_application"] == 2


def test_experiment_comparison_lists_experiments_without_id(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id_for_email(db, email)
    task = Task(name="DE Task", task_key="list-de", organization_id=org_id, is_active=True)
    db.add(task)
    db.flush()
    role = Role(organization_id=org_id, name="Data Eng", source="manual")
    db.add(role)
    db.flush()
    exp = AssessmentExperiment(
        organization_id=org_id, role_id=role.id, key="list-exp",
        name="Data A/B", status="active", experiment_type="task", salt="s",
    )
    db.add(exp)
    db.flush()
    db.add(AssessmentExperimentArm(experiment_id=exp.id, arm_key="A", task_id=task.id, weight=1, is_active=True))
    db.commit()

    resp = client.get("/api/v1/analytics/experiments/comparison", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = {e["key"] for e in body["experiments"]}
    assert "list-exp" in keys
