"""Commit-boundary behavior for candidate graph listener dispatch."""

from unittest.mock import patch

from app.candidate_graph import listeners


def test_deferred_graph_work_coalesces_and_dispatches_after_commit(db):
    db.info[listeners._PENDING_KEY] = {
        ("candidate", 7),
        ("candidate", 7),
        ("interview", 9),
    }

    with patch.object(listeners, "_enqueue_candidate_sync") as candidate, patch.object(
        listeners, "_enqueue_interview_sync"
    ) as interview:
        listeners._dispatch_after_commit(db)

    candidate.assert_called_once_with(7)
    interview.assert_called_once_with(9)
    assert listeners._PENDING_KEY not in db.info


def test_rollback_discards_graph_work_without_publishing(db):
    db.info[listeners._PENDING_KEY] = {("candidate", 7)}

    with patch.object(listeners, "_enqueue_candidate_sync") as candidate:
        listeners._discard_after_rollback(db)

    candidate.assert_not_called()
    assert listeners._PENDING_KEY not in db.info
