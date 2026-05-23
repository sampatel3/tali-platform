from .celery_app import celery_app
from .assessment_tasks import (
    send_assessment_email,
    send_candidate_feedback_ready_email,
    send_results_email,
    post_results_to_workable,
    cleanup_expired_assessments,
    sync_workable_jobs,
    sync_starred_roles,
    sync_agent_mode_roles,
    sync_workable_daily_candidates,
    reap_stuck_workable_sync_runs,
)
# Eager-import scoring_tasks so Celery autodiscover registers the
# score_application_job + batch_score_role tasks on the worker. Without
# this, dispatched scoring jobs land in the queue but the worker drops
# them with "Received unregistered task" and silently discards.
from .scoring_tasks import (
    batch_score_role,
    score_application_job,
)
# Eager-import automation_tasks so Celery registers the event-driven
# auto-tasks (interview focus, interview pack regen, auto-reject pre-
# screen). Skipping this would leave them unregistered and silently
# dropped — same trap as scoring_tasks above.
from .automation_tasks import (
    generate_application_interview_pack,
    generate_role_interview_focus,
    run_application_auto_reject,
)
from .workable_tasks import run_workable_sync_run_task
# Eager-import reconciliation_tasks so the daily Anthropic billing
# reconciliation beat task lands in the worker registry. Same trap as
# the imports above — beat will fire ``reconcile_anthropic_usage`` on
# schedule, but without this import the worker rejects it as
# unregistered and drops the run.
from .reconciliation_tasks import reconcile_anthropic_usage
# Eager-import agent_tasks so the autonomous-agent task names land in
# the worker registry. Without this, every agent path — manual-run API,
# event triggers, the daily-review sweep, and the cohort-tick beat —
# silently NotRegistered's on the worker and the agent never runs.
from .agent_tasks import (
    agent_react_to_event,
    agent_manual_run,
    agent_daily_review_sweep,
    agent_daily_review_role,
    agent_cohort_tick_sweep,
    agent_cohort_tick_role,
    agent_expire_stuck_runs,
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
    score_terminal_for_calibration,
)

__all__ = [
    "celery_app",
    "send_assessment_email",
    "send_candidate_feedback_ready_email",
    "send_results_email",
    "post_results_to_workable",
    "cleanup_expired_assessments",
    "sync_workable_jobs",
    "sync_starred_roles",
    "sync_agent_mode_roles",
    "sync_workable_daily_candidates",
    "reap_stuck_workable_sync_runs",
    "score_application_job",
    "batch_score_role",
    "generate_role_interview_focus",
    "generate_application_interview_pack",
    "run_application_auto_reject",
    "run_workable_sync_run_task",
    "reconcile_anthropic_usage",
    "agent_react_to_event",
    "agent_manual_run",
    "agent_daily_review_sweep",
    "agent_daily_review_role",
    "agent_cohort_tick_sweep",
    "agent_cohort_tick_role",
    "agent_expire_stuck_runs",
    "nightly_retune_sweep",
    "score_terminal_for_calibration",
    "recalibrate_cv_match",
]
