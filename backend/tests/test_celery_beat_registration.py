"""Every beat-scheduled task must actually be registered on the worker.

Celery's ``autodiscover_tasks`` is a no-op for this layout, so a task only
lands in the registry if its module is *eager-imported* from
``app/tasks/__init__.py``. A beat entry whose ``task`` name was never imported
doesn't error at deploy — beat happily fires it, the worker logs
``Received unregistered task`` and **drops the run silently**. That trap has
bitten scoring, agent ticks, reconciliation, and the graph-episode drain (see
the eager-import comments in ``app/tasks/__init__.py``), and it's exactly what
would keep the outbound mainspring brain feed (``flush_brain_feed``) from ever
shipping.

This gate closes the loop: importing the task package (worker parity) and
asserting every ``beat_schedule`` entry — and every routed task — resolves to a
registered task. Add a beat entry without the matching eager import and this
fails locally instead of silently in prod.
"""

from __future__ import annotations

import app.tasks as tasks_pkg

celery_app = tasks_pkg.celery_app


def _scheduled_task_names() -> dict[str, str]:
    return {
        name: entry["task"]
        for name, entry in (celery_app.conf.beat_schedule or {}).items()
    }


def test_every_beat_scheduled_task_is_registered():
    missing = {
        name: task
        for name, task in _scheduled_task_names().items()
        if task not in celery_app.tasks
    }
    assert not missing, (
        "Beat-scheduled tasks not in the worker registry — eager-import them in "
        "app/tasks/__init__.py or beat fires them and the worker drops them as "
        f"unregistered: {missing}"
    )


def test_every_routed_task_is_registered():
    routes = celery_app.conf.task_routes or {}
    missing = [task for task in routes if task not in celery_app.tasks]
    assert not missing, (
        "Routed tasks not in the worker registry (same eager-import trap): "
        f"{missing}"
    )


def test_pool_rescore_endpoint_task_is_registered():
    # This task is API-dispatched rather than routed/scheduled, so the generic
    # Beat and route-table gates above cannot catch a missing eager import.
    assert "rescore_pool_against_requirement" in celery_app.tasks


def test_flush_brain_feed_is_scheduled_and_registered():
    # Explicit guard for the outbound brain feed: if its flush task isn't both
    # on the beat schedule and registered, the feed never ships.
    task_name = "app.tasks.brain_feed_tasks.flush_brain_feed"
    assert task_name in celery_app.tasks
    assert task_name in _scheduled_task_names().values()


def test_assessment_task_provisioning_recovery_is_scheduled_and_registered():
    task_name = "app.tasks.assessment_tasks.sweep_assessment_task_provisioning"
    assert task_name in celery_app.tasks
    assert task_name in _scheduled_task_names().values()


def test_assessment_invite_provider_recovery_is_scheduled_and_registered():
    task_name = (
        "app.components.notifications.tasks.sweep_retryable_assessment_invites"
    )
    assert task_name in celery_app.tasks
    assert task_name in _scheduled_task_names().values()


def test_incomplete_rubric_recovery_is_scheduled_registered_and_routed():
    retry_task = "app.tasks.rubric_retry_tasks.retry_incomplete_rubric_scoring"
    sweep_task = "app.tasks.rubric_retry_tasks.sweep_incomplete_rubric_scoring"
    scheduled = _scheduled_task_names().values()
    assert retry_task in celery_app.tasks
    assert sweep_task in celery_app.tasks
    assert sweep_task in scheduled
    assert celery_app.conf.task_routes[retry_task]["queue"] == "scoring"
    assert celery_app.conf.task_routes[sweep_task]["queue"] == "scoring"


def test_stale_usage_credit_hold_recovery_is_scheduled_and_registered():
    task_name = "app.tasks.health_tasks.release_stale_usage_credit_reservations"
    assert task_name in celery_app.tasks
    assert task_name in _scheduled_task_names().values()


def test_bullhorn_incremental_sweeps_are_scheduled_and_registered():
    # Explicit guard for the Bullhorn incremental layer: the event-poll sweep
    # (destructive event-queue drain) and the nightly reconcile sweep must each
    # be BOTH registered and on the beat schedule, or the incremental sync + the
    # drift check never fire. Both tasks are cheap no-ops when BULLHORN_ENABLED
    # is off, so scheduling them on the live platform is safe.
    scheduled = _scheduled_task_names().values()
    for task_name in (
        "app.tasks.bullhorn_tasks.bullhorn_event_poll_sweep",
        "app.tasks.bullhorn_tasks.bullhorn_initial_sync_recovery_sweep",
        "app.tasks.bullhorn_tasks.bullhorn_reconcile_sweep",
    ):
        assert task_name in celery_app.tasks, f"{task_name} not registered"
        assert task_name in scheduled, f"{task_name} not on the beat schedule"


def test_statistical_policy_fit_precedes_nightly_retune():
    scheduled = _scheduled_task_names()
    assert scheduled["decision-policy-nightly-fit"] == (
        "app.tasks.decision_policy_tasks.nightly_policy_fit"
    )
    assert scheduled["decision-policy-nightly-retune"] == (
        "app.tasks.decision_policy_tasks.nightly_retune_sweep"
    )
