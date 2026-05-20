from celery import Celery
from celery.schedules import crontab

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
        # Sync redesign (2026-05-20): the old ``sync_workable_orgs`` did a
        # full-fat sync every 30 min and was rate-limiting Workable while
        # re-downloading CVs we already had. Now split across four tasks:
        #   * jobs metadata refresh — every 15 min (jobs_only mode)
        #   * starred-role candidates — every 5 min (full)
        #   * agent-mode-role candidates — every 5 min (full)
        #   * everything else — once nightly (full)
        "workable-jobs-every-15-minutes": {
            "task": "app.tasks.assessment_tasks.sync_workable_jobs",
            "schedule": 900.0,
        },
        # Starred roles are the ones the recruiter is actively piloting Tali
        # on — candidates kept in lockstep so stage changes (e.g. candidate
        # moved to Technical Interview) reflect in Tali within minutes.
        "sync-starred-roles-every-5-minutes": {
            "task": "app.tasks.assessment_tasks.sync_starred_roles",
            "schedule": 300.0,
        },
        # Agent-mode roles need fresh candidate state every cycle so the
        # agent loop's decisions reflect the latest Workable signals.
        "sync-agent-mode-roles-every-5-minutes": {
            "task": "app.tasks.assessment_tasks.sync_agent_mode_roles",
            "schedule": 300.0,
        },
        # Nightly catch-all for non-starred, non-agent roles. 03:15 UTC
        # is off-peak for our user base and avoids colliding with the
        # daily Anthropic reconciliation at midnight.
        "sync-workable-daily-candidates": {
            "task": "app.tasks.assessment_tasks.sync_workable_daily_candidates",
            "schedule": crontab(hour=3, minute=15),
        },
        # Recover Workable sync runs whose worker died mid-flight. Without
        # this, status='running' rows linger forever and ``POST /workable/sync``
        # returns ``already_running`` on every subsequent attempt.
        "reap-stuck-workable-sync-runs-every-30-minutes": {
            "task": "app.tasks.assessment_tasks.reap_stuck_workable_sync_runs",
            "schedule": 1800.0,
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
        # Phase 7 cohort planner tick: every 5 min, fan a tick to each
        # agent-enabled, non-paused role. The orchestrator surveys
        # cohort state and acts on what it finds. Replaces the old
        # per-application event trigger.
        "agent-cohort-tick-every-5-minutes": {
            "task": "app.tasks.agent_tasks.agent_cohort_tick_sweep",
            "schedule": 300.0,
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
        # Watchdog for AgentRun rows stuck in status='running' (worker
        # crash, deploy restart, OOM mid-cycle). Without this the row
        # stays "running" forever, hiding real failures in /agent/status.
        "agent-expire-stuck-runs": {
            "task": "app.tasks.agent_tasks.agent_expire_stuck_runs",
            "schedule": 300.0,  # every 5 min — fast enough to surface within one cohort tick
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
