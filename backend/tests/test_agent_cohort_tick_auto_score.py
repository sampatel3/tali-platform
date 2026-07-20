"""Auto-scoring is wired into the cohort tick so the agent picks up
new candidates without a recruiter clicking ``Process N candidates``.

We test the helper directly rather than the full Celery task — the task
calls ``run_cycle`` which needs Anthropic, an agent_run table, and lots
of plumbing that's orthogonal to the auto-enqueue we're verifying.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.models.usage_event import UsageEvent
from app.services.usage_metering_service import InsufficientCreditsError
from app.tasks.agent_tasks import (
    ACTIVATION_AUTO_SCORE_CAP,
    _auto_enqueue_scoring,
    _mark_agent_tick_ready,
    _retry_or_fail_cohort_bootstrap,
    agent_cohort_tick_role,
    agent_cohort_tick_sweep,
    agent_manual_run,
)


def _seed_role(db, *, agentic_mode_enabled: bool = True) -> tuple[Organization, Role]:
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        agentic_mode_enabled=agentic_mode_enabled,
        monthly_usd_budget_cents=5000,
        job_spec_text="hire stuff",
    )
    db.add(role)
    db.flush()
    return org, role


def _seed_app(
    db,
    *,
    org: Organization,
    role: Role,
    cv_text: str | None = "candidate cv text",
    cv_match_score: float | None = None,
    application_outcome: str = "open",
) -> CandidateApplication:
    import uuid
    cand = Candidate(
        organization_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@x.test",
        full_name="C",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome=application_outcome,
        source="manual",
        cv_text=cv_text,
        cv_match_score=cv_match_score,
    )
    db.add(app)
    db.flush()
    return app


def test_auto_enqueue_calls_enqueue_score_for_each_unscored_app(db):
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)

    seen_app_ids: list[int] = []

    def fake_enqueue(db_arg, app, *, force=False, **kwargs):
        seen_app_ids.append(int(app.id))
        assert kwargs["requires_active_agent"] is True
        return object()  # truthy stand-in for a Job

    with patch("app.services.cv_score_orchestrator.enqueue_score", side_effect=fake_enqueue):
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 3
    assert len(seen_app_ids) == 3


def test_auto_enqueue_skips_already_scored_apps(db):
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role, cv_match_score=72.0)  # scored — skip
    _seed_app(db, org=org, role=role)                       # unscored — enqueue
    _seed_app(db, org=org, role=role, cv_match_score=33.0)  # scored — skip

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_replays_paused_deferred_rescore_after_resume(db):
    org, role = _seed_role(db)
    app = _seed_app(db, org=org, role=role, cv_match_score=72.0)
    db.add(
        CvScoreJob(
            application_id=app.id,
            role_id=role.id,
            status="stale",
            error_message="deferred_agent_paused",
            requires_active_agent=True,
            force_full_score=True,
        )
    )
    db.commit()

    with patch(
        "app.services.cv_score_orchestrator.enqueue_score",
        return_value=object(),
    ) as enqueue:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    enqueue.assert_called_once_with(
        db,
        app,
        force=True,
        bypass_pre_screen=True,
        requires_active_agent=True,
    )


def test_active_agent_replays_role_intent_change_with_visible_old_score(db):
    org, role = _seed_role(db)
    app = _seed_app(db, org=org, role=role, cv_match_score=72.0)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="stale",
            error_message="role_intent_changed",
            requires_active_agent=True,
        )
    )
    db.commit()

    with patch(
        "app.services.cv_score_orchestrator.enqueue_score", return_value=object()
    ) as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=1)

    assert count == 1
    enqueue.assert_called_once_with(
        db,
        app,
        force=True,
        bypass_pre_screen=False,
        requires_active_agent=True,
    )


@pytest.mark.parametrize("held", ("paused", "off"))
def test_role_intent_change_waits_while_agent_is_not_runnable(db, held):
    org, role = _seed_role(db)
    app = _seed_app(db, org=org, role=role, cv_match_score=72.0)
    if held == "paused":
        role.agent_paused_at = datetime.now(timezone.utc)
    else:
        role.agentic_mode_enabled = False
    stale = CvScoreJob(
        application_id=int(app.id),
        role_id=int(role.id),
        status="stale",
        error_message="role_intent_changed",
        requires_active_agent=True,
    )
    db.add(stale)
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score") as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=1)

    assert count == 0
    enqueue.assert_not_called()
    db.refresh(stale)
    assert stale.status == "stale"


def test_other_stale_reason_never_enters_automatic_intent_recovery(db):
    org, role = _seed_role(db)
    app = _seed_app(db, org=org, role=role, cv_match_score=72.0)
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="stale",
            error_message="candidate_data_changed",
            requires_active_agent=True,
        )
    )
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score") as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=1)

    assert count == 0
    enqueue.assert_not_called()


def test_recovery_uses_largest_job_id_when_latest_attempt_is_backdated(db):
    org, role = _seed_role(db)
    app = _seed_app(db, org=org, role=role, cv_match_score=72.0)
    # The later INSERT is the causal successor even if mixed timestamp sources
    # give it an older queued_at value.
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="done",
            queued_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.flush()
    db.add(
        CvScoreJob(
            application_id=int(app.id),
            role_id=int(role.id),
            status="stale",
            error_message="role_intent_changed",
            queued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score") as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=1)

    assert count == 1
    enqueue.assert_called_once()


def test_auto_enqueue_skips_apps_without_cv_text(db):
    """Apps without ``cv_text`` need the CV fetcher first; the scoring
    enqueue helper would just no-op on them. Filter them out upstream
    so we don't churn through them every tick."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role, cv_text=None)  # no CV text
    _seed_app(db, org=org, role=role, cv_text="")    # empty CV text
    _seed_app(db, org=org, role=role)                # has CV text — enqueue

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_skips_rejected_or_hired_apps(db):
    """Only ``outcome='open'`` apps get auto-scored. Closed-out apps
    (rejected, hired, withdrawn) are settled — don't burn budget
    rescoring them."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)                                # open — enqueue
    _seed_app(db, org=org, role=role, application_outcome="rejected")
    _seed_app(db, org=org, role=role, application_outcome="hired")

    with patch("app.services.cv_score_orchestrator.enqueue_score", return_value=object()) as m:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 1
    assert m.call_count == 1


def test_auto_enqueue_returns_zero_when_no_apps(db):
    org, role = _seed_role(db)
    count = _auto_enqueue_scoring(db, role=role)
    assert count == 0


def test_auto_enqueue_swallows_individual_failures(db):
    """One app's enqueue raising shouldn't kill the whole batch."""
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)
    _seed_app(db, org=org, role=role)

    calls = {"n": 0}

    def flaky(db_arg, app, *, force=False, **kwargs):
        assert kwargs["requires_active_agent"] is True
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated metering glitch")
        return object()

    with patch("app.services.cv_score_orchestrator.enqueue_score", side_effect=flaky):
        count = _auto_enqueue_scoring(db, role=role)

    # 2 successful enqueues, 1 swallowed exception — count reflects successes only.
    assert count == 2
    assert calls["n"] == 3


def test_auto_enqueue_pauses_when_live_usage_credits_are_exhausted(db):
    org, role = _seed_role(db)
    _seed_app(db, org=org, role=role)
    role.agent_bootstrap_status = "starting"
    db.flush()

    depleted = InsufficientCreditsError(
        organization_id=int(org.id), required=30_000, available=0
    )
    with patch(
        "app.services.usage_metering_service.reserve", side_effect=depleted
    ), patch("app.services.cv_score_orchestrator.enqueue_score") as enqueue:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 0
    assert not enqueue.called
    assert role.agent_paused_at is not None
    assert "top up to resume" in role.agent_paused_reason
    assert role.agent_bootstrap_status == "failed"


def test_live_activation_scoring_burst_is_capped_by_credit_capacity(db):
    org, role = _seed_role(db)
    org.credits_balance = 60_000  # exactly two conservative SCORE reservations
    for _ in range(5):
        _seed_app(db, org=org, role=role)
    db.flush()

    with patch(
        "app.platform.config.settings.USAGE_METER_LIVE", True
    ), patch(
        "app.services.cv_score_orchestrator.enqueue_score", return_value=object()
    ) as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=500)

    assert count == 2
    assert enqueue.call_count == 2


def test_500_job_activation_is_capped_by_remaining_role_budget(db):
    """Projected jobs consume the remaining cap before workers record spend."""
    org, role = _seed_role(db)
    org.credits_balance = 100_000_000
    role.monthly_usd_budget_cents = 300  # $3.00 = 100 SCORE reservations total
    db.add(
        UsageEvent(
            organization_id=org.id,
            role_id=role.id,
            feature="score",
            model="claude-haiku-4-5-20251001",
            input_tokens=0,
            output_tokens=0,
            cost_usd_micro=200_000,
            markup_multiplier=3,
            credits_charged=600_000,  # $0.60 spent; $2.40 / $0.03 = 80 jobs
            cache_hit=0,
            created_at=datetime.now(timezone.utc),
        )
    )
    for _ in range(500):
        _seed_app(db, org=org, role=role)
    db.flush()

    with patch(
        "app.platform.config.settings.USAGE_METER_LIVE", True
    ), patch(
        "app.services.cv_score_orchestrator.enqueue_score", return_value=object()
    ) as enqueue:
        count = _auto_enqueue_scoring(db, role=role, limit=500)

    assert count == 80
    assert enqueue.call_count == 80


def test_activation_runs_large_phase_one_even_with_old_agent_run_in_flight(db):
    org, role = _seed_role(db)
    role.agent_bootstrap_status = "starting"
    in_flight = AgentRun(
        id=9_000_000 + int(role.id),
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger="cron",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(in_flight)
    db.commit()

    with patch(
        "app.tasks.agent_tasks._auto_enqueue_scoring", return_value=17
    ) as score_phase:
        result = agent_cohort_tick_role.run(int(role.id), activation=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "already_running"
    assert result["auto_scored_enqueued"] == 17
    assert result["in_flight_run_id"] == int(in_flight.id)
    assert score_phase.call_args.kwargs["limit"] == ACTIVATION_AUTO_SCORE_CAP
    assert score_phase.call_args.kwargs["strict"] is True
    db.expire_all()
    refreshed = db.query(Role).filter(Role.id == role.id).one()
    assert refreshed.agent_bootstrap_status == "ready"


@pytest.mark.parametrize("terminal_kind", ("local", "workable", "bullhorn"))
def test_cohort_tick_does_not_start_for_terminal_job_lifecycle(
    db, terminal_kind: str
):
    from app.models.role import JOB_STATUS_CANCELLED, JOB_STATUS_OPEN

    _org, role = _seed_role(db)
    role.job_status = JOB_STATUS_OPEN
    if terminal_kind == "local":
        role.job_status = JOB_STATUS_CANCELLED
    elif terminal_kind == "workable":
        role.source = "workable"
        role.workable_job_id = f"WORK-{role.id}"
        role.workable_job_data = {"state": "closed"}
    else:
        role.source = "bullhorn"
        role.bullhorn_job_order_id = str(92_000 + int(role.id))
        role.bullhorn_job_data = {"status": "Closed", "isOpen": False}
    db.commit()

    with patch("app.tasks.agent_tasks._auto_enqueue_scoring") as enqueue, patch(
        "app.agent_runtime.orchestrator.run_cycle"
    ) as run_cycle:
        result = agent_cohort_tick_role.run(int(role.id))

    assert result["status"] == "skipped"
    assert result["reason"] == "not_eligible"
    assert "not open" in result["detail"] or "not live" in result["detail"]
    enqueue.assert_not_called()
    run_cycle.assert_not_called()


def test_auto_enqueue_boundary_rejects_terminal_role(db):
    from app.models.role import JOB_STATUS_CANCELLED

    org, role = _seed_role(db)
    role.job_status = JOB_STATUS_CANCELLED
    _seed_app(db, org=org, role=role)
    db.commit()

    with patch("app.services.cv_score_orchestrator.enqueue_score") as enqueue:
        count = _auto_enqueue_scoring(db, role=role)

    assert count == 0
    enqueue.assert_not_called()


def test_successful_tick_persists_bootstrap_acknowledgement(db):
    _org, role = _seed_role(db)
    role.agent_bootstrap_status = "starting"
    db.flush()

    _mark_agent_tick_ready(db, role=role)

    assert role.agent_bootstrap_status == "ready"
    assert role.agent_bootstrap_error is None
    assert role.agent_bootstrap_completed_at is not None
    assert role.agent_last_run_at is not None


def test_exhausted_resume_bootstrap_is_failed_and_paused(db):
    _org, role = _seed_role(db)
    role.agent_bootstrap_status = "starting"
    db.commit()

    class _Request:
        retries = 3

    class _Task:
        request = _Request()
        max_retries = 3

    failure = RuntimeError("scoring worker unavailable")
    dispatched_version = int(role.version or 1)
    with pytest.raises(RuntimeError, match="scoring worker unavailable"):
        _retry_or_fail_cohort_bootstrap(
            _Task(),
            db=db,
            role=role,
            exc=failure,
            activation=True,
            dispatch_role_version=dispatched_version,
        )

    db.refresh(role)
    assert role.agent_bootstrap_status == "failed"
    assert role.agent_paused_at is not None
    assert "after retries" in role.agent_paused_reason
    assert role.version == dispatched_version + 1
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == "agent_paused"
    assert event.actor_user_id is None
    assert event.from_version == dispatched_version
    assert event.to_version == dispatched_version + 1


def test_exhausted_bootstrap_does_not_pause_newer_role_revision(db):
    _org, role = _seed_role(db)
    role.agent_bootstrap_status = "starting"
    role.version = 8
    db.commit()

    class _Request:
        retries = 3

    class _Task:
        request = _Request()
        max_retries = 3

    failure = RuntimeError("old dispatch failed")
    with pytest.raises(RuntimeError, match="old dispatch failed"):
        _retry_or_fail_cohort_bootstrap(
            _Task(),
            db=db,
            role=role,
            exc=failure,
            activation=True,
            dispatch_role_version=7,
        )

    db.expire_all()
    current = db.query(Role).filter(Role.id == role.id).one()
    assert current.version == 8
    assert current.agent_bootstrap_status == "starting"
    assert current.agent_paused_at is None
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .count()
        == 0
    )


def test_bootstrap_retry_preserves_original_dispatch_revision(db):
    _org, role = _seed_role(db)
    db.commit()

    class _RetryRaised(Exception):
        pass

    class _Request:
        retries = 1
        args = (int(role.id),)
        kwargs = {"activation": True}

    class _Task:
        request = _Request()
        max_retries = 3
        captured = None

        def retry(self, **kwargs):
            self.captured = kwargs
            raise _RetryRaised()

    task = _Task()
    with pytest.raises(_RetryRaised):
        _retry_or_fail_cohort_bootstrap(
            task,
            db=db,
            role=role,
            exc=RuntimeError("transient"),
            activation=True,
            dispatch_role_version=4,
        )

    assert task.captured["kwargs"] == {
        "activation": True,
        "dispatch_role_version": 4,
    }


def test_agent_cycle_workers_skip_soft_deleted_roles(db):
    _org, role = _seed_role(db)
    role.deleted_at = datetime.now(timezone.utc)
    db.commit()

    with patch("app.agent_runtime.orchestrator.run_cycle") as run_cycle:
        cohort = agent_cohort_tick_role.run(int(role.id))
        manual = agent_manual_run.run(int(role.id))

    assert cohort == {
        "status": "skipped",
        "reason": "role_not_found",
        "role_id": role.id,
    }
    assert manual == cohort
    run_cycle.assert_not_called()


def test_cohort_sweep_dispatches_the_captured_role_revision(db):
    _org, role = _seed_role(db)
    role.version = 6
    db.commit()

    with patch.object(agent_cohort_tick_role, "delay") as dispatch:
        result = agent_cohort_tick_sweep.run()

    assert result["role_ids"] == [role.id]
    dispatch.assert_called_once_with(role.id, dispatch_role_version=6)


def test_deferred_activation_worker_skips_soft_deleted_role(db):
    _org, role = _seed_role(db, agentic_mode_enabled=False)
    role.deleted_at = datetime.now(timezone.utc)
    db.commit()

    result = agent_cohort_tick_role.run(
        int(role.id),
        activation=True,
        activation_intent_id="deleted-activation",
    )

    assert result == {
        "status": "skipped",
        "reason": "activation_missing",
        "role_id": role.id,
    }
