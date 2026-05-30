from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from ..platform.config import settings

celery_app = Celery(
    "taali",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)


@worker_process_init.connect
def _install_anthropic_wire_tap(**_kwargs):
    """Install the transport-level Anthropic wire-tap in every worker
    process. The bulk of Anthropic spend (scoring, agent, Graphiti) runs
    on the workers, so the wire-tap MUST be installed here too — not just
    in the API lifespan — or the ground-truth log misses worker traffic.

    ``worker_process_init`` fires once per forked worker process (prefork
    pool), which is where httpx clients are actually constructed and used.
    """
    try:
        from ..services.anthropic_wire_tap import install

        install()
    except Exception:  # pragma: no cover — never block worker boot
        import logging

        logging.getLogger("taali.anthropic_wire_tap").exception(
            "Failed to install Anthropic wire-tap in worker"
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
        # Anthropic billing reconciliation. Pulls the last 48h so
        # late-arriving Anthropic data on the previous day gets re-checked.
        #
        # Switched from ``schedule: 86400.0`` to a fixed-time crontab on
        # 2026-05-26. The 24h interval timer is RESET on every beat
        # restart — so a deploy/redeploy that happens before the 24h
        # tick fires silently skips that day's run. Symptom (2026-05-23
        # → 2026-05-26): three consecutive missed reconciliations until
        # someone noticed the table hadn't moved. A fixed-time crontab
        # fires at the same wall-clock moment regardless of when the
        # beat was last restarted.
        "anthropic-usage-reconciliation-daily": {
            "task": "app.tasks.reconciliation_tasks.reconcile_anthropic_usage",
            "schedule": crontab(hour=3, minute=0),
        },
        # Weekly settle sweep: re-reconcile the last 14 days so very-late
        # batch-retrieval rows (which can land days after Anthropic bills the
        # batch) converge the stored drift toward 0 instead of leaving a stale
        # negative number from the day's first 03:00 run. Idempotent upsert.
        "anthropic-usage-reconciliation-weekly-settle": {
            "task": "app.tasks.reconciliation_tasks.reconcile_anthropic_usage",
            "schedule": crontab(hour=4, minute=0, day_of_week=0),
            "kwargs": {"days": 14},
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
        # SLA sweep for stale pending decisions (BUG-2). Ages out pending
        # verdicts older than the SLA to status='expired', and re-surfaces
        # stale ``escalate_low_confidence`` rows (re-prioritised, never
        # silently expired) so a "human must decide" signal can't rot in the
        # queue forever. Hourly is ample for the day-scale SLA and keeps the
        # sweep cheap.
        "agent-expire-stale-decisions-hourly": {
            "task": "app.tasks.agent_tasks.agent_expire_stale_decisions",
            "schedule": 3600.0,
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
        # Drain the durable Graphiti episode outbox. Realised-outcome (and
        # decision) episodes are written to graph_episode_outbox in the
        # producer's transaction so a graph outage can't drop the
        # irreplaceable signal; this ships pending rows to Graphiti with
        # retry. Every 5 min keeps the graph fresh while a backlog from an
        # outage drains quickly once Graphiti recovers.
        "drain-graph-episode-outbox-every-5-minutes": {
            "task": "app.tasks.graph_outbox_tasks.drain_graph_episode_outbox",
            "schedule": 300.0,
        },
        # Outbound mainspring brain feed: sweep newly-resolved decisions /
        # teach outcomes / daily usage rollups (anonymized) into the
        # brain_feed_outbox and ship them to mainspring's ingest API. No-op
        # unless MAINSPRING_BRAIN_FEED_ENABLED is on (default off), so this is
        # inert on the live platform until deliberately enabled. Every 15 min
        # keeps the feed near-continuous without adding load.
        "flush-brain-feed-every-15-minutes": {
            "task": "app.tasks.brain_feed_tasks.flush_brain_feed",
            "schedule": 900.0,
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
