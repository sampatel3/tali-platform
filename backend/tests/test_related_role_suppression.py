"""Liveness and semantic parity for related-role decision suppression."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.models.agent_decision import AgentDecision
from app.models.assessment import Assessment, AssessmentStatus
from app.models.role_criterion import RoleCriterion
from app.models.task import Task
from app.models.user import User
from app.services.decision_role_staleness import related_decision_staleness
from app.services.related_role_runtime import run_related_role_cycle
from app.services.related_role_runtime_batch import _role_wide_actionable_query
from tests.test_related_role_manual_run import _related_family
from tests.test_related_role_runtime import _family, _role_local_fingerprints


def _reviewer(db, *, organization_id: int, suffix: str) -> User:
    reviewer = User(
        email=f"related-suppression-{suffix}@example.test",
        hashed_password="x",
        full_name="Related Suppression Reviewer",
        organization_id=int(organization_id),
        is_active=True,
        is_verified=True,
    )
    db.add(reviewer)
    db.flush()
    return reviewer


def test_role_wide_cycle_skips_resolved_current_generation_head_rows(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="resolved-generation-window",
        statuses=("done",) * 251,
    )
    db.commit()

    first = run_related_role_cycle(db, role=role, limit=250)
    assert first["created"] == 250
    assert first["has_more"] is True

    reviewer = _reviewer(
        db,
        organization_id=int(organization.id),
        suffix="resolved-window",
    )
    for index, decision in enumerate(db.query(AgentDecision).all()):
        decision.status = "approved" if index % 2 == 0 else "discarded"
        decision.resolved_at = datetime.now(timezone.utc)
        if decision.status == "discarded":
            decision.resolved_by_user_id = int(reviewer.id)
    db.commit()

    second = run_related_role_cycle(db, role=role, limit=250)

    tail_application, _tail_evaluation = rows[250]
    assert second["created"] == 1
    assert second.get("has_more") is not True
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(tail_application.id))
        .count()
        == 1
    )


def test_role_wide_system_discards_reprocess_once_without_pinning_tail(db):
    _organization, _owner, role, rows = _related_family(
        db,
        suffix="system-discard-window",
        statuses=("done",) * 251,
    )
    db.commit()

    assert run_related_role_cycle(db, role=role, limit=250)["created"] == 250
    for decision in db.query(AgentDecision).all():
        decision.status = "discarded"
        decision.resolved_at = datetime.now(timezone.utc)
        decision.resolved_by_user_id = None
    db.commit()

    retried = run_related_role_cycle(db, role=role, limit=250)
    assert retried["created"] == 250
    assert retried["has_more"] is True

    tail = run_related_role_cycle(db, role=role, limit=250)
    tail_application, _tail_evaluation = rows[250]
    assert tail["created"] == 1
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(tail_application.id))
        .count()
        == 1
    )


def test_role_wide_human_sub_five_point_drift_does_not_pin_tail(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="human-sub-five-window",
        statuses=("done",) * 251,
    )
    db.commit()

    assert run_related_role_cycle(db, role=role, limit=250)["created"] == 250
    reviewer = _reviewer(
        db,
        organization_id=int(organization.id),
        suffix="sub-five-window",
    )
    for decision in db.query(AgentDecision).all():
        decision.status = "discarded"
        decision.resolved_at = datetime.now(timezone.utc)
        decision.resolved_by_user_id = int(reviewer.id)
    for _application, evaluation in rows[:250]:
        evaluation.role_fit_score = 82.0
    db.commit()

    result = run_related_role_cycle(db, role=role, limit=250)

    tail_application, _tail_evaluation = rows[250]
    assert result["created"] == 1
    assert result.get("deduplicated", 0) == 0
    assert (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(tail_application.id))
        .count()
        == 1
    )


def test_approved_selector_uses_seven_day_window_and_five_point_floor(db):
    _org, _owner, _application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    evaluation.role_fit_score = 84.0
    db.commit()

    assert run_related_role_cycle(db, role=role)["created"] == 1
    decision = db.query(AgentDecision).one()
    decision.status = "approved"
    decision.resolved_at = datetime.now(timezone.utc)
    evaluation.role_fit_score = 82.0
    db.commit()

    same_bucket = run_related_role_cycle(db, role=role)
    assert same_bucket.get("created", 0) == 0
    assert same_bucket.get("deduplicated", 0) == 0

    decision.resolved_at = datetime.now(timezone.utc) - timedelta(days=8)
    db.commit()

    outside_window = run_related_role_cycle(db, role=role)
    assert outside_window["created"] == 1
    assert db.query(AgentDecision).count() == 2


def test_new_assessment_releases_human_suppressed_pre_assessment_decision(db):
    organization, _owner, application, roles, _evaluations = _family(
        db,
        related_count=1,
    )
    role = roles[0]
    assert run_related_role_cycle(db, role=role)["created"] == 1
    decision = db.query(AgentDecision).one()
    reviewer = _reviewer(
        db,
        organization_id=int(organization.id),
        suffix="new-assessment",
    )
    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    decision.resolved_by_user_id = int(reviewer.id)
    assessment_task = Task(
        organization_id=int(organization.id),
        name="Suppression generation assessment",
        description="Role-local assessment generation test",
        duration_minutes=30,
        is_active=True,
    )
    db.add(assessment_task)
    db.flush()
    assessment = Assessment(
        organization_id=int(organization.id),
        candidate_id=int(application.candidate_id),
        role_id=int(role.id),
        application_id=int(application.id),
        task_id=int(assessment_task.id),
        token="new-related-assessment-generation",
        status=AssessmentStatus.COMPLETED,
        taali_score=85.0,
    )
    db.add(assessment)
    db.commit()

    refreshed = run_related_role_cycle(db, role=role)

    assert refreshed["created"] == 1
    assert db.query(AgentDecision).count() == 2
    newest = db.query(AgentDecision).order_by(AgentDecision.id.desc()).first()
    assert newest is not None
    assert newest.evidence["assessment_id"] == int(assessment.id)


def test_postgres_selector_uses_floor_not_rounding_for_score_bucket(db):
    _organization, _owner, role, _rows = _related_family(
        db,
        suffix="postgres-floor-bucket",
        statuses=("done",),
    )
    query = _role_wide_actionable_query(
        db,
        role=role,
        threshold=70.0,
        has_assessment_stage=False,
        criteria_fingerprint=None,
    )

    sql = str(query.statement.compile(dialect=postgresql.dialect())).lower()

    assert sql.count("floor(") >= 2


def test_related_decision_staleness_detects_last_criterion_removal(db):
    _org, _owner, application, roles, evaluations = _family(db, related_count=1)
    role, evaluation = roles[0], evaluations[0]
    criterion = RoleCriterion(
        role_id=role.id,
        text="Production Python",
        bucket="must",
        weight=2.0,
        must_have=True,
    )
    db.add(criterion)
    _role_local_fingerprints(application, role, evaluation)
    db.commit()

    run_related_role_cycle(db, role=role)
    decision = db.query(AgentDecision).one()
    criterion.deleted_at = datetime.now(timezone.utc)
    db.flush()

    report = related_decision_staleness(
        db,
        decision,
        evaluation,
        application=application,
        role=role,
    )

    assert "criteria_changed" in report.reasons


@pytest.mark.parametrize("resolution", ("approved", "discarded"))
def test_malformed_historic_numeric_snapshots_do_not_starve_role_batch(
    db,
    resolution,
):
    """One corrupt audit row must not abort or monopolise a role-wide cycle."""

    organization, _owner, role, rows = _related_family(
        db,
        suffix=f"malformed-numeric-{resolution}",
        statuses=("done", "done"),
    )
    db.commit()

    # Materialise only the first candidate, leaving a second candidate behind
    # it to prove the next role-wide query remains live.
    initial = run_related_role_cycle(db, role=role, limit=1)
    assert initial["created"] == 1
    first_application, _first_evaluation = rows[0]
    first_decision = (
        db.query(AgentDecision)
        .filter(AgentDecision.application_id == int(first_application.id))
        .one()
    )
    first_decision.status = resolution
    first_decision.resolved_at = datetime.now(timezone.utc)
    if resolution == "discarded":
        reviewer = _reviewer(
            db,
            organization_id=int(organization.id),
            suffix=f"malformed-numeric-{resolution}",
        )
        first_decision.resolved_by_user_id = int(reviewer.id)

    # Cover every numeric JSON cast in the selector.  The exponent and long
    # digit strings are syntactically numeric but overflow PostgreSQL's native
    # float/integer casts; the text value also covers ordinary malformed data.
    first_decision.evidence = {
        **dict(first_decision.evidence or {}),
        "effective_threshold": "not-a-number",
        "taali_score": "1e1000000",
        "role_fit_score": "9" * 512,
        "sister_evaluation_id": "9" * 512,
        "assessment_id": "9" * 512,
        "assessment_score": "1e1000000",
    }
    first_decision.input_fingerprint = {
        **dict(first_decision.input_fingerprint or {}),
        "cv_match_score_at_emit": "not-a-number",
        "last_recruiter_note_id": "9" * 512,
    }
    db.commit()

    result = run_related_role_cycle(db, role=role, limit=250)

    second_application, _second_evaluation = rows[1]
    second_pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == int(second_application.id),
            AgentDecision.status == "pending",
        )
        .one_or_none()
    )
    assert result["status"] == "ok"
    assert second_pending is not None
