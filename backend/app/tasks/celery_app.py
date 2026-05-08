from celery import Celery
from ..platform.config import settings

celery_app = Celery(
    "taali",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Task → queue routing. Scoring lives on its own queue so a long-running
# integration task (e.g. Workable sync at 60+ min) can't starve scoring.
# Today we run a single worker that consumes both queues; when we
# outgrow that we add a second Railway service that consumes only
# `scoring`. See backend/docs/CELERY_QUEUES.md for the rollout.
_TASK_ROUTES = {
    "app.tasks.scoring_tasks.score_application_job": {"queue": "scoring"},
    "app.tasks.scoring_tasks.batch_score_role": {"queue": "scoring"},
}

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="celery",
    task_routes=_TASK_ROUTES,
    beat_schedule={
        "workable-sync-every-30-minutes": {
            "task": "app.tasks.assessment_tasks.sync_workable_orgs",
            "schedule": 1800.0,
        },
        "sync-starred-roles-every-15-minutes": {
            "task": "app.tasks.assessment_tasks.sync_starred_roles",
            "schedule": 900.0,
        },
        "assessment-expiry-reminders-daily": {
            "task": "app.tasks.assessment_tasks.send_assessment_expiry_reminders",
            "schedule": 86400.0,
        },
        # Anthropic billing reconciliation. Runs once a day; pulls the
        # last 48h so late-arriving Anthropic data on the previous day
        # gets re-checked. Crontab would be tighter than `schedule:
        # 86400.0` (which drifts), but the project doesn't use
        # ``celery.schedules.crontab`` elsewhere — keeping it consistent.
        "anthropic-usage-reconciliation-daily": {
            "task": "app.tasks.reconciliation_tasks.reconcile_anthropic_usage",
            "schedule": 86400.0,
        },
        # Phase 7 cohort planner tick: every 30 min, fan a tick to each
        # agent-enabled, non-paused role. The orchestrator surveys
        # cohort state and acts on what it finds. Replaces the old
        # per-application event trigger.
        "agent-cohort-tick-every-30-minutes": {
            "task": "app.tasks.agent_tasks.agent_cohort_tick_sweep",
            "schedule": 1800.0,
        },
        # Agent daily review (legacy): kept for the proactive once-a-day
        # sweep that surfaces idle candidates / stale scores / etc. The
        # cohort-tick task above handles the bulk of work; daily review
        # remains as a wider triage pass.
        "agent-daily-review-sweep": {
            "task": "app.tasks.agent_tasks.agent_daily_review_sweep",
            "schedule": 86400.0,
        },
        # Nightly DecisionPolicy retune. Aggregates explicit feedback +
        # silent overrides + manual recruiter actions over the window
        # since the last cause='feedback_retune' revision and writes a
        # new (inactive by default) policy. Auto-apply is per-org via
        # workspace_settings.decision_policy_auto_apply.
        "decision-policy-nightly-retune": {
            "task": "app.tasks.decision_policy_tasks.nightly_retune_sweep",
            "schedule": 86400.0,
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
