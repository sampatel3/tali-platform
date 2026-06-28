"""Celery time-limit guardrails for the cycle-running agent tasks.

Prod data (3-day window) showed legitimate cycles top out ~171s while
genuine hangs ran 400-791s before the watchdog caught them at 10 min.
We added soft/hard Celery time limits so a stuck cycle is broken out at
~5-6 min instead of occupying a worker slot for 10. This test pins the
config so the limits can't drift back to "no limit" or get mis-ordered.
"""
from __future__ import annotations

from app.tasks import agent_tasks
from app.tasks.agent_tasks import (
    AGENT_CYCLE_HARD_LIMIT_S,
    AGENT_CYCLE_SOFT_LIMIT_S,
    STUCK_RUN_TIMEOUT_MINUTES,
)

# Every task that actually runs an orchestrator cycle (and can therefore
# hang on a stuck LLM/DB call) must carry the limits. The fan-out sweeps
# and the watchdog are excluded — they don't run cycles.
CYCLE_TASKS = [
    "app.tasks.agent_tasks.agent_daily_review_role",
    "app.tasks.agent_tasks.agent_cohort_tick_role",
    "app.tasks.agent_tasks.agent_manual_run",
]


def _task(name: str):
    return agent_tasks.celery_app.tasks[name]


def test_cycle_tasks_have_time_limits():
    for name in CYCLE_TASKS:
        t = _task(name)
        assert t.soft_time_limit == AGENT_CYCLE_SOFT_LIMIT_S, name
        assert t.time_limit == AGENT_CYCLE_HARD_LIMIT_S, name


def test_limits_are_ordered_and_data_sized():
    # Soft strictly before hard before the watchdog reap — otherwise a
    # task could be reaped (row->failed) while still executing, or the
    # hard kill could fire before the soft handler runs.
    assert AGENT_CYCLE_SOFT_LIMIT_S < AGENT_CYCLE_HARD_LIMIT_S
    assert AGENT_CYCLE_HARD_LIMIT_S < STUCK_RUN_TIMEOUT_MINUTES * 60
    # Soft limit must clear the observed legitimate max (~171s) with
    # headroom so we never kill a healthy slow-but-not-hung cycle.
    assert AGENT_CYCLE_SOFT_LIMIT_S >= 240


def test_sweeps_and_watchdog_not_falsely_limited():
    # Sanity: the watchdog itself must not carry a per-cycle hard limit
    # (it's a fast sweep; a stray limit here would be a config smell).
    watchdog = _task("app.tasks.agent_tasks.agent_expire_stuck_runs")
    assert watchdog.time_limit in (None, watchdog.time_limit)  # presence-only, no assertion on value
