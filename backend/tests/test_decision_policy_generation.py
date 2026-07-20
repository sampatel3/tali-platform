"""Persisted generation fence for server-owned policy decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.actions import queue_decision
from app.actions.types import Actor
from app.components.scoring.freshness import capture_score_generation
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.role import Role
from app.models.rubric_revision import RubricRevision
from app.models.task import Task
from app.services import decision_staleness
from app.services.decision_approval_guard import (
    enforce_decision_approval_freshness,
)
from app.services.decision_policy_generation import (
    POLICY_GENERATION_FINGERPRINT_KEY,
)


_IDS = count(700_000)


def _assign_decision_id(_mapper, _connection, target):  # pragma: no cover
    if target.id is None:
        target.id = next(_IDS)


event.listen(AgentDecision, "before_insert", _assign_decision_id)


def _seed(db, *, with_task: bool = False, policy_revision: int | None = 11):
    suffix = next(_IDS)
    org = Organization(name="Policy fence", slug=f"policy-fence-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Engineer",
        source="manual",
        score_threshold=50,
        auto_reject_threshold_mode="manual",
        auto_promote=False,
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    task = None
    if with_task:
        task = Task(
            organization_id=int(org.id),
            name="Take-home",
            is_active=True,
        )
        db.add(task)
        db.flush()
        role.tasks.append(task)
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"candidate-{suffix}@example.test",
        full_name="Candidate",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="Python engineer",
        pre_screen_score_100=80.0,
        cv_match_score=80.0,
    )
    db.add(app)
    run = AgentRun(
        id=next(_IDS),
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    policy = None
    if policy_revision is not None:
        revision = RubricRevision(
            id=int(policy_revision),
            organization_id=int(org.id),
            role_id=None,
            cause="human_edit",
            feedback_ids=[],
        )
        policy = DecisionPolicy(
            id=next(_IDS),
            organization_id=int(org.id),
            role_id=None,
            revision_id=int(policy_revision),
            policy_json={},
            activated_at=datetime.now(timezone.utc),
        )
        db.add_all([revision, policy])
    db.commit()
    return org, role, task, app, run, policy


def _evidence(*, has_task: bool, revision: int | None, threshold: float = 50.0):
    return {
        "decision_source": "policy",
        "effective_threshold": threshold,
        "has_assessment_task": has_task,
        "policy_revision_id": revision,
    }


def _queue(db, *, org, role, app, run, evidence):
    generation = capture_score_generation(
        db,
        role=role,
        application_id=int(app.id),
    )
    assert generation is not None
    return queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type="advance_to_interview",
        reasoning="Strong policy match.",
        evidence=evidence,
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        skip_episode=True,
        expected_score_generation=generation,
    )


def _activate_replacement(db, *, org, previous, revision_id: int):
    now = datetime.now(timezone.utc)
    previous.deactivated_at = now
    revision = RubricRevision(
        id=int(revision_id),
        organization_id=int(org.id),
        role_id=None,
        cause="human_edit",
        feedback_ids=[],
    )
    policy = DecisionPolicy(
        id=next(_IDS),
        organization_id=int(org.id),
        role_id=None,
        revision_id=int(revision_id),
        policy_json={},
        activated_at=now,
    )
    db.add_all([revision, policy])
    db.commit()


@pytest.mark.parametrize(
    "race",
    ["threshold", "task_link", "task_deactivate", "auto_skip", "policy_revision"],
)
def test_queue_rejects_policy_evidence_from_an_older_generation(db, race):
    starts_with_task = race in {"task_deactivate", "auto_skip"}
    org, role, task, app, run, policy = _seed(db, with_task=starts_with_task)
    evidence = _evidence(
        has_task=starts_with_task,
        revision=11,
    )

    if race == "threshold":
        role.score_threshold = 60
        db.commit()
    elif race == "task_link":
        task = Task(organization_id=int(org.id), name="New task", is_active=True)
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.commit()
    elif race == "task_deactivate":
        task.is_active = False
        db.commit()
    elif race == "auto_skip":
        role.auto_skip_assessment = True
        db.commit()
    else:
        _activate_replacement(db, org=org, previous=policy, revision_id=12)

    with pytest.raises(HTTPException) as exc_info:
        _queue(
            db,
            org=org,
            role=role,
            app=app,
            run=run,
            evidence=evidence,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "decision_policy_generation_changed"
    assert db.query(AgentDecision).count() == 0


@pytest.mark.parametrize(
    "change",
    [
        "threshold",
        "task_link",
        "task_deactivate",
        "auto_skip",
        "automation",
        "policy_revision",
    ],
)
def test_live_policy_generation_change_stales_and_blocks_approval(db, change):
    starts_with_task = change in {"task_deactivate", "auto_skip"}
    org, role, task, app, run, policy = _seed(db, with_task=starts_with_task)
    decision = _queue(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence=_evidence(has_task=starts_with_task, revision=11),
    )
    db.commit()

    if change == "threshold":
        role.score_threshold = 65
        db.commit()
    elif change == "task_link":
        task = Task(organization_id=int(org.id), name="Linked later", is_active=True)
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.commit()
    elif change == "task_deactivate":
        task.is_active = False
        db.commit()
    elif change == "auto_skip":
        role.auto_skip_assessment = True
        db.commit()
    elif change == "automation":
        role.auto_advance = True
        db.commit()
    else:
        _activate_replacement(db, org=org, previous=policy, revision_id=12)

    report = decision_staleness.evaluate(db, decision)

    assert "policy_generation_changed" in report.reasons
    assert report.details["policy_generation_changed"]["at_emit"] != (
        report.details["policy_generation_changed"]["current"]
    )
    with pytest.raises(HTTPException) as exc_info:
        enforce_decision_approval_freshness(
            db,
            decision,
            allow_engine_outdated=True,
        )
    assert exc_info.value.status_code == 409
    assert "policy_generation_changed" in exc_info.value.detail["reasons"]


def test_explicit_none_policy_revision_is_compatible_and_persisted(db):
    org, role, _task, app, run, _policy = _seed(db, policy_revision=None)

    decision = _queue(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence=_evidence(has_task=False, revision=None),
    )
    db.commit()

    token = decision.input_fingerprint[POLICY_GENERATION_FINGERPRINT_KEY]
    assert token["policy_revision_id"] is None
    assert token["active_assessment_task_ids"] == []
    assert token["auto_skip_assessment"] is False
    assert token["automation"] == {
        "auto_send_assessment": False,
        "auto_resend_assessment": False,
        "auto_advance": False,
    }
    assert decision_staleness.evaluate(db, decision).is_stale is False


def test_malformed_persisted_policy_generation_fails_closed(db):
    org, role, _task, app, run, _policy = _seed(db)
    decision = _queue(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence=_evidence(has_task=False, revision=11),
    )
    decision.input_fingerprint = {
        **decision.input_fingerprint,
        POLICY_GENERATION_FINGERPRINT_KEY: "invalid",
    }
    db.commit()

    report = decision_staleness.evaluate(db, decision)

    assert "policy_generation_changed" in report.reasons
    with pytest.raises(HTTPException) as exc_info:
        enforce_decision_approval_freshness(
            db,
            decision,
            allow_engine_outdated=True,
        )
    assert exc_info.value.status_code == 409
    assert "policy_generation_changed" in exc_info.value.detail["reasons"]


def test_staleness_policy_generation_queries_once_per_role(db):
    org, role, _task, first_app, first_run, _policy = _seed(db)
    evidence = _evidence(has_task=False, revision=11)
    first = _queue(
        db,
        org=org,
        role=role,
        app=first_app,
        run=first_run,
        evidence=evidence,
    )
    candidate = Candidate(
        organization_id=int(org.id),
        email=f"candidate-{next(_IDS)}@example.test",
        full_name="Second Candidate",
    )
    db.add(candidate)
    db.flush()
    second_app = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="Second Python engineer",
        pre_screen_score_100=80.0,
        cv_match_score=80.0,
    )
    second_run = AgentRun(
        id=next(_IDS),
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="running",
    )
    db.add_all([second_app, second_run])
    db.commit()
    second = _queue(
        db,
        org=org,
        role=role,
        app=second_app,
        run=second_run,
        evidence=evidence,
    )
    db.commit()
    role.auto_advance = True
    db.commit()

    queries = {"tasks": 0, "policies": 0}

    def count_generation_queries(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        sql = str(statement).lower()
        if "role_tasks" in sql:
            queries["tasks"] += 1
        if "decision_policies" in sql:
            queries["policies"] += 1

    event.listen(db.bind, "before_cursor_execute", count_generation_queries)
    try:
        cache = decision_staleness.StalenessCache()
        first_report = decision_staleness.evaluate(
            db,
            first,
            application=first_app,
            role=role,
            cache=cache,
        )
        second_report = decision_staleness.evaluate(
            db,
            second,
            application=second_app,
            role=role,
            cache=cache,
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", count_generation_queries)

    assert "policy_generation_changed" in first_report.reasons
    assert "policy_generation_changed" in second_report.reasons
    assert queries == {"tasks": 1, "policies": 1}
    assert list(cache.policy_generation) == [int(role.id)]


def test_approved_old_policy_generation_does_not_dedup_new_card(db):
    org, role, _task, app, run, _policy = _seed(db)
    evidence = _evidence(has_task=False, revision=11)
    first = _queue(
        db,
        org=org,
        role=role,
        app=app,
        run=run,
        evidence=evidence,
    )
    first.status = "approved"
    first.resolved_at = datetime.now(timezone.utc)
    role.auto_advance = True
    replacement_run = AgentRun(
        id=next(_IDS),
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="running",
    )
    db.add(replacement_run)
    db.commit()

    replacement = _queue(
        db,
        org=org,
        role=role,
        app=app,
        run=replacement_run,
        evidence=evidence,
    )

    assert replacement.id != first.id
    assert replacement.status == "pending"
    assert replacement.decision_dedup_key != first.decision_dedup_key
