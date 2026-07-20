from .celery_app import celery_app
from .assessment_tasks import (
    post_results_to_workable,
    cleanup_expired_assessments,
    finalize_timed_out_assessments,
    sync_workable_jobs,
    sync_starred_roles,
    sync_agent_mode_roles,
    sync_workable_daily_candidates,
    reap_stuck_workable_sync_runs,
    generate_assessment_task_for_role,
    sweep_assessment_task_provisioning,
    battle_test_generated_task,
    repair_generated_task_after_battle_failure,
)
# Eager-import the canonical email-task module so Celery registers
# send_assessment_email / send_results_email on the worker. (Taali never
# emails candidates about the job — there is no rejection-email task; the ATS
# owns candidate job communication.) Imported as a module (not by name) on
# purpose: notifications.
# tasks imports celery_app from this package, so a name-level import here would
# re-enter while that module is half-loaded and raise ImportError.
from ..components.notifications import tasks as _notification_email_tasks  # noqa: F401
# Eager-import scoring_tasks so Celery autodiscover registers the
# score_application_job + batch_score_role tasks on the worker. Without
# this, dispatched scoring jobs land in the queue but the worker drops
# them with "Received unregistered task" and silently discards.
from .scoring_tasks import (
    batch_score_role,
    recover_stuck_score_jobs,
    score_application_job,
)
# Incomplete rubric rows are a durable scoring outbox. Eager import both the
# direct worker and Beat sweep or Celery silently drops their task names.
from .rubric_retry_tasks import (
    retry_incomplete_rubric_scoring,
    sweep_incomplete_rubric_scoring,
)
# Queue-specific Beat canaries. Production activation requires fresh proof for
# both the default and scoring queues, so this task must be registered on every
# worker deployment just like the scoring tasks themselves.
from .health_tasks import (
    queue_worker_heartbeat,
    release_stale_usage_credit_reservations,
)
from .sister_role_tasks import (
    recover_sister_role_evaluations,
    related_role_agent_cycle,
    score_sister_evaluation,
    score_sister_role,
)
# Eager-import corroboration_tasks so the worker registers the async
# (shortlist-gated) graph + GitHub enrichment job — same unregistered-drop
# trap as scoring_tasks if skipped.
from .corroboration_tasks import enrich_corroboration_job  # noqa: F401
# Eager-import automation_tasks so Celery registers the event-driven
# auto-tasks (interview focus and auto-reject pre-screen). Skipping this
# would leave them unregistered and silently dropped — same trap as
# scoring_tasks above.
from .automation_tasks import (
    generate_role_interview_focus,
    parse_application_cv_sections,
    run_application_auto_reject,
)
# ATS sync writes application-created work to a transactional outbox. Eager
# import its post-commit dispatcher + Beat recovery task so workers never drop
# either task as unregistered.
from .application_ingest_tasks import (
    dispatch_application_cv_parse_outbox,
    dispatch_application_created_outbox,
    sweep_application_cv_parse_outbox,
    sweep_application_created_outbox,
)
# Eager-import anthropic_batch_tasks so the worker registers the Message
# Batches submit/poll beat tasks — same unregistered-drop trap as above.
from .anthropic_batch_tasks import (
    poll_cv_parse_batches,
    submit_cv_parse_batches,
)
# Eager-import workable_tasks so the worker registers the sync runner AND the
# disqualify-retry task. Without this, the retry enqueued from the reject path
# (on a transient Workable 429) would NotRegistered on the worker and drop —
# leaving Tali 'rejected' but Workable still active. Same trap as above.
from .workable_tasks import (
    run_workable_sync_run_task,
    retry_workable_disqualify_task,
    run_workable_op_task,
    recover_dispatching_workable_ops,
)
# Eager-import bullhorn_tasks so the worker registers the Bullhorn full-sync
# runner AND the two incremental beat sweeps (event poll + nightly reconcile).
# Same eager-import trap as above: a task enqueued/scheduled without this would
# NotRegistered on the worker and drop silently — and beat references the two
# sweep task names. Gated inside each task/runner (cheap no-op when
# BULLHORN_ENABLED is off or the org isn't connected).
from .bullhorn_tasks import (  # noqa: F401
    bullhorn_event_poll_sweep,
    bullhorn_initial_sync_recovery_sweep,
    bullhorn_reconcile_sweep,
    run_bullhorn_sync_run_task,
)
# Eager-import reconciliation_tasks so the daily Anthropic billing
# reconciliation beat task lands in the worker registry. Same trap as
# the imports above — beat will fire ``reconcile_anthropic_usage`` on
# schedule, but without this import the worker rejects it as
# unregistered and drops the run.
from .reconciliation_tasks import reconcile_anthropic_usage
# Eager-import agent_tasks so the autonomous-agent task names land in
# the worker registry. Without this, every agent path — manual-run API,
# the daily-review sweep, and the cohort-tick beat — silently
# NotRegistered's on the worker and the agent never runs.
from .agent_tasks import (
    agent_manual_run,
    agent_daily_review_sweep,
    agent_daily_review_role,
    agent_cohort_tick_sweep,
    agent_cohort_tick_role,
    agent_recovery_sweep,
    pre_screen_reject_sweep,
    agent_expire_stuck_runs,
    agent_publish_terminal_run_events,
    agent_expire_stale_decisions,
)
# Eager-import agent_chat_tasks so the worker registers the post-re-screen
# impact report AND the per-message turn runner. The web send-message route
# enqueues run_agent_chat_turn; the constraint-edit chat tool enqueues
# report_rescreen_impact. Without this import the worker NotRegistered's them
# and the agent's reply / the "re-screen complete" follow-up never posts.
from .agent_chat_tasks import (
    bulk_agent_message,
    report_rescreen_impact,
    run_agent_chat_turn,
)
# Eager-import decision_policy_tasks for the nightly retune beat. Same
# trap as above — the beat schedule references this task name, but
# without the import the worker drops the run.
from .decision_policy_tasks import nightly_retune_sweep
# Eager-import calibration_tasks for the nightly model-refinement beats
# (terminal-scoring + recalibration). Same trap — beat references these task
# names, but without this import the worker NotRegistered's them and drops the
# runs. (autodiscover_tasks(["app.tasks"]) does NOT cover these — it looks for
# an app.tasks.tasks module, which doesn't exist.)
from .calibration_tasks import (
    recalibrate_cv_match,
    recalibrate_prescreen_gate,
    sample_prescreen_for_calibration,
    score_terminal_for_calibration,
)
# Eager-import the threshold-calibration beat (same NotRegistered trap).
from .threshold_calibration_tasks import calibrate_thresholds_sweep
# Eager-import decision_tasks so the worker registers the deferred
# decision side-effects task. The approve / override / bulk-approve routes
# enqueue it after commit; without this import the worker NotRegistered's it
# and the Workable writeback + graph episode silently never run.
from .decision_tasks import apply_decision_side_effects
# Eager-import graph_outbox_tasks so the worker registers the durable
# episode-outbox drain. The beat schedule references this task name; without
# the import the worker NotRegistered's it and the irreplaceable realised-
# outcome episodes never reach Graphiti. Same trap as the imports above.
from .graph_outbox_tasks import drain_graph_episode_outbox
# Eager-import brain_feed_tasks so the worker registers the outbound
# mainspring brain-feed flush. The beat schedule references this task name;
# without the import the worker NotRegistered's it and the (flag-gated) feed
# never ships. Same trap as the imports above. The task itself is a no-op
# unless MAINSPRING_BRAIN_FEED_ENABLED is set.
from .brain_feed_tasks import flush_brain_feed
# Eager-import graph_ingest_tasks so the worker registers the candidate-graph
# ingestion tasks. The candidate_graph SQLAlchemy listeners enqueue these on
# every Candidate / Interview / Event write; without this import the worker
# NotRegistered's them and candidate/interview/pipeline episodes silently
# never reach Graphiti. Same trap as the imports above.
from .graph_ingest_tasks import (
    sync_candidate_to_graph,
    sync_event_to_graph,
    sync_interview_to_graph,
)
# Eager-import workable_provider_tasks so the worker registers the result-push
# flush. The beat schedule references this task name; without the import the
# worker NotRegistered's it. No-op unless WORKABLE_PROVIDER_ENABLED is set.
from .workable_provider_tasks import flush_workable_provider
# Eager-import outreach_tasks so the worker registers the campaign draft +
# send tasks. The /outreach/campaigns generate + send routes enqueue these;
# without this import the worker NotRegistered's them and drafts never get
# written / approved messages never send. Same trap as the imports above.
from .outreach_tasks import (
    generate_campaign_drafts,
    send_campaign_messages,
)
# Talent-pool re-score is dispatched by an API route rather than Beat. The
# worker still has to eager-import its module: Celery autodiscovery does not
# traverse this package layout, so otherwise accepted jobs are dropped as an
# unregistered task.
from .pool_rescore_tasks import rescore_pool_against_requirement

__all__ = [
    "celery_app",
    "post_results_to_workable",
    "cleanup_expired_assessments",
    "sync_workable_jobs",
    "sync_starred_roles",
    "sync_agent_mode_roles",
    "sync_workable_daily_candidates",
    "reap_stuck_workable_sync_runs",
    "generate_assessment_task_for_role",
    "sweep_assessment_task_provisioning",
    "battle_test_generated_task",
    "repair_generated_task_after_battle_failure",
    "score_application_job",
    "batch_score_role",
    "recover_stuck_score_jobs",
    "retry_incomplete_rubric_scoring",
    "sweep_incomplete_rubric_scoring",
    "queue_worker_heartbeat",
    "release_stale_usage_credit_reservations",
    "related_role_agent_cycle",
    "rescore_pool_against_requirement",
    "generate_role_interview_focus",
    "parse_application_cv_sections",
    "run_application_auto_reject",
    "dispatch_application_cv_parse_outbox",
    "dispatch_application_created_outbox",
    "sweep_application_cv_parse_outbox",
    "sweep_application_created_outbox",
    "submit_cv_parse_batches",
    "poll_cv_parse_batches",
    "run_workable_sync_run_task",
    "retry_workable_disqualify_task",
    "run_workable_op_task",
    "recover_dispatching_workable_ops",
    "run_bullhorn_sync_run_task",
    "bullhorn_event_poll_sweep",
    "bullhorn_initial_sync_recovery_sweep",
    "bullhorn_reconcile_sweep",
    "reconcile_anthropic_usage",
    "agent_manual_run",
    "agent_daily_review_sweep",
    "agent_daily_review_role",
    "agent_cohort_tick_sweep",
    "agent_cohort_tick_role",
    "agent_recovery_sweep",
    "pre_screen_reject_sweep",
    "agent_expire_stuck_runs",
    "agent_publish_terminal_run_events",
    "agent_expire_stale_decisions",
    "report_rescreen_impact",
    "nightly_retune_sweep",
    "score_terminal_for_calibration",
    "sample_prescreen_for_calibration",
    "recalibrate_cv_match",
    "recalibrate_prescreen_gate",
    "apply_decision_side_effects",
    "drain_graph_episode_outbox",
    "flush_brain_feed",
    "sync_candidate_to_graph",
    "sync_interview_to_graph",
    "sync_event_to_graph",
    "flush_workable_provider",
    "generate_campaign_drafts",
    "send_campaign_messages",
]
