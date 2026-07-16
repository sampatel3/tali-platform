"""graph_ingest_tasks move candidate / interview / event graph sync off the
web request path onto the Celery worker pool (the in-thread daemon listeners
used to starve the web service). These guard that each task loads its row,
respects the candidate cost-gate, and threads the same per-org / per-candidate
billing attribution into the sync that the old in-thread path passed — so
graph-sync spend still lands on the right org's usage_event, not org=NULL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.organization import Organization
from app.models.role import Role
from app.tasks import graph_ingest_tasks as git


def _fake_session(entity):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = entity
    return db


def _run(task, *args):
    # Execute the task body synchronously (no broker) and return its value.
    return task.apply(args=list(args)).get()


def test_listener_role_gate_reads_current_on_pause_and_off_state(db):
    org = Organization(name="Graph gate", slug=f"graph-gate-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Graph role",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.commit()

    assert git._listener_graph_role_is_active(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
    ) is True

    role.agent_paused_at = datetime.now(timezone.utc)
    db.commit()
    assert git._listener_graph_role_is_active(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
    ) is False

    role.agent_paused_at = None
    role.agentic_mode_enabled = False
    db.commit()
    assert git._listener_graph_role_is_active(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
    ) is False


def test_sync_candidate_passes_billing_when_gate_open():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    captured: dict = {}

    # Keep the fake's keyword signature aligned with the real sync function so
    # this test catches callers that invent unsupported kwargs. The previous
    # **kwargs fake masked a production TypeError from ``bill_candidate_id``.
    def _fake_sync(
        candidate,
        *,
        db=None,
        include_cv_text=True,
        bill_organization_id=None,
        bill_role_id=None,
        bill_user_id=None,
        force_resync=False,
        require_role_admission=False,
        raise_on_error=False,
    ):
        captured.update(
            candidate=candidate,
            db=db,
            bill_organization_id=bill_organization_id,
            bill_role_id=bill_role_id,
            require_role_admission=require_role_admission,
            raise_on_error=raise_on_error,
        )

    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.billing_role_id_for_candidate", return_value=9
    ), patch(
        "app.candidate_graph.sync.sync_candidate",
        autospec=True,
        side_effect=_fake_sync,
    ):
        res = _run(git.sync_candidate_to_graph, 7)
    assert res["status"] == "ok"
    assert captured["candidate"] is cand
    assert captured["db"] is not None
    assert captured["bill_organization_id"] == 42
    assert captured["bill_role_id"] == 9
    assert captured["require_role_admission"] is True
    assert captured["raise_on_error"] is True
    # sync_candidate derives candidate attribution from ``candidate.id``;
    # passing the old, nonexistent bill_candidate_id keyword crashed the real
    # Celery task even though an unconstrained MagicMock accepted it.
    assert "bill_candidate_id" not in captured


def test_sync_candidate_skips_below_cost_gate():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    calls = {"n": 0}
    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.billing_role_id_for_candidate", return_value=None
    ), patch(
        "app.candidate_graph.sync.sync_candidate",
        side_effect=lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    ):
        res = _run(git.sync_candidate_to_graph, 7)
    assert res["status"] == "skipped"
    assert calls["n"] == 0  # gate closed → no expensive sync


def test_sync_candidate_skips_when_role_was_turned_off_after_enqueue():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.billing_role_id_for_candidate", return_value=9
    ), patch.object(
        git, "_listener_graph_role_is_active", return_value=False
    ), patch(
        "app.candidate_graph.sync.sync_candidate"
    ) as sync_candidate:
        res = _run(git.sync_candidate_to_graph, 7)

    assert res == {"status": "skipped", "reason": "role_not_running", "id": 7}
    sync_candidate.assert_not_called()


def test_sync_interview_threads_org():
    iv = MagicMock()
    iv.id = 3
    iv.organization_id = 55
    iv.application.role_id = 12
    captured: dict = {}
    with patch.object(git, "SessionLocal", return_value=_fake_session(iv)), patch(
        "app.candidate_graph.sync.sync_interview",
        side_effect=lambda i, **k: captured.update(k),
    ):
        res = _run(git.sync_interview_to_graph, 3)
    assert res["status"] == "ok"
    assert captured["bill_organization_id"] == 55
    assert captured["bill_role_id"] == 12
    assert captured["require_role_admission"] is True
    assert captured["raise_on_error"] is True


def test_sync_event_threads_org():
    ev = MagicMock()
    ev.id = 9
    ev.organization_id = 99
    ev.application.role_id = 13
    captured: dict = {}
    with patch.object(git, "SessionLocal", return_value=_fake_session(ev)), patch(
        "app.candidate_graph.sync.sync_event",
        side_effect=lambda e, **k: captured.update(k),
    ):
        res = _run(git.sync_event_to_graph, 9)
    assert res["status"] == "ok"
    assert captured["bill_organization_id"] == 99
    assert captured["bill_role_id"] == 13
    assert captured["require_role_admission"] is True
    assert captured["raise_on_error"] is True


def test_sync_interview_skips_when_role_is_paused():
    iv = MagicMock()
    iv.id = 3
    iv.organization_id = 55
    iv.application.role_id = 12
    with patch.object(git, "SessionLocal", return_value=_fake_session(iv)), patch.object(
        git, "_listener_graph_role_is_active", return_value=False
    ), patch("app.candidate_graph.sync.sync_interview") as sync_interview:
        res = _run(git.sync_interview_to_graph, 3)

    assert res == {"status": "skipped", "reason": "role_not_running", "id": 3}
    sync_interview.assert_not_called()


def test_sync_event_skips_when_role_is_paused():
    ev = MagicMock()
    ev.id = 9
    ev.organization_id = 99
    ev.application.role_id = 13
    with patch.object(git, "SessionLocal", return_value=_fake_session(ev)), patch.object(
        git, "_listener_graph_role_is_active", return_value=False
    ), patch("app.candidate_graph.sync.sync_event") as sync_event:
        res = _run(git.sync_event_to_graph, 9)

    assert res == {"status": "skipped", "reason": "role_not_running", "id": 9}
    sync_event.assert_not_called()


def test_provider_failure_is_retried_with_terminal_cap():
    cand = MagicMock()
    cand.id = 7
    cand.organization_id = 42
    provider_error = RuntimeError("voyage unavailable")
    with patch.object(git, "SessionLocal", return_value=_fake_session(cand)), patch(
        "app.candidate_graph.sync.billing_role_id_for_candidate", return_value=9
    ), patch(
        "app.candidate_graph.sync.sync_candidate", side_effect=provider_error
    ), patch.object(
        git.sync_candidate_to_graph,
        "retry",
        side_effect=RuntimeError("retry scheduled"),
    ) as retry:
        try:
            git.sync_candidate_to_graph.run(7)
        except RuntimeError as exc:
            assert str(exc) == "retry scheduled"
        else:  # pragma: no cover
            raise AssertionError("provider failure should remain queued for retry")

    assert git.sync_candidate_to_graph.max_retries == git._PROVIDER_MAX_RETRIES
    retry.assert_called_once()
    kwargs = retry.call_args.kwargs
    assert kwargs["exc"] is provider_error
    assert kwargs["max_retries"] == git._PROVIDER_MAX_RETRIES
