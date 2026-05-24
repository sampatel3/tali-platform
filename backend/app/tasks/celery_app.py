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
    # Nightly calibration scoring is Anthropic-heavy — keep it off the default
    # queue so it can't starve agent ticks / sync.
    "app.tasks.calibration_tasks.score_terminal_for_calibration": {"queue": "scoring"},
    # Recalibration writes snapshots read by apply_calibrator during scoring —
    # keep it on the scoring worker so they're co-located.
    "app.tasks.calibration_tasks.recalibrate_cv_match": {"queue": "scoring"},
    # Pre-screen reject shadow-scoring is Anthropic-heavy — keep it off the
    # default queue too.
    "app.tasks.calibration_tasks.sample_prescreen_for_calibration": {"queue": "scoring"},
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
        # Recover approve batches whose worker was SIGKILLed mid-run (deploy):
        # decisions stranded in 'processing' go back to the Hub queue and the
        # stuck decision_batch BackgroundJobRun is marked failed. Mirrors
        # agent_expire_stuck_runs; 5 min surfaces within ~one beat tick.
        "expire-stuck-decision-batches-every-5-minutes": {
            "task": "app.tasks.workable_tasks.expire_stuck_decision_batches",
            "schedule": 300.0,
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
        # Watchdog for AgentRun rows stuck in status='running' (worker
        # crash, deploy restart, OOM mid-cycle). Without this the row
        # stays "running" forever, hiding real failures in /agent/status.
        "agent-expire-stuck-runs": {
            "task": "app.tasks.agent_tasks.agent_expire_stuck_runs",
            "schedule": 300.0,  # every 5 min — fast enough to surface within one cohort tick
        },
        # Safety net: find applications whose scores have been invalidated
        # (NULL pre_screen_score with cv_text present + stale CvScoreJob
        # row) and re-enqueue them. Hooks at the role-criteria, CV
        # upload, and Workable-sync sites handle the common cases in
        # real time; this catches anything that slips through (worker
        # crash mid-batch, missed hook on a new mutation path, etc.).
        "sweep-stale-scores-every-30-minutes": {
            "task": "app.tasks.scoring_tasks.sweep_stale_scores",
            "schedule": 1800.0,
        },
        # Model-refinement data prep. Runs nightly AFTER the 03:15 daily
        # candidate sync so the day's freshly-decided candidates (Workable
        # offer/hired/reject) are present. Scores any `advanced` candidate
        # lacking a Tali score so the cv_match calibrator has (score ->
        # outcome) pairs. Bounded per run; backlog drains over a few nights.
        "score-terminal-for-calibration-nightly": {
            "task": "app.tasks.calibration_tasks.score_terminal_for_calibration",
            "schedule": crontab(hour=3, minute=45),
        },
        # Refit the cv_match calibrators from the day's (score -> outcome)
        # pairs. Runs AFTER the terminal-scoring task so the fresh scores are
        # included. Same scoring queue so snapshots land where apply_calibrator
        # reads them at scoring time.
        "recalibrate-cv-match-nightly": {
            "task": "app.tasks.calibration_tasks.recalibrate_cv_match",
            "schedule": crontab(hour=4, minute=30),
        },
        # Weekly reject-inference sampling: shadow-score a random sample of
        # pre-screen rejects (backend-only) so the pre-screen calibrator has
        # unbiased labels below the gate. Weekly (not nightly) to bound the
        # extra Anthropic spend; bounded per run.
        "sample-prescreen-for-calibration-weekly": {
            "task": "app.tasks.calibration_tasks.sample_prescreen_for_calibration",
            "schedule": crontab(hour=2, minute=30, day_of_week=0),
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
