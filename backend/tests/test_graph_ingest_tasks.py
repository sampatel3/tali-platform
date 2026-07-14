"""graph_ingest_tasks move candidate / interview / event graph sync off the
web request path onto the Celery worker pool (the in-thread daemon listeners
used to starve the web service). These guard that each task loads its row,
respects the candidate cost-gate, and threads the same per-org / per-candidate
billing attribution into the sync that the old in-thread path passed — so
graph-sync spend still lands on the right org's usage_event, not org=NULL.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tasks import graph_ingest_tasks as git


def _fake_session(entity):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = entity
    return db


def _run(task, *args):
    # Execute the task body synchronously (no broker) and return its value.
    return task.apply(args=list(args)).get()


def test_sync_candidate_passes_billing_when_gate_open():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    captured: dict = {}

    # Keep the fake's keyword signature aligned with the real sync function so
    # this test catches callers that invent unsupported kwargs. The previous
    # **kwargs fake masked a production TypeError from ``bill_candidate_id``.
    def _fake_sync(candidate, *, db=None, bill_organization_id=None):
        captured.update(
            candidate=candidate,
            db=db,
            bill_organization_id=bill_organization_id,
        )

    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.should_sync_candidate_to_graph", return_value=True
    ), patch(
        "app.candidate_graph.sync.sync_candidate",
        side_effect=_fake_sync,
    ):
        res = _run(git.sync_candidate_to_graph, 7)
    assert res["status"] == "ok"
    assert captured["candidate"] is cand
    assert captured["db"] is not None
    assert captured["bill_organization_id"] == 42


def test_sync_candidate_skips_below_cost_gate():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    calls = {"n": 0}
    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.should_sync_candidate_to_graph", return_value=False
    ), patch(
        "app.candidate_graph.sync.sync_candidate",
        side_effect=lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    ):
        res = _run(git.sync_candidate_to_graph, 7)
    assert res["status"] == "skipped"
    assert calls["n"] == 0  # gate closed → no expensive sync


def test_sync_interview_threads_org():
    iv = MagicMock()
    iv.id = 3
    iv.organization_id = 55
    captured: dict = {}
    with patch.object(git, "SessionLocal", return_value=_fake_session(iv)), patch(
        "app.candidate_graph.sync.sync_interview",
        side_effect=lambda i, **k: captured.update(k),
    ):
        res = _run(git.sync_interview_to_graph, 3)
    assert res["status"] == "ok"
    assert captured["bill_organization_id"] == 55


def test_sync_event_threads_org():
    ev = MagicMock()
    ev.id = 9
    ev.organization_id = 99
    captured: dict = {}
    with patch.object(git, "SessionLocal", return_value=_fake_session(ev)), patch(
        "app.candidate_graph.sync.sync_event",
        side_effect=lambda e, **k: captured.update(k),
    ):
        res = _run(git.sync_event_to_graph, 9)
    assert res["status"] == "ok"
    assert captured["bill_organization_id"] == 99
