"""Operational result aggregation for Bullhorn Beat sweeps."""

from __future__ import annotations

import pytest

from app.tasks import bullhorn_tasks


@pytest.mark.parametrize(
    ("task", "runner_name", "success_key"),
    [
        (bullhorn_tasks.bullhorn_event_poll_sweep, "execute_bullhorn_event_poll", "polled"),
        (bullhorn_tasks.bullhorn_reconcile_sweep, "execute_bullhorn_reconcile", "reconciled"),
    ],
)
def test_bullhorn_sweep_surfaces_retry_pending_as_degraded(
    monkeypatch,
    task,
    runner_name,
    success_key,
):
    monkeypatch.setattr(bullhorn_tasks.settings, "BULLHORN_ENABLED", True)
    monkeypatch.setattr(bullhorn_tasks, "_connected_org_ids", lambda: [1, 2, 3])
    outcomes = iter(
        [
            {"status": "ok"},
            {"status": "retry_pending", "reason": "lease_lost"},
            {"status": "skipped", "reason": "locked"},
        ]
    )
    monkeypatch.setattr(
        bullhorn_tasks,
        runner_name,
        lambda *, org_id: next(outcomes),
    )

    result = task.run()

    assert result["status"] == "degraded"
    assert result[success_key] == 1
    assert result["retry_pending"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0
