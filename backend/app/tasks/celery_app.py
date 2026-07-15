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
    "app.tasks.scoring_tasks.recover_stuck_score_jobs": {"queue": "scoring"},
    # Nightly calibration scoring is Anthropic-heavy — keep it off the default
    # queue so it can't starve agent ticks / sync.
    "app.tasks.calibration_tasks.score_terminal_for_calibration": {"queue": "scoring"},
    # Recalibration writes snapshots read by apply_calibrator during scoring —
    # keep it on the scoring worker so they're co-located.
    "app.tasks.calibration_tasks.recalibrate_cv_match": {"queue": "scoring"},
    # Pre-screen reject shadow-scoring is Anthropic-heavy — keep it off the
    # default queue too.
    "app.tasks.calibration_tasks.sample_prescreen_for_calibration": {"queue": "scoring"},
    # Timed-out assessment finalize runs the full submit/scoring pipeline per row
    # (Anthropic + E2B) — keep it off the default queue so it can't starve agent
    # ticks / Workable sync.
    "app.tasks.assessment_tasks.finalize_timed_out_assessments": {"queue": "scoring"},
    # JD authoring/repair is multi-call Sonnet work; battle testing holds an E2B
    # sandbox. Keep all provisioning work off the default agent/sync queue.
    "app.tasks.assessment_tasks.generate_assessment_task_for_role": {"queue": "scoring"},
    "app.tasks.assessment_tasks.repair_generated_task_after_battle_failure": {"queue": "scoring"},
    "app.tasks.assessment_tasks.battle_test_generated_task": {"queue": "scoring"},
    "app.tasks.rubric_retry_tasks.retry_incomplete_rubric_scoring": {"queue": "scoring"},
    "app.tasks.rubric_retry_tasks.sweep_incomplete_rubric_scoring": {"queue": "scoring"},
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
        # End-to-end scheduler canary. A fresh key proves Beat dispatched and a
        # worker consumed a task; API production activation fails closed when
        # it goes stale instead of pretending an idle agent is running.
        "default-queue-worker-heartbeat-every-minute": {
            "task": "app.tasks.health_tasks.queue_worker_heartbeat",
            "schedule": 60.0,
            "args": ["celery"],
            "options": {"queue": "celery", "priority": 9},
        },
        "scoring-queue-worker-heartbeat-every-minute": {
            "task": "app.tasks.health_tasks.queue_worker_heartbeat",
            "schedule": 60.0,
            "args": ["scoring"],
            "options": {"queue": "scoring", "priority": 9},
        },
        # A worker can die after committing a provider-call credit hold but
        # before the metering wrapper settles/releases it. Reap only holds
        # older than two hours (far beyond any bounded provider call); the
        # settlement ref makes this idempotent and late results are still
        # charged by the canonical reservation reconciler.
        "release-stale-usage-credit-reservations-every-15-minutes": {
            "task": "app.tasks.health_tasks.release_stale_usage_credit_reservations",
            "schedule": 900.0,
            "kwargs": {"stale_after_minutes": 120, "limit": 500},
        },
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
        # Workable/Bullhorn application creation is a transactional outbox:
        # the normal kick fires only after the sync commit. Recover a broker
        # outage, lost kick, or stale dispatcher lease without waiting for a
        # later ATS re-sync or requiring a recruiter action.
        "sweep-application-created-outbox-every-minute": {
            "task": "app.tasks.application_ingest_tasks.sweep_application_created_outbox",
            "schedule": 60.0,
        },
        # Broker acceptance is not a CV-parse result. Reconcile worker acks,
        # re-kick lost queued tasks, and retry transient parse/provider failures
        # from the durable application-created row.
        "sweep-application-cv-parse-outbox-every-minute": {
            "task": "app.tasks.application_ingest_tasks.sweep_application_cv_parse_outbox",
            "schedule": 60.0,
        },
        # Related-role evaluations are their own durable scoring outbox. This
        # recovers broker loss, worker death, transient provider failures, and
        # rows held while the source role's agent is paused or turned off.
        "recover-related-role-evaluations-every-minute": {
            "task": "app.tasks.sister_role_tasks.recover_sister_role_evaluations",
            "schedule": 60.0,
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
        # Overrides may include non-idempotent recruiter side effects, so their
        # payloads are never replayed by the generic ATS-op recovery. A stale
        # queued/running delivery instead fails visibly and returns only a still-
        # processing decision to the Hub for explicit HITL retry.
        "expire-stuck-override-ops-every-5-minutes": {
            "task": "app.tasks.workable_tasks.expire_stuck_override_ops",
            "schedule": 300.0,
        },
        "recover-dispatching-ats-ops-every-minute": {
            "task": "app.tasks.workable_tasks.recover_dispatching_workable_ops",
            "schedule": 60.0,
        },
        # A broker outage or worker SIGKILL must not leave a CvScoreJob in
        # pending/running forever (which blocks enqueue_score's duplicate
        # guard). Archive the stale attempt and dispatch a fresh idempotent one.
        "recover-stuck-score-jobs-every-5-minutes": {
            "task": "app.tasks.scoring_tasks.recover_stuck_score_jobs",
            "schedule": 300.0,
        },
        # Message Batches API pipelines (cv_parse today). Submit sweeps
        # parse-pending applications into per-org batches every 15 min
        # (no-op unless CV_PARSE_BATCH_ENABLED); poll drains ended batches
        # every 5 min (never gated, so in-flight batches finish even after
        # the flag is turned off). Most batches end within minutes, so a
        # fresh parse lands ~15-20 min after ingest at 50% of live pricing.
        "submit-cv-parse-batches-every-15-minutes": {
            "task": "app.tasks.anthropic_batch_tasks.submit_cv_parse_batches",
            "schedule": 900.0,
        },
        "poll-cv-parse-batches-every-5-minutes": {
            "task": "app.tasks.anthropic_batch_tasks.poll_cv_parse_batches",
            "schedule": 300.0,
        },
        "assessment-expiry-reminders-daily": {
            "task": "app.tasks.assessment_tasks.send_assessment_expiry_reminders",
            "schedule": 86400.0,
        },
        # Mid-window nudges (delivered-not-opened / opened-not-started at 48h,
        # one per assessment). No-op unless ASSESSMENT_NUDGES_ENABLED is set.
        "assessment-nudges-daily": {
            "task": "app.tasks.assessment_tasks.send_assessment_nudges",
            "schedule": 86400.0,
        },
        # Assessment rows are the durable invite outbox. The normal producer
        # kick runs only after the outer DB commit; this sweep recovers a lost
        # kick, broker outage, or worker crash without recruiter intervention.
        "sweep-pending-assessment-invites-every-minute": {
            "task": "app.components.notifications.tasks.sweep_pending_assessment_invites",
            "schedule": 60.0,
        },
        # Provider-send recovery is deliberately separate from outbox dispatch:
        # it leases cooled-down/stale sends only when the default worker's live
        # Resend canary is healthy, then retries with the original idempotency
        # key so no recruiter intervention or duplicate candidate email occurs.
        "sweep-retryable-assessment-invites-every-minute": {
            "task": "app.components.notifications.tasks.sweep_retryable_assessment_invites",
            "schedule": 60.0,
        },
        # Workable stage/note is a distinct outbox that begins only after
        # Resend returns a provider id. ATS outages therefore recover without
        # re-submitting the candidate email.
        "sweep-assessment-invite-workable-handoffs-every-minute": {
            "task": "app.components.notifications.tasks.sweep_assessment_invite_workable_handoffs",
            "schedule": 60.0,
        },
        # Role JSON is the durable JD->assessment provisioning outbox. Recover
        # broker outages, stale worker claims, and cooled-down failed retry
        # chains without a recruiter re-publishing or manually retrying.
        "sweep-assessment-task-provisioning-every-minute": {
            "task": "app.tasks.assessment_tasks.sweep_assessment_task_provisioning",
            "schedule": 60.0,
        },
        # Completed candidate work with a partial/failed rubric remains
        # non-authoritative. This durable sweep recovers provider/credit errors,
        # lost broker kicks, and stale worker leases without a recruiter click.
        "sweep-incomplete-rubric-grading-every-minute": {
            "task": "app.tasks.rubric_retry_tasks.sweep_incomplete_rubric_scoring",
            "schedule": 60.0,
            "options": {"queue": "scoring"},
        },
        # Per-(task, role_family) predictive-quality calibration. The engine
        # (sub_agents.task_calibration.recompute_all) predates this entry but
        # was never scheduled. Weekly is plenty at current volume.
        "recompute-task-calibrations-weekly": {
            "task": "app.tasks.assessment_tasks.recompute_task_calibrations",
            "schedule": 604800.0,
        },
        # Reap abandoned assessments: mark PENDING-past-expiry as EXPIRED and
        # close IN_PROGRESS sessions left open > 2h. The task existed and was
        # registered but was never on the beat schedule, so abandoned rows piled
        # up indefinitely (e.g. role-26 assessments stuck IN_PROGRESS for 9 days)
        # — polluting funnel counts and leaving sandboxes referenced. 30 min is
        # well within the 2h staleness cutoff for our ≤60-min assessments.
        "cleanup-expired-assessments-every-30-minutes": {
            "task": "app.tasks.assessment_tasks.cleanup_expired_assessments",
            "schedule": 1800.0,
        },
        # Server-side timer enforcement: finalize + SCORE assessments whose working
        # timer expired but the candidate never submitted (closed the tab). The
        # in-app enforce_active_or_timeout gate is pull-based and never fires for a
        # walk-away, so without this their work is lost. 15 min keeps the lag from
        # timer-expiry-to-result short. Anthropic/E2B-heavy → scoring queue.
        "finalize-timed-out-assessments-every-15-minutes": {
            "task": "app.tasks.assessment_tasks.finalize_timed_out_assessments",
            "schedule": 900.0,
        },
        # Watchdog for the GitHub credential that assessment repo provisioning
        # depends on. An expired token returns 401 and silently blocks every
        # candidate from starting an assessment (repo init fails at send + start)
        # — the 2026-06-25 zero-traction incident. Alerts on failure (log + Sentry)
        # so it surfaces in minutes, not days. Light call → default queue.
        "assessment-provisioning-healthcheck-every-30-minutes": {
            "task": "app.tasks.assessment_tasks.assessment_provisioning_healthcheck",
            "schedule": 1800.0,
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
        # Phase 7 cohort planner tick: every 60 min, fan a tick to each
        # agent-enabled, non-paused role. The orchestrator surveys
        # cohort state and acts on what it finds. Replaces the old
        # per-application event trigger. Cadence is 60 min (was 30) — the
        # standing-backlog re-examination is the only thing this proactive
        # sweep does, and new candidates are picked up on the next tick, so
        # a longer cadence only cuts redundant re-scans of an unchanged
        # cohort (LLM-cost win).
        "agent-cohort-tick-every-60-minutes": {
            "task": "app.tasks.agent_tasks.agent_cohort_tick_sweep",
            "schedule": 3600.0,
        },
        # System holds self-heal: month rollover, credit top-up, and restored
        # runtime/provider health are rechecked against the same fail-closed
        # readiness contract used by explicit Resume. Manual pauses are never
        # cleared. A successful recovery immediately dispatches a role tick.
        "agent-system-hold-recovery-every-5-minutes": {
            "task": "app.tasks.agent_tasks.agent_recovery_sweep",
            "schedule": 300.0,
        },
        # Reconcile durable terminal AgentRun rows into role-chat event cards.
        # Direct publication happens in the source transaction; this is the
        # retry net for a transient notification failure or worker interruption.
        "agent-terminal-run-events-every-5-minutes": {
            "task": "app.tasks.agent_tasks.agent_publish_terminal_run_events",
            "schedule": 300.0,
        },
        # Deterministic, free pre-screen reject catch-up. Unlike the cohort
        # tick above (which skips budget-paused roles), this culls already
        # pre-screened, below-threshold candidates on every role — agent off
        # and paused included — so the obvious-no backlog never strands open.
        # No LLM spend;
        # just re-dispatches the idempotent auto-reject task. Bounded per run.
        "pre-screen-reject-sweep-every-30-minutes": {
            "task": "app.tasks.agent_tasks.pre_screen_reject_sweep",
            "schedule": 1800.0,
        },
        # Agent daily review (legacy): kept for the proactive once-a-day
        # sweep that surfaces idle candidates / stale scores / etc. The
        # cohort-tick task above handles the bulk of work; daily review
        # remains as a wider triage pass.
        "agent-daily-review-sweep": {
            "task": "app.tasks.agent_tasks.agent_daily_review_sweep",
            # Fixed 04:30 UTC (08:30 Dubai). Unlike a 24-hour interval, this
            # cannot be indefinitely reset/skipped by routine beat restarts.
            "schedule": crontab(hour=4, minute=30),
        },
        # Nightly DecisionPolicy retune. Aggregates explicit feedback +
        # silent overrides + manual recruiter actions over the window
        # since the last cause='feedback_retune' revision and writes a
        # new (inactive by default) policy. Auto-apply is per-org via
        # workspace_settings.decision_policy_auto_apply.
        "decision-policy-nightly-fit": {
            "task": "app.tasks.decision_policy_tasks.nightly_policy_fit",
            "schedule": crontab(hour=2, minute=30),
        },
        "decision-policy-nightly-retune": {
            "task": "app.tasks.decision_policy_tasks.nightly_retune_sweep",
            "schedule": crontab(hour=3, minute=0),
        },
        # Nightly role_fit threshold calibration: learn the advance/reject cut
        # on the OBJECTIVE score from recruiter terminal decisions (Youden's J),
        # bias-gate it, write SHADOW proposals (proposed-for-review; auto-apply
        # is opt-in + bias-gated). Fixed-time crontab (NOT a float interval —
        # see the reconciliation note above for why intervals silently die).
        # Runs after score-terminal-for-calibration (03:45) so fresh raw scores
        # are present.
        "threshold-calibration-nightly": {
            "task": "app.tasks.threshold_calibration_tasks.calibrate_thresholds_sweep",
            "schedule": crontab(hour=4, minute=15),
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
        # NO auto re-scoring on a schedule. ``sweep_stale_scores`` and
        # ``score_terminal_for_calibration`` are deliberately NOT here:
        # both dispatch paid Anthropic scoring without a recruiter
        # action. Stale scores stay visibly stale until the recruiter
        # approves a re-evaluation (agent chat quotes the estimated
        # cost, then kicks a one-shot ``sweep_stale_scores`` itself);
        # terminal-outcome scoring for calibration is explicit-run only.
        #
        # Refit the cv_match calibrators from stored (score -> outcome)
        # pairs. Pure math over existing rows — no API spend. Same
        # scoring queue so snapshots land where apply_calibrator reads
        # them at scoring time.
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
        # Recompute the data-driven Stage-1 gate threshold per org + log the
        # divergence vs the static env threshold (SHADOW measurement — changes
        # nothing live until PRE_SCREEN_DYNAMIC_GATE_ENFORCE). Runs ~30 min after
        # the prescreen sampler so the week's fresh shadow rejects are included.
        "recalibrate-prescreen-gate-weekly": {
            "task": "app.tasks.calibration_tasks.recalibrate_prescreen_gate",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),
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
        # Push scored Workable-marketplace assessment results back to Workable.
        # No-op unless WORKABLE_PROVIDER_ENABLED is on (default off), so this is
        # inert on the live platform until the add-on is deliberately enabled.
        # Every 2 min surfaces a completed result on the candidate's Workable
        # timeline promptly.
        "flush-workable-provider-every-2-minutes": {
            "task": "app.tasks.workable_provider_tasks.flush_workable_provider",
            "schedule": 120.0,
        },
        # Bullhorn incremental event poll: drain each connected org's destructive
        # event queue on the configured cadence (BULLHORN_EVENT_POLL_SECONDS,
        # default 180s). The task is a cheap early-exit no-op unless
        # BULLHORN_ENABLED is on (default off), so it's inert on the live
        # platform until the integration is deliberately enabled. Cadence is read
        # from settings so a rate-budget adjustment (100k calls/mo default) is a
        # config change, not a code change.
        "bullhorn-event-poll": {
            "task": "app.tasks.bullhorn_tasks.bullhorn_event_poll_sweep",
            "schedule": float(settings.BULLHORN_EVENT_POLL_SECONDS),
        },
        # Durable outbox recovery for the connect-time FULL import. If the web
        # process or broker fails after credentials commit but before the sync
        # task starts, re-dispatch the same idempotent run on the next cadence.
        "bullhorn-initial-sync-recovery": {
            "task": "app.tasks.bullhorn_tasks.bullhorn_initial_sync_recovery_sweep",
            "schedule": float(settings.BULLHORN_EVENT_POLL_SECONDS),
        },
        # Bullhorn nightly reconciliation: the dateLastModified fallback sweep +
        # count-based drift check per connected org. Fixed-time crontab (NOT a
        # float interval — an interval timer is reset on every beat restart and a
        # redeploy before the tick silently skips the run; see the Anthropic
        # reconciliation note above). Same BULLHORN_ENABLED early-exit gate.
        # 03:30 UTC is off-peak and clear of the other nightly jobs.
        "bullhorn-reconcile-nightly": {
            "task": "app.tasks.bullhorn_tasks.bullhorn_reconcile_sweep",
            "schedule": crontab(hour=3, minute=30),
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
