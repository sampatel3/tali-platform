"""Task-result boundaries expose stable codes, never exception messages."""

from __future__ import annotations

from unittest.mock import patch


_SECRET = "sk-live-task-boundary-secret"


def test_brain_feed_exhaustion_redacts_exception_text():
    from app.tasks.brain_feed_tasks import flush_brain_feed

    with patch.object(flush_brain_feed, "max_retries", 0), patch(
        "app.brain_feed.sweep.sweep_and_enqueue",
        side_effect=RuntimeError(_SECRET),
    ):
        result = flush_brain_feed.run()

    assert result == {"status": "error", "error": "brain_feed_flush_failed"}
    assert _SECRET not in repr(result)


def test_workable_provider_exhaustion_redacts_exception_text(monkeypatch):
    from app.platform.config import settings
    from app.tasks.workable_provider_tasks import flush_workable_provider

    monkeypatch.setattr(settings, "WORKABLE_PROVIDER_ENABLED", True)
    with patch.object(flush_workable_provider, "max_retries", 0), patch(
        "app.domains.workable_provider.service.enqueue_completed_results",
        side_effect=RuntimeError(_SECRET),
    ):
        result = flush_workable_provider.run()

    assert result == {
        "status": "error",
        "error": "workable_provider_flush_failed",
    }
    assert _SECRET not in repr(result)


def test_graph_outbox_exhaustion_redacts_exception_text():
    from app.tasks.graph_outbox_tasks import drain_graph_episode_outbox

    with patch.object(drain_graph_episode_outbox, "max_retries", 0), patch(
        "app.candidate_graph.episode_outbox.drain",
        side_effect=RuntimeError(_SECRET),
    ):
        result = drain_graph_episode_outbox.run(batch_size=1)

    assert result == {"status": "error", "error": "graph_outbox_drain_failed"}
    assert _SECRET not in repr(result)


def test_reconciliation_result_redacts_exception_text():
    from app.tasks import reconciliation_tasks

    with patch.object(
        reconciliation_tasks,
        "reconcile_recent",
        side_effect=RuntimeError(_SECRET),
    ):
        result = reconciliation_tasks.reconcile_anthropic_usage.run(days=1)

    assert result == {"error": "anthropic_reconciliation_failed"}
    assert _SECRET not in repr(result)
