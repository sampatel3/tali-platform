from .celery_app import celery_app
from .assessment_tasks import (
    send_assessment_email,
    send_candidate_feedback_ready_email,
    send_results_email,
    post_results_to_workable,
    cleanup_expired_assessments,
    sync_workable_orgs,
)
# Eager-import scoring_tasks so Celery autodiscover registers the
# score_application_job + batch_score_role tasks on the worker. Without
# this, dispatched scoring jobs land in the queue but the worker drops
# them with "Received unregistered task" and silently discards.
from .scoring_tasks import (
    batch_score_role,
    score_application_job,
)
from .workable_tasks import run_workable_sync_run_task

__all__ = [
    "celery_app",
    "send_assessment_email",
    "send_candidate_feedback_ready_email",
    "send_results_email",
    "post_results_to_workable",
    "cleanup_expired_assessments",
    "sync_workable_orgs",
    "score_application_job",
    "batch_score_role",
    "run_workable_sync_run_task",
]
