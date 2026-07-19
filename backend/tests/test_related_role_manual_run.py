"""Manual related-role scoring and completed-score handoff contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.sister_role_service import text_fingerprint


def _related_family(
    db,
    *,
    suffix: str,
    statuses: tuple[str, ...],
) -> tuple[
    Organization, Role, Role, list[tuple[CandidateApplication, SisterRoleEvaluation]]
]:
    organization = Organization(
        name=f"Related manual {suffix}",
        slug=f"related-manual-{suffix}-{id(db)}",
        credits_balance=100_000_000,
    )
    db.add(organization)
    db.flush()
    owner = Role(
        organization_id=int(organization.id),
        name=f"Related owner {suffix}",
        source="manual",
        job_spec_text="Owner role",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=int(organization.id),
        name=f"Related role {suffix}",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        agentic_mode_enabled=True,
        job_spec_text="Related platform role with production ownership.",
        score_threshold=70,
        auto_skip_assessment=True,
    )
    db.add(related)
    db.flush()

    rows: list[tuple[CandidateApplication, SisterRoleEvaluation]] = []
    for index, status in enumerate(statuses):
        candidate = Candidate(
            organization_id=int(organization.id),
            full_name=f"Related candidate {suffix} {index}",
            email=f"related-manual-{suffix}-{index}@example.test",
            cv_text="Python, distributed systems, and production ownership.",
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=int(organization.id),
            role_id=int(owner.id),
            candidate_id=int(candidate.id),
            source="manual",
            application_outcome="open",
            pipeline_stage="review",
            cv_text=candidate.cv_text,
        )
        db.add(application)
        db.flush()
        evaluation = SisterRoleEvaluation(
            organization_id=int(organization.id),
            role_id=int(related.id),
            source_application_id=int(application.id),
            status=status,
            spec_fingerprint=text_fingerprint(related.job_spec_text),
            cv_fingerprint=text_fingerprint(application.cv_text),
            role_fit_score=85.0 if status == "done" else None,
        )
        db.add(evaluation)
        db.flush()
        rows.append((application, evaluation))
    return organization, owner, related, rows


def _manual_intent(db, *, role: Role, application_id: int | None, key: str) -> AgentRun:
    from app.services.manual_agent_run_dispatch import ensure_manual_run_intent

    intent = ensure_manual_run_intent(
        db,
        role=role,
        application_id=application_id,
        dispatch_key=key,
    ).run
    db.commit()
    return intent


def _assert_succeeded(db, intent: AgentRun) -> None:
    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.finished_at is not None


def test_focused_manual_run_materializes_done_evaluation_without_rescore(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="focused-done",
        statuses=("done",),
    )
    application, evaluation = rows[0]
    intent = _manual_intent(
        db,
        role=role,
        application_id=int(application.id),
        key="related-focused-done",
    )
    materialized_result = {
        "status": "ok",
        "role_id": int(role.id),
        "created": 1,
    }

    from app.tasks.sister_role_tasks import score_sister_role

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle",
            return_value=materialized_result,
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "completed",
        "role_id": int(role.id),
        "application_id": int(application.id),
        "queued": 0,
        "retrying": 0,
        "materialized": materialized_result,
    }
    score_evaluation.assert_not_called()
    materialize.assert_called_once()
    assert materialize.call_args.kwargs["evaluation_id"] == int(evaluation.id)
    _assert_succeeded(db, intent)

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as replay_score,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as replay_materialize,
    ):
        replay = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
            organization_id=int(organization.id),
        )
    assert replay["status"] == "replayed"
    assert replay["run_status"] == "succeeded"
    replay_score.assert_not_called()
    replay_materialize.assert_not_called()


def test_role_wide_manual_run_materializes_all_done_without_rescore(db):
    organization, _owner, role, _rows = _related_family(
        db,
        suffix="role-wide-done",
        statuses=("done", "done"),
    )
    intent = _manual_intent(
        db,
        role=role,
        application_id=None,
        key="related-role-wide-done",
    )
    materialized_result = {
        "status": "ok",
        "role_id": int(role.id),
        "created": 2,
    }

    from app.tasks.sister_role_tasks import score_sister_role

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle",
            return_value=materialized_result,
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "completed",
        "role_id": int(role.id),
        "queued": 0,
        "retrying": 0,
        "materialized": materialized_result,
    }
    score_evaluation.assert_not_called()
    materialize.assert_called_once()
    assert materialize.call_args.kwargs["evaluation_id"] is None
    _assert_succeeded(db, intent)

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as replay_score,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as replay_materialize,
    ):
        replay = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )
    assert replay["status"] == "replayed"
    replay_score.assert_not_called()
    replay_materialize.assert_not_called()


def test_role_wide_manual_run_materializes_done_and_dispatches_pending_once(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="role-wide-mixed",
        statuses=("done", "pending"),
    )
    _done_application, done_evaluation = rows[0]
    _pending_application, pending_evaluation = rows[1]
    intent = _manual_intent(
        db,
        role=role,
        application_id=None,
        key="related-role-wide-mixed",
    )
    materialized_result = {
        "status": "ok",
        "role_id": int(role.id),
        "created": 1,
    }

    from app.tasks.sister_role_tasks import score_sister_role

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle",
            return_value=materialized_result,
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "queued",
        "role_id": int(role.id),
        "queued": 1,
        "retrying": 0,
        "materialized": materialized_result,
    }
    materialize.assert_called_once()
    assert materialize.call_args.kwargs["evaluation_id"] is None
    score_evaluation.assert_called_once_with(
        args=[int(pending_evaluation.id)],
        queue="scoring",
    )
    assert int(score_evaluation.call_args.kwargs["args"][0]) != int(done_evaluation.id)
    _assert_succeeded(db, intent)

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as replay_score,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as replay_materialize,
    ):
        replay = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )
    assert replay["status"] == "replayed"
    replay_score.assert_not_called()
    replay_materialize.assert_not_called()


def test_manual_provider_backoff_reports_deferred_and_remains_recoverable(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="provider-backoff",
        statuses=("retry_wait",),
    )
    application, evaluation = rows[0]
    future_retry = datetime.now(timezone.utc) + timedelta(hours=2)
    evaluation.last_error_code = "provider_scoring_failed"
    evaluation.next_attempt_at = future_retry
    intent = _manual_intent(
        db,
        role=role,
        application_id=int(application.id),
        key="related-provider-backoff",
    )

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_role,
    )

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "deferred",
        "role_id": int(role.id),
        "application_id": int(application.id),
        "queued": 0,
        "retrying": 1,
        "recovery": "recover_sister_role_evaluations",
    }
    score_evaluation.assert_not_called()
    materialize.assert_not_called()
    _assert_succeeded(db, intent)
    saved = db.get(SisterRoleEvaluation, int(evaluation.id))
    assert saved is not None
    assert saved.status == "retry_wait"
    assert saved.last_error_code == "provider_scoring_failed"
    assert saved.next_attempt_at is not None

    saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as recovered_score:
        recovered = recover_sister_role_evaluations.run(limit=10)
    assert recovered["recoverable"] == 1
    assert recovered["queued"] == 1
    recovered_score.assert_called_once_with(
        args=[int(evaluation.id)],
        queue="scoring",
    )

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as replay_score:
        replay = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            application_id=int(application.id),
            organization_id=int(organization.id),
        )
    assert replay["status"] == "replayed"
    replay_score.assert_not_called()


def test_empty_role_wide_manual_run_reports_no_work(db):
    organization, _owner, role, _rows = _related_family(
        db,
        suffix="empty",
        statuses=(),
    )
    intent = _manual_intent(
        db,
        role=role,
        application_id=None,
        key="related-empty-role-wide",
    )

    from app.tasks.sister_role_tasks import score_sister_role

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )

    assert result == {
        "status": "no_work",
        "role_id": int(role.id),
        "queued": 0,
        "retrying": 0,
    }
    score_evaluation.assert_not_called()
    materialize.assert_not_called()
    _assert_succeeded(db, intent)


def test_non_manual_score_kick_does_not_enter_manual_materialization(db):
    _organization, _owner, role, rows = _related_family(
        db,
        suffix="scheduled-unchanged",
        statuses=("done",),
    )
    application, _evaluation = rows[0]
    db.commit()

    from app.tasks.sister_role_tasks import score_sister_role

    with (
        patch(
            "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
        ) as score_evaluation,
        patch(
            "app.services.related_role_manual_run.run_related_role_cycle"
        ) as materialize,
    ):
        result = score_sister_role.run(
            int(role.id),
            application_id=int(application.id),
        )

    assert result == {
        "status": "queued",
        "role_id": int(role.id),
        "queued": 0,
        "retrying": 0,
    }
    score_evaluation.assert_not_called()
    materialize.assert_not_called()


def test_role_wide_cycle_reaches_work_after_250_waiting_rows(db):
    _organization, _owner, role, rows = _related_family(
        db,
        suffix="large-actionable-window",
        statuses=("done",) * 251,
    )
    waiting_run = AgentRun(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        trigger="cron",
        status="succeeded",
        model_version="test",
        prompt_version="test",
    )
    db.add(waiting_run)
    db.flush()
    db.add_all(
        AgentDecision(
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            application_id=int(application.id),
            agent_run_id=int(waiting_run.id),
            decision_type="advance_to_interview",
            recommendation="advance_to_interview",
            status="pending",
            reasoning="Already waiting for recruiter review.",
            evidence={},
            model_version="test",
            prompt_version="test",
            idempotency_key=f"large-window-pending:{application.id}",
        )
        for application, _evaluation in rows[:125]
    )
    db.add_all(
        Assessment(
            organization_id=int(role.organization_id),
            candidate_id=int(application.candidate_id),
            role_id=int(role.id),
            application_id=int(application.id),
            token=f"large-window-assessment-{application.id}",
            status=AssessmentStatus.PENDING,
            invite_email_status="delivered",
            is_voided=False,
        )
        for application, _evaluation in rows[125:250]
    )
    db.commit()

    from app.services.related_role_runtime import run_related_role_cycle

    result = run_related_role_cycle(db, role=role, limit=250)

    last_application, _last_evaluation = rows[250]
    assert result["created"] == 1
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == int(last_application.id),
        )
        .count()
        == 1
    )


@pytest.mark.parametrize(
    "blocked_head",
    ("closed", "outside_roster", "missing_score", "terminal_incomplete"),
)
def test_role_wide_cycle_skips_persistent_non_actionable_head_rows(
    db,
    blocked_head,
):
    _organization, _owner, role, rows = _related_family(
        db,
        suffix=f"non-actionable-{blocked_head}",
        statuses=("done",) * 251,
    )
    for application, evaluation in rows[:250]:
        if blocked_head == "closed":
            application.workable_disqualified = True
        elif blocked_head == "outside_roster":
            application.candidate.deleted_at = datetime.now(timezone.utc)
        elif blocked_head == "missing_score":
            evaluation.role_fit_score = None
        else:
            db.add(
                Assessment(
                    organization_id=int(role.organization_id),
                    candidate_id=int(application.candidate_id),
                    role_id=int(role.id),
                    application_id=int(application.id),
                    token=f"terminal-incomplete-{application.id}",
                    status=AssessmentStatus.COMPLETED,
                    scoring_failed=True,
                    is_voided=False,
                )
            )
    db.commit()

    from app.services.related_role_runtime import run_related_role_cycle

    result = run_related_role_cycle(db, role=role, limit=250)

    last_application, _last_evaluation = rows[250]
    assert result["created"] == 1
    assert result.get("has_more") is not True
    assert (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == int(role.id),
            AgentDecision.application_id == int(last_application.id),
        )
        .count()
        == 1
    )


def test_focused_cycle_preserves_missing_score_diagnostic(db):
    _organization, _owner, role, rows = _related_family(
        db,
        suffix="focused-missing-score-diagnostic",
        statuses=("done",),
    )
    _application, evaluation = rows[0]
    evaluation.role_fit_score = None
    db.commit()

    from app.services.related_role_runtime import run_related_role_cycle

    role_wide = run_related_role_cycle(db, role=role)
    focused = run_related_role_cycle(
        db,
        role=role,
        evaluation_id=int(evaluation.id),
    )

    assert role_wide.get("missing_score", 0) == 0
    assert focused["missing_score"] == 1


def test_role_wide_active_assessment_requires_matching_candidate(db):
    organization, _owner, role, rows = _related_family(
        db,
        suffix="assessment-candidate-scope",
        statuses=("done",),
    )
    application, _evaluation = rows[0]
    other_candidate = Candidate(
        organization_id=int(organization.id),
        full_name="Different assessment candidate",
        email="different-assessment-candidate@example.test",
    )
    db.add(other_candidate)
    db.flush()
    db.add(
        Assessment(
            organization_id=int(organization.id),
            candidate_id=int(other_candidate.id),
            role_id=int(role.id),
            application_id=int(application.id),
            token="mismatched-related-assessment",
            status=AssessmentStatus.PENDING,
            invite_email_status="delivered",
            is_voided=False,
        )
    )
    db.commit()

    from app.services.related_role_runtime import run_related_role_cycle

    result = run_related_role_cycle(db, role=role)

    assert result["created"] == 1
    decision = db.query(AgentDecision).one()
    assert int(decision.application_id) == int(application.id)
    assert decision.decision_type == "advance_to_interview"


def test_role_wide_manual_run_uses_durable_bounded_continuation_over_250(db):
    organization, _owner, role, _rows = _related_family(
        db,
        suffix="manual-bounded-continuation",
        statuses=("done",) * 251,
    )
    intent = _manual_intent(
        db,
        role=role,
        application_id=None,
        key="related-manual-bounded-continuation",
    )

    from app.tasks.agent_tasks import recover_dispatching_manual_agent_runs
    from app.tasks.sister_role_tasks import score_sister_role

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as score_evaluation:
        first = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )

    assert first["status"] == "in_progress"
    assert first["recovery"] == "recover_dispatching_manual_agent_runs"
    assert first["materialized"]["created"] == 250
    assert first["materialized"]["has_more"] is True
    score_evaluation.assert_not_called()
    db.expire_all()
    persisted = db.get(AgentRun, int(intent.id))
    assert persisted is not None
    assert persisted.status == "dispatching"
    assert persisted.finished_at is None

    with patch(
        "app.tasks.sister_role_tasks.score_sister_role.apply_async"
    ) as continuation:
        recovered = recover_dispatching_manual_agent_runs.run(limit=10)
    assert recovered == {"scanned": 1, "kicked": 1, "publish_failed": 0}
    continuation.assert_called_once_with(
        args=[int(role.id)],
        kwargs={
            "dispatch_key": str(intent.dispatch_key),
            "organization_id": int(organization.id),
        },
        queue="scoring",
    )

    with patch(
        "app.tasks.sister_role_tasks.score_sister_evaluation.apply_async"
    ) as continuation_score:
        second = score_sister_role.run(
            int(role.id),
            dispatch_key=str(intent.dispatch_key),
            organization_id=int(organization.id),
        )
    assert second["status"] == "completed"
    assert second["materialized"]["created"] == 1
    continuation_score.assert_not_called()
    _assert_succeeded(db, intent)
    assert (
        db.query(AgentDecision).filter(AgentDecision.role_id == role.id).count() == 251
    )

    replay = score_sister_role.run(
        int(role.id),
        dispatch_key=str(intent.dispatch_key),
        organization_id=int(organization.id),
    )
    assert replay["status"] == "replayed"


def test_role_wide_batch_query_count_is_constant_in_cohort_size(db):
    _small_org, _small_owner, small_role, _small_rows = _related_family(
        db,
        suffix="query-count-small",
        statuses=("done",),
    )
    _large_org, _large_owner, large_role, _large_rows = _related_family(
        db,
        suffix="query-count-large",
        statuses=("done",) * 40,
    )
    db.commit()
    small_role_id = int(small_role.id)
    large_role_id = int(large_role.id)

    from app.services.related_role_runtime_batch import (
        claim_related_role_runtime_batch,
    )

    def measured(role_id: int, expected_rows: int) -> int:
        db.expunge_all()
        role = db.get(Role, role_id)
        assert role is not None
        statements = 0

        def count_selects(_connection, _cursor, statement, *_args):
            nonlocal statements
            if statement.lstrip().upper().startswith("SELECT"):
                statements += 1

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", count_selects)
        try:
            batch = claim_related_role_runtime_batch(
                db,
                role=role,
                evaluation_id=None,
                limit=250,
                threshold=70.0,
                has_assessment_stage=False,
                criteria_fingerprint=None,
            )
        finally:
            event.remove(bind, "before_cursor_execute", count_selects)
        assert len(batch.evaluations) == expected_rows
        assert batch.has_more is False
        db.rollback()
        return statements

    small_queries = measured(small_role_id, 1)
    large_queries = measured(large_role_id, 40)

    assert large_queries <= small_queries + 1
    assert large_queries < 20


@pytest.mark.parametrize(
    ("revocation", "summary_key"),
    (
        ("application_deleted", "outside_roster"),
        ("candidate_deleted", "outside_roster"),
        ("owner_deleted", "outside_roster"),
        ("application_reassigned", "outside_roster"),
        ("application_disqualified", "closed"),
    ),
)
def test_related_runtime_does_not_materialize_revoked_owner_roster_rows(
    db,
    revocation,
    summary_key,
):
    _organization, owner, role, rows = _related_family(
        db,
        suffix=f"runtime-{revocation}",
        statuses=("done",),
    )
    application, evaluation = rows[0]
    stamp = datetime.now(timezone.utc)
    if revocation == "application_deleted":
        application.deleted_at = stamp
    elif revocation == "candidate_deleted":
        application.candidate.deleted_at = stamp
    elif revocation == "owner_deleted":
        owner.deleted_at = stamp
    elif revocation == "application_reassigned":
        other_owner = Role(
            organization_id=int(role.organization_id),
            name="Different owner role",
            source="manual",
        )
        db.add(other_owner)
        db.flush()
        application.role_id = int(other_owner.id)
    else:
        application.workable_disqualified = True
    db.commit()

    from app.services.related_role_runtime import run_related_role_cycle

    result = run_related_role_cycle(
        db,
        role=role,
        evaluation_id=int(evaluation.id),
    )

    assert result["status"] == "ok"
    assert result[summary_key] == 1
    assert db.query(AgentDecision).count() == 0
    assert db.query(AgentRun).count() == 0
