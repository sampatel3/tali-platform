from .celery_app import celery_app
from .assessment_tasks import (
    send_assessment_email,
    send_candidate_feedback_ready_email,
    send_results_email,
    post_results_to_workable,
    cleanup_expired_assessments,
    sync_workable_orgs,
    sync_starred_roles,
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

__all__ = [
    "celery_app",
    "send_assessment_email",
    "send_candidate_feedback_ready_email",
    "send_results_email",
    "post_results_to_workable",
    "cleanup_expired_assessments",
    "sync_workable_orgs",
    "sync_starred_roles",
    "score_application_job",
    "batch_score_role",
    "generate_role_interview_focus",
    "generate_application_interview_pack",
    "run_application_auto_reject",
    "run_workable_sync_run_task",
    "reconcile_anthropic_usage",
]
